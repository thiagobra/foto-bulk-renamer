"""Tests for renamer.py and every preset in presets.py.

Run with:  python -m unittest test_renamer -v

These are plain unittest tests (standard library, nothing to install). They use
a throwaway temp folder, so they never touch your real photos.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from PIL import Image

import presets
import renamer
from renamer import (CleanupOptions, InsertPosition, Mode, PhotoFile,
                     RenameSettings)

_ISOLATED_APPDATA = None


def setUpModule():
    """Point the undo-log folder at a temp dir so tests stay self-contained."""
    global _ISOLATED_APPDATA
    _ISOLATED_APPDATA = tempfile.TemporaryDirectory()
    os.environ["XDG_DATA_HOME"] = _ISOLATED_APPDATA.name
    os.environ["LOCALAPPDATA"] = _ISOLATED_APPDATA.name


def tearDownModule():
    _ISOLATED_APPDATA.cleanup()


def make_photo(stem: str, ext: str = ".jpg",
               taken: str = "2026-06-12 14:22:33") -> PhotoFile:
    """A PhotoFile that does not need to exist on disk (for pure name tests)."""
    return PhotoFile(path=Path(f"/tmp/{stem}{ext}"),
                     taken_at=datetime.fromisoformat(taken),
                     size=1234)


def write_jpeg(path: Path, *, exif_date: str | None = None, colour=(70, 90, 120)):
    """Create a tiny real JPEG, optionally carrying a real EXIF capture date."""
    img = Image.new("RGB", (24, 18), colour)
    if exif_date:
        exif = img.getexif()
        exif[306] = exif_date                      # DateTime, in IFD0
        exif.get_ifd(0x8769)[36867] = exif_date    # DateTimeOriginal, Exif IFD
        img.save(path, exif=exif)
    else:
        img.save(path)


# --------------------------------------------------------------------------

class TestHelpers(unittest.TestCase):

    def test_natural_sort_beats_alphabetical(self):
        names = ["IMG_10.jpg", "IMG_9.jpg", "IMG_100.jpg"]
        self.assertEqual(sorted(names, key=renamer.natural_key),
                         ["IMG_9.jpg", "IMG_10.jpg", "IMG_100.jpg"])

    def test_camera_id_takes_last_four_digits(self):
        self.assertEqual(renamer.camera_id("IMG_20260612_142233"), "2233")
        self.assertEqual(renamer.camera_id("DSC_0007"), "0007")
        self.assertEqual(renamer.camera_id("panorama"), "rama")  # no digits


class TestTokens(unittest.TestCase):

    def setUp(self):
        self.photo = make_photo("IMG_20260612_142233")

    def expand(self, pattern, event="lakeside wedding", index=14, digits=3):
        return renamer.expand_pattern(pattern, photo=self.photo, event=event,
                                      index=index, digits=digits)

    def test_every_token(self):
        self.assertEqual(self.expand("{date}"), "2026-06-12")
        self.assertEqual(self.expand("{date8}"), "20260612")
        self.assertEqual(self.expand("{time}"), "14-22-33")
        self.assertEqual(self.expand("{event}"), "lakeside wedding")
        self.assertEqual(self.expand("{orig}"), "IMG_20260612_142233")
        self.assertEqual(self.expand("{cam}"), "2233")
        self.assertEqual(self.expand("{n}"), "014")

    def test_digits_padding(self):
        self.assertEqual(self.expand("{n}", index=7, digits=4), "0007")
        self.assertEqual(self.expand("{n}", index=1234, digits=2), "1234")

    def test_unknown_token_is_left_visible(self):
        self.assertEqual(self.expand("{nope}_{n}"), "{nope}_014")


class TestCleanupAndSafety(unittest.TestCase):

    def test_accents_spaces_case(self):
        out = renamer.apply_cleanup("Café Déjà Vu", CleanupOptions())
        self.assertEqual(out, "cafe-deja-vu")

    def test_collapse_and_trim_separators(self):
        out = renamer.apply_cleanup("_beach--trip__2026_", CleanupOptions())
        self.assertEqual(out, "beach-trip_2026")

    def test_cleanup_can_be_switched_off(self):
        opts = CleanupOptions(lowercase=False, spaces_to_hyphens=False,
                              strip_accents=False, collapse_separators=False)
        self.assertEqual(renamer.apply_cleanup("Café Trip", opts), "Café Trip")

    def test_illegal_characters_removed(self):
        self.assertEqual(renamer.sanitize_stem('a<b>c:d"e/f\\g|h?i*j'), "abcdefghij")

    def test_trailing_dots_and_spaces_removed(self):
        self.assertEqual(renamer.sanitize_stem("holiday... "), "holiday")

    def test_reserved_windows_name_is_escaped(self):
        self.assertEqual(renamer.sanitize_stem("CON"), "_CON")
        self.assertEqual(renamer.sanitize_stem("beach"), "beach")

    def test_empty_name_never_returned(self):
        self.assertEqual(renamer.sanitize_stem("///"), "unnamed")


class TestConventionCheck(unittest.TestCase):

    def test_good_iso_name(self):
        ok, msg = renamer.check_convention("2026-06-12_lakeside-wedding_014")
        self.assertTrue(ok)
        self.assertIn("ISO date-first", msg)

    def test_spaces_flagged(self):
        ok, msg = renamer.check_convention("beach trip 014")
        self.assertFalse(ok)
        self.assertIn("spaces", msg)

    def test_unpadded_sequence_flagged(self):
        ok, msg = renamer.check_convention("beach-7")
        self.assertFalse(ok)
        self.assertIn("zero-padded", msg)

    def test_non_ascii_flagged(self):
        ok, _ = renamer.check_convention("café-001")
        self.assertFalse(ok)


class TestInsertPositions(unittest.TestCase):
    """The beginning / middle / end behaviour, on a real Android-style name."""

    STEM = "IMG_20260612_142233"

    def insert(self, **kwargs):
        return renamer.insert_into_stem(self.STEM, "beach", **kwargs)

    def test_beginning(self):
        self.assertEqual(self.insert(position=InsertPosition.BEGINNING),
                         "beach_IMG_20260612_142233")

    def test_end(self):
        self.assertEqual(self.insert(position=InsertPosition.END),
                         "IMG_20260612_142233_beach")

    def test_after_first_separator(self):
        self.assertEqual(
            self.insert(position=InsertPosition.AFTER_NTH_SEPARATOR, nth=1),
            "IMG_beach_20260612_142233")

    def test_after_second_separator(self):
        self.assertEqual(
            self.insert(position=InsertPosition.AFTER_NTH_SEPARATOR, nth=2),
            "IMG_20260612_beach_142233")

    def test_missing_separator_falls_back_to_appending(self):
        out = renamer.insert_into_stem("photo", "beach",
                                       position=InsertPosition.AFTER_NTH_SEPARATOR,
                                       nth=1)
        self.assertEqual(out, "photo_beach")

    def test_at_character(self):
        self.assertEqual(self.insert(position=InsertPosition.AT_CHAR, char_index=3),
                         "IMGbeach_20260612_142233")

    def test_at_character_is_clamped(self):
        self.assertEqual(self.insert(position=InsertPosition.AT_CHAR, char_index=999),
                         "IMG_20260612_142233beach")

    def test_insert_mode_keeps_the_original_name_untouched(self):
        """Cleanup must not lowercase IMG_ when you are only inserting text."""
        settings = RenameSettings(mode=Mode.INSERT, insert_text="Beach Day",
                                  insert_position=InsertPosition.BEGINNING)
        stem = renamer.build_new_stem(make_photo("IMG_20260612_142233"),
                                      settings, 1)
        self.assertEqual(stem, "beach-day_IMG_20260612_142233")

    def test_empty_text_changes_nothing(self):
        self.assertEqual(renamer.insert_into_stem(self.STEM, "",
                                                  position=InsertPosition.BEGINNING),
                         self.STEM)


class TestFindReplace(unittest.TestCase):

    def build(self, **kwargs):
        settings = RenameSettings(mode=Mode.REPLACE, **kwargs)
        return renamer.build_new_stem(make_photo("IMG_20260612_142233"), settings, 1)

    def test_case_insensitive_by_default(self):
        self.assertEqual(self.build(find="img", replace_with="holiday"),
                         "holiday_20260612_142233")

    def test_match_case(self):
        # "img" does not match "IMG", so the name is left exactly as it was.
        self.assertEqual(self.build(find="img", replace_with="holiday",
                                    match_case=True),
                         "IMG_20260612_142233")

    def test_empty_find_is_a_no_op(self):
        self.assertEqual(self.build(find=""), "IMG_20260612_142233")

    def test_only_the_replacement_text_is_cleaned_up(self):
        # The typed text is tidied, the rest of the original name is not.
        self.assertEqual(self.build(find="IMG", replace_with="Lakeside Wedding"),
                         "lakeside-wedding_20260612_142233")


class TestPresets(unittest.TestCase):
    """Every shipped preset, against one fixed sample photo."""

    EXPECTED = {
        "date_event_n":      "2026-06-12_lakeside-wedding_014.jpg",
        "date_event_n_cam":  "2026-06-12_lakeside-wedding_014_2233.jpg",
        "date8_n":           "20260612_014.jpg",
        "date_time":         "2026-06-12_14-22-33.jpg",
        "event_n":           "lakeside-wedding_014.jpg",
        "web_slug":          "lakeside-wedding-014.jpg",
        "prefix_keep":       "lakeside-wedding_IMG_20260612_142233.jpg",
        "suffix_keep":       "IMG_20260612_142233_lakeside-wedding.jpg",
        "custom":            "2026-06-12_lakeside-wedding_014.jpg",
    }

    def test_all_presets_render_as_documented(self):
        photo = make_photo("IMG_20260612_142233", ext=".JPG")
        for preset in presets.PRESETS:
            with self.subTest(preset=preset.key):
                settings = RenameSettings(mode=preset.mode,
                                          pattern=preset.pattern,
                                          event="lakeside wedding",
                                          start=14, digits=3,
                                          cleanup=preset.cleanup)
                stem = renamer.build_new_stem(photo, settings, settings.start)
                # Extensions are always lowercased, even from a .JPG original.
                self.assertEqual(stem + photo.ext.lower(),
                                 self.EXPECTED[preset.key])

    def test_every_preset_key_is_covered(self):
        self.assertEqual({p.key for p in presets.PRESETS}, set(self.EXPECTED))

    def test_preset_examples_match_reality(self):
        """The example text in the dropdown must not drift from the pattern."""
        photo = make_photo(presets.EXAMPLE_ORIG, ext=".jpg")
        for preset in presets.PRESETS:
            if preset.key == "custom":
                continue
            with self.subTest(preset=preset.key):
                event = ("edit" if preset.key == "suffix_keep"
                         else presets.EXAMPLE_EVENT)
                settings = RenameSettings(pattern=preset.pattern, event=event,
                                          start=14, digits=3,
                                          cleanup=preset.cleanup)
                stem = renamer.build_new_stem(photo, settings, 14)
                self.assertEqual(stem + ".jpg", preset.example)


class TestDiskOperations(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def listing(self) -> list[str]:
        return sorted(p.name for p in self.dir.iterdir())

    # -- EXIF -------------------------------------------------------------

    def test_exif_date_is_preferred(self):
        path = self.dir / "IMG_0001.jpg"
        write_jpeg(path, exif_date="2026:06:12 14:22:33")
        taken, from_exif = renamer.read_taken_at(path)
        self.assertTrue(from_exif)
        self.assertEqual(taken, datetime(2026, 6, 12, 14, 22, 33))

    def test_falls_back_to_modified_time(self):
        path = self.dir / "clip.mp4"
        path.write_bytes(b"not really a video")
        os.utime(path, (1_760_000_000, 1_760_000_000))
        taken, from_exif = renamer.read_taken_at(path)
        self.assertFalse(from_exif)
        self.assertEqual(taken, datetime.fromtimestamp(1_760_000_000))

    # -- scanning and sorting ---------------------------------------------

    def test_scan_skips_unsupported_and_sorts_chronologically(self):
        write_jpeg(self.dir / "b.jpg", exif_date="2026:06:12 09:00:00")
        write_jpeg(self.dir / "a.jpg", exif_date="2026:06:12 10:00:00")
        (self.dir / "notes.txt").write_text("ignore me")
        files, skipped = renamer.scan_paths([self.dir])
        self.assertEqual(skipped, 1)
        ordered = [f.name for f in renamer.sort_files(files)]
        self.assertEqual(ordered, ["b.jpg", "a.jpg"])  # by time, not by name

    def test_scan_accepts_individual_files_and_dedupes(self):
        write_jpeg(self.dir / "a.jpg")
        files, _ = renamer.scan_paths([self.dir / "a.jpg", self.dir / "a.jpg"])
        self.assertEqual(len(files), 1)

    # -- collisions -------------------------------------------------------

    def test_existing_unrelated_file_forces_a_suffix(self):
        write_jpeg(self.dir / "IMG_0001.jpg", exif_date="2026:06:12 14:22:33")
        (self.dir / "2026-06-12_beach_001.jpg").write_bytes(b"already here")
        files, _ = renamer.scan_paths([self.dir / "IMG_0001.jpg"])
        plans = renamer.plan_renames(files, RenameSettings(event="beach"))
        self.assertEqual(plans[0].new_name, "2026-06-12_beach_001 (1).jpg")
        self.assertTrue(plans[0].conflict)

    def test_identical_targets_are_de_duplicated(self):
        for i in (1, 2):
            write_jpeg(self.dir / f"IMG_000{i}.jpg", exif_date="2026:06:12 14:22:33")
        files = renamer.sort_files(renamer.scan_paths([self.dir])[0])
        # {date}_{event} with no number: both photos want the same name.
        plans = renamer.plan_renames(
            files, RenameSettings(pattern="{date}_{event}", event="beach"))
        self.assertEqual([p.new_name for p in plans],
                         ["2026-06-12_beach.jpg", "2026-06-12_beach (1).jpg"])

    # -- applying ---------------------------------------------------------

    def test_rename_batch_and_undo_round_trip(self):
        for i in range(1, 13):
            write_jpeg(self.dir / f"IMG_{i:04d}.jpg",
                       exif_date=f"2026:06:12 14:{i:02d}:00")
        before = self.listing()

        files = renamer.sort_files(renamer.scan_paths([self.dir])[0])
        plans = renamer.plan_renames(files, RenameSettings(event="lakeside wedding"))
        result = renamer.apply_renames(plans)

        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.renamed), 12)
        self.assertIn("2026-06-12_lakeside-wedding_001.jpg", self.listing())
        self.assertIn("2026-06-12_lakeside-wedding_012.jpg", self.listing())
        self.assertIsNotNone(result.log_path)

        undone = renamer.undo_last()
        self.assertEqual(undone.errors, [])
        self.assertEqual(self.listing(), before)

    def test_swapping_two_names_is_safe(self):
        """The case a naive rename loop corrupts: a.jpg <-> b.jpg."""
        write_jpeg(self.dir / "a.jpg", colour=(255, 0, 0))
        write_jpeg(self.dir / "b.jpg", colour=(0, 0, 255))
        red = (self.dir / "a.jpg").read_bytes()
        blue = (self.dir / "b.jpg").read_bytes()

        files, _ = renamer.scan_paths([self.dir])
        by_name = {f.name: f for f in files}
        plans = [renamer.RenamePlan(by_name["a.jpg"], "b.jpg"),
                 renamer.RenamePlan(by_name["b.jpg"], "a.jpg")]
        result = renamer.apply_renames(plans)

        self.assertEqual(result.errors, [])
        self.assertEqual((self.dir / "b.jpg").read_bytes(), red)
        self.assertEqual((self.dir / "a.jpg").read_bytes(), blue)
        self.assertEqual(self.listing(), ["a.jpg", "b.jpg"])

    def test_no_temp_files_are_left_behind(self):
        for i in range(3):
            write_jpeg(self.dir / f"IMG_{i}.jpg", exif_date="2026:06:12 14:22:33")
        files = renamer.sort_files(renamer.scan_paths([self.dir])[0])
        renamer.apply_renames(renamer.plan_renames(files, RenameSettings(event="x")))
        self.assertFalse([n for n in self.listing() if renamer.TEMP_SUFFIX in n])

    def test_unchanged_names_are_not_touched(self):
        write_jpeg(self.dir / "2026-06-12_beach_001.jpg", exif_date="2026:06:12 14:22:33")
        files, _ = renamer.scan_paths([self.dir])
        plans = renamer.plan_renames(files, RenameSettings(event="beach"))
        self.assertFalse(plans[0].changed)
        result = renamer.apply_renames(plans)
        self.assertEqual(result.renamed, [])

    def test_undo_skips_files_that_moved_away(self):
        write_jpeg(self.dir / "IMG_0001.jpg", exif_date="2026:06:12 14:22:33")
        files, _ = renamer.scan_paths([self.dir])
        plans = renamer.plan_renames(files, RenameSettings(event="beach"))
        renamer.apply_renames(plans)
        (self.dir / plans[0].new_name).unlink()      # user deleted it afterwards
        undone = renamer.undo_last()
        self.assertEqual(undone.renamed, [])
        self.assertIn("skipped", undone.errors[0][1])

    def test_result_carries_absolute_paths(self):
        write_jpeg(self.dir / "IMG_0001.jpg", exif_date="2026:06:12 14:22:33")
        files, _ = renamer.scan_paths([self.dir])
        result = renamer.apply_renames(
            renamer.plan_renames(files, RenameSettings(event="beach")))
        self.assertEqual(len(result.moved), 1)
        old, new = result.moved[0]
        self.assertTrue(old.is_absolute() and new.is_absolute())
        self.assertEqual(new.name, "2026-06-12_beach_001.jpg")

    def test_two_folders_with_the_same_file_name_survive_undo(self):
        """A batch spanning two cards: both hold an IMG_0001.jpg.

        Identifying files by their bare name used to collapse the two into
        one, which corrupted the undo log and stranded the first folder.
        """
        one, two = self.dir / "one", self.dir / "two"
        one.mkdir(), two.mkdir()
        for folder in (one, two):
            write_jpeg(folder / "IMG_0001.jpg", exif_date="2026:06:12 14:22:33")

        files = renamer.sort_files(renamer.scan_paths([one, two])[0])
        result = renamer.apply_renames(
            renamer.plan_renames(files, RenameSettings(event="beach")))

        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.renamed), 2)
        self.assertEqual([p.name for p in sorted(one.iterdir())],
                         ["2026-06-12_beach_001.jpg"])
        self.assertEqual([p.name for p in sorted(two.iterdir())],
                         ["2026-06-12_beach_002.jpg"])

        # The log must name both folders, not the same one twice.
        pairs = json.loads(result.log_path.read_text())["pairs"]
        self.assertEqual({str(Path(old).parent) for old, _new in pairs},
                         {str(one), str(two)})

        undone = renamer.undo_last()
        self.assertEqual(undone.errors, [])
        self.assertTrue((one / "IMG_0001.jpg").exists())
        self.assertTrue((two / "IMG_0001.jpg").exists())

    def test_one_unreachable_file_does_not_abort_the_batch(self):
        """README promise: the rest of the batch still completes."""
        for name in ("a.jpg", "b.jpg", "c.jpg"):
            write_jpeg(self.dir / name, exif_date="2026:06:12 14:22:33")
        files = renamer.sort_files(renamer.scan_paths([self.dir])[0])
        plans = renamer.plan_renames(files, RenameSettings(event="trip"))
        (self.dir / "b.jpg").unlink()          # vanished after the preview

        result = renamer.apply_renames(plans)

        self.assertEqual(len(result.renamed), 2)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0][0], "b.jpg")
        self.assertFalse([n for n in self.listing() if renamer.TEMP_SUFFIX in n])

    def test_a_file_that_cannot_move_is_never_overwritten(self):
        """POSIX rename() overwrites silently, so the mover must refuse."""
        (self.dir / "a.jpg").write_bytes(b"AAAA")
        (self.dir / "b.jpg").write_bytes(b"BBBB")

        real_rename = Path.rename

        def locked(self, target):
            if self.name == "a.jpg":
                raise OSError(13, "Permission denied")
            return real_rename(self, target)

        Path.rename = locked
        try:
            result = renamer._two_phase_move(
                [(self.dir / "a.jpg", self.dir / "b.jpg"),
                 (self.dir / "b.jpg", self.dir / "a.jpg")])
        finally:
            Path.rename = real_rename

        self.assertEqual((self.dir / "a.jpg").read_bytes(), b"AAAA")
        self.assertEqual((self.dir / "b.jpg").read_bytes(), b"BBBB")
        self.assertEqual(result.renamed, [])
        self.assertTrue(any("still taken" in why for _name, why in result.errors))
        self.assertFalse([n for n in self.listing() if renamer.TEMP_SUFFIX in n])

    def test_a_stranded_temp_file_is_named_in_the_error(self):
        """If even the restore fails, say where the file actually is."""
        write_jpeg(self.dir / "a.jpg", exif_date="2026:06:12 14:22:33")
        real_rename = Path.rename

        def fails_after_staging(self, target):
            if renamer.TEMP_SUFFIX in self.name:      # forward move and restore
                raise OSError(13, "Permission denied")
            return real_rename(self, target)

        Path.rename = fails_after_staging
        try:
            result = renamer._two_phase_move(
                [(self.dir / "a.jpg", self.dir / "b.jpg")])
        finally:
            Path.rename = real_rename

        self.assertEqual(result.renamed, [])
        self.assertIn(renamer.TEMP_SUFFIX, result.errors[0][1])

    def test_undo_log_contains_absolute_pairs(self):
        write_jpeg(self.dir / "IMG_0001.jpg", exif_date="2026:06:12 14:22:33")
        files, _ = renamer.scan_paths([self.dir])
        result = renamer.apply_renames(
            renamer.plan_renames(files, RenameSettings(event="beach")))
        data = json.loads(result.log_path.read_text())
        old, new = data["pairs"][0]
        self.assertTrue(Path(old).is_absolute() and Path(new).is_absolute())
        renamer.undo_last()


class TestWindowsPathLimit(unittest.TestCase):
    """Windows refuses any path of 260 characters or more.

    A long Event name is the easy way for a user to blunder into that, so the
    plan has to cut the name down *before* anything touches the disk. These
    tests check the limit is respected and that a name is cut in a way that
    still reads like a name.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_long_event_name_is_capped_not_attempted(self):
        write_jpeg(self.dir / "IMG_0001.jpg", exif_date="2026:06:12 14:22:33")
        files, _ = renamer.scan_paths([self.dir])
        plans = renamer.plan_renames(files, RenameSettings(event="x" * 300))
        self.assertLessEqual(len(str(plans[0].target)), renamer.MAX_PATH_USABLE)
        self.assertTrue(plans[0].truncated)
        self.assertFalse(plans[0].too_long)

    def test_a_normal_name_is_left_alone(self):
        write_jpeg(self.dir / "IMG_0001.jpg", exif_date="2026:06:12 14:22:33")
        files, _ = renamer.scan_paths([self.dir])
        plans = renamer.plan_renames(files, RenameSettings(event="beach day"))
        self.assertFalse(plans[0].truncated)
        self.assertEqual(plans[0].new_name, "2026-06-12_beach-day_001.jpg")

    def test_a_capped_name_does_not_end_on_a_dangling_separator(self):
        stem, cut = renamer.fit_stem_to_path(
            "2026-06-12_" + "ab-" * 200, directory=self.dir, ext=".jpg")
        self.assertTrue(cut)
        self.assertFalse(stem.endswith(("-", "_", " ", ".")))

    def test_capping_still_leaves_room_for_the_dedupe_suffix(self):
        for index in range(3):
            write_jpeg(self.dir / f"IMG_{index}.jpg", exif_date="2026:06:12 14:22:33")
        files, _ = renamer.scan_paths([self.dir])
        # date_time makes all three want the identical name, so two get " (n)".
        plans = renamer.plan_renames(
            files, RenameSettings(pattern="{date}_" + "y" * 300))
        for plan in plans:
            self.assertLessEqual(len(str(plan.target)), renamer.MAX_PATH_USABLE)
        self.assertTrue(any(p.conflict for p in plans))

    def test_a_folder_that_is_already_too_deep_is_refused_not_half_renamed(self):
        write_jpeg(self.dir / "IMG_0001.jpg", exif_date="2026:06:12 14:22:33")
        files, _ = renamer.scan_paths([self.dir])
        plans = renamer.plan_renames(files, RenameSettings(event="beach"))
        plans[0].too_long = True          # pretend the folder path is enormous
        result = renamer.apply_renames(plans)
        self.assertEqual(result.renamed, [])
        self.assertIn("too long", result.errors[0][1])
        self.assertTrue((self.dir / "IMG_0001.jpg").exists())

    def test_the_temporary_name_never_breaks_the_limit_itself(self):
        """Phase 1 parks each file under a temp name. That name must fit too,
        or a rename could fail for a name the user never asked for."""
        long_name = "z" * 240 + ".jpg"
        path = self.dir / long_name
        write_jpeg(path)
        temp = renamer._temp_name(path, 0)
        self.assertLessEqual(len(str(temp)), renamer.MAX_PATH_USABLE)

    def test_a_temporary_name_never_lands_on_an_existing_file(self):
        """On Linux rename() silently destroys the file it lands on, so a
        left-over temp file from an earlier crash must be stepped over."""
        path = self.dir / "IMG_0001.jpg"
        write_jpeg(path)
        squatter = self.dir / f"IMG_0001.jpg{renamer.TEMP_SUFFIX}0"
        squatter.write_text("a survivor of an earlier crash")
        temp = renamer._temp_name(path, 0)
        self.assertNotEqual(temp, squatter)
        self.assertFalse(temp.exists())
        self.assertEqual(squatter.read_text(), "a survivor of an earlier crash")


class TestCaseOnlyRename(unittest.TestCase):
    """IMG_0001.JPG -> img_0001.jpg is the same file to Windows.

    Renaming straight over itself would be refused there, which is exactly
    why every move goes via a temporary name first. Worth pinning down: it is
    the most common rename this app does (the lowercase switch is on by
    default) and it is the one Linux would let us get away with.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        for spent in renamer.history_dir().glob("*.json*"):
            spent.unlink()

    def test_lowercasing_a_name_is_not_treated_as_a_collision(self):
        write_jpeg(self.dir / "IMG_0001.JPG", exif_date="2026:06:12 14:22:33")
        files, _ = renamer.scan_paths([self.dir])
        plans = renamer.plan_renames(
            files, RenameSettings(mode=Mode.REPLACE, find="IMG", replace_with="img"))
        self.assertEqual(plans[0].new_name, "img_0001.jpg")
        self.assertFalse(plans[0].conflict, "the file collided with itself")

    def test_a_case_only_rename_round_trips(self):
        write_jpeg(self.dir / "IMG_0001.JPG", exif_date="2026:06:12 14:22:33")
        files, _ = renamer.scan_paths([self.dir])
        plans = renamer.plan_renames(
            files, RenameSettings(mode=Mode.REPLACE, find="IMG", replace_with="img"))
        result = renamer.apply_renames(plans)
        self.assertEqual(len(result.renamed), 1)
        self.assertEqual([p.name for p in self.dir.iterdir()], ["img_0001.jpg"])
        renamer.undo_last()
        self.assertEqual([p.name for p in self.dir.iterdir()], ["IMG_0001.JPG"])


class TestUndoDurability(unittest.TestCase):
    """The undo log is the user's only way back. Losing it is the worst bug
    this program could have, so a failed undo must not consume it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        for spent in renamer.history_dir().glob("*.json"):
            spent.unlink()

    def tearDown(self):
        self.tmp.cleanup()
        for spent in renamer.history_dir().glob("*.json*"):
            spent.unlink()

    def _rename_one(self):
        write_jpeg(self.dir / "IMG_0001.jpg", exif_date="2026:06:12 14:22:33")
        files, _ = renamer.scan_paths([self.dir])
        return renamer.apply_renames(
            renamer.plan_renames(files, RenameSettings(event="beach")))

    def test_a_blocked_undo_keeps_the_log_so_it_can_be_retried(self):
        self._rename_one()
        real_rename = Path.rename

        def refuse(self, target):
            raise PermissionError(13, "file is open in another program")

        Path.rename = refuse
        try:
            result = renamer.undo_last()
        finally:
            Path.rename = real_rename

        self.assertEqual(result.renamed, [])
        self.assertTrue(result.errors)
        self.assertIsNotNone(renamer.last_log())      # still there to retry

        again = renamer.undo_last()                   # now that it is free
        self.assertEqual(len(again.renamed), 1)
        self.assertTrue((self.dir / "IMG_0001.jpg").exists())
        self.assertIsNone(renamer.last_log())         # only now is it spent

    def test_a_successful_undo_retires_the_log(self):
        self._rename_one()
        renamer.undo_last()
        self.assertIsNone(renamer.last_log())

    def test_an_undo_of_vanished_files_retires_the_log(self):
        result = self._rename_one()
        result.moved[0][1].unlink()                   # user deleted the photo
        outcome = renamer.undo_last()
        self.assertTrue(outcome.errors)
        self.assertIsNone(renamer.last_log())

    def test_a_corrupt_log_is_retired_rather_than_failing_forever(self):
        self._rename_one()
        renamer.last_log().write_text("{ this is not json", encoding="utf-8")
        outcome = renamer.undo_last()
        self.assertIn("unreadable", outcome.errors[0][1])
        self.assertIsNone(renamer.last_log())


class TestHeicSupport(unittest.TestCase):
    """HEIC is advertised as supported, so the code must be honest about what
    'supported' means with and without the pillow-heif plugin installed."""

    def test_heic_is_accepted_for_renaming_either_way(self):
        self.assertIn(".heic", renamer.SUPPORTED_EXTS)
        self.assertIn(".heif", renamer.SUPPORTED_EXTS)

    def test_thumbnails_are_only_promised_when_the_plugin_is_present(self):
        promised = renamer.HEIC_EXTS <= renamer.THUMBNAILABLE_EXTS
        self.assertEqual(promised, renamer.HEIC_SUPPORT)

    def test_a_heic_capture_date_is_read_when_the_plugin_is_present(self):
        if not renamer.HEIC_SUPPORT:
            self.skipTest("pillow-heif is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "IMG_9999.heic"
            image = Image.new("RGB", (32, 24), (10, 10, 10))
            exif = image.getexif()
            exif.get_ifd(0x8769)[36867] = "2026:06:12 14:22:33"
            image.save(path, format="HEIF", exif=exif)
            taken, from_exif = renamer.read_taken_at(path)
            self.assertTrue(from_exif)
            self.assertEqual(taken, datetime(2026, 6, 12, 14, 22, 33))


class TestScanResilience(unittest.TestCase):

    def test_a_file_that_vanishes_mid_scan_is_skipped_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            write_jpeg(directory / "a.jpg")
            ghost = directory / "b.jpg"
            write_jpeg(ghost)
            # The folder listing succeeds, then the file disappears before we
            # can read it - a card pulled out mid-scan looks exactly like this.
            real_read = renamer.read_taken_at

            def vanishing_read(path):
                if path.name == "b.jpg":
                    raise FileNotFoundError(2, "No such file or directory")
                return real_read(path)

            renamer.read_taken_at = vanishing_read
            try:
                files, skipped = renamer.scan_paths([directory])
            finally:
                renamer.read_taken_at = real_read
            self.assertEqual([f.name for f in files], ["a.jpg"])
            self.assertEqual(skipped, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
