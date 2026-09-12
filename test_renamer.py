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


class TestPlaceToken(unittest.TestCase):
    """{place} is an ordinary token: it composes, it moves, it can be empty."""

    def setUp(self):
        self.photo = make_photo("IMG_20260612_142233", taken="2026-09-15 10:00:00")
        self.stays = (renamer.parse_stay("2026-09-15", "2026-09-15", "New York City"),
                      renamer.parse_stay("2026-09-16", "2026-09-20", "Boston"))

    def name(self, pattern: str, photo=None) -> str:
        settings = RenameSettings(pattern=pattern, event="lakeside wedding",
                                  stays=self.stays)
        return renamer.build_new_stem(photo or self.photo, settings, 7)

    def test_place_at_the_end(self):
        self.assertEqual(self.name("{date}_{event}_{n}_{place}"),
                         "2026-09-15_lakeside-wedding_007_new-york-city")

    def test_place_at_the_beginning(self):
        self.assertEqual(self.name("{place}_{date}_{event}_{n}"),
                         "new-york-city_2026-09-15_lakeside-wedding_007")

    def test_place_in_the_middle(self):
        self.assertEqual(self.name("{date}_{place}_{event}_{n}"),
                         "2026-09-15_new-york-city_lakeside-wedding_007")

    def test_a_photo_from_another_day_picks_up_its_own_place(self):
        boston = make_photo("IMG_0002", taken="2026-09-17 08:00:00")
        self.assertEqual(self.name("{date}_{place}_{n}", photo=boston),
                         "2026-09-17_boston_007")

    def test_a_photo_outside_every_stay_leaves_no_hole(self):
        stray = make_photo("IMG_0003", taken="2026-09-22 08:00:00")
        self.assertEqual(self.name("{date}_{event}_{n}_{place}", photo=stray),
                         "2026-09-22_lakeside-wedding_007")

    def test_an_empty_place_at_the_beginning_leaves_no_leading_separator(self):
        stray = make_photo("IMG_0003", taken="2026-09-22 08:00:00")
        self.assertEqual(self.name("{place}_{date}_{n}", photo=stray),
                         "2026-09-22_007")

    def test_no_stays_configured_renders_place_as_nothing(self):
        settings = RenameSettings(pattern="{date}_{place}_{n}")
        self.assertEqual(renamer.build_new_stem(self.photo, settings, 7),
                         "2026-09-15_007")

    def test_expand_pattern_still_works_without_the_new_argument(self):
        """The default keeps every existing caller - and TestTokens - honest."""
        self.assertEqual(
            renamer.expand_pattern("{place}{date}", photo=self.photo,
                                   event="", index=1, digits=3),
            "2026-09-15")

    def test_the_token_is_documented_for_the_chip_row(self):
        self.assertIn("{place}", renamer.TOKEN_HELP)

    def test_insert_mode_ignores_the_stay_list(self):
        """Insert and Find & replace promise to leave the original name alone,
        and that includes not quietly gaining a place."""
        settings = RenameSettings(mode=Mode.INSERT, insert_text="praia",
                                  stays=self.stays)
        self.assertEqual(renamer.build_new_stem(self.photo, settings, 1),
                         "praia_IMG_20260612_142233")


class TestStays(unittest.TestCase):
    """parse_stay turns two text boxes into a period, or says why it cannot."""

    def test_a_bare_date_covers_the_whole_day(self):
        stay = renamer.parse_stay("2026-09-15", "2026-09-15", "New York City")
        self.assertEqual(stay.start, datetime(2026, 9, 15, 0, 0, 0))
        self.assertEqual(stay.end, datetime(2026, 9, 15, 23, 59, 59))
        self.assertEqual(stay.place, "New York City")

    def test_a_date_and_time_is_taken_literally(self):
        stay = renamer.parse_stay("2026-09-18 13:00", "2026-09-18 18:00", "Fenway Park")
        self.assertEqual(stay.start, datetime(2026, 9, 18, 13, 0))
        self.assertEqual(stay.end, datetime(2026, 9, 18, 18, 0))

    def test_seconds_are_accepted_too(self):
        stay = renamer.parse_stay("2026-09-18 13:00:30", "2026-09-18 18:00:45", "Fenway")
        self.assertEqual(stay.start.second, 30)
        self.assertEqual(stay.end.second, 45)

    def test_a_multi_day_stay_runs_to_the_end_of_the_last_day(self):
        stay = renamer.parse_stay("2026-09-16", "2026-09-20", "Boston")
        self.assertEqual(stay.span.days, 4)
        self.assertEqual(stay.end, datetime(2026, 9, 20, 23, 59, 59))

    def test_junk_is_rejected_with_a_sentence_not_a_traceback(self):
        with self.assertRaises(ValueError) as caught:
            renamer.parse_stay("15/09/2026", "2026-09-15", "Boston")
        self.assertEqual(str(caught.exception),
                         "15/09/2026 is not a date — use 2026-09-15")

    def test_a_word_where_a_date_should_be_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            renamer.parse_stay("2026-09-15", "yesterday", "Boston")
        self.assertIn("not a date", str(caught.exception))

    def test_a_missing_zero_is_forgiven_rather_than_scolded(self):
        """strptime reads "2026-9-15" correctly, so refusing it would be
        pedantry. Only text that has no date in it at all is an error."""
        stay = renamer.parse_stay("2026-9-15", "2026-9-15", "Boston")
        self.assertEqual(stay.start.date(), datetime(2026, 9, 15).date())

    def test_a_missing_date_says_which_one(self):
        with self.assertRaises(ValueError) as caught:
            renamer.parse_stay("2026-09-15", "  ", "Boston")
        self.assertIn("end date", str(caught.exception))

    def test_a_missing_place_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            renamer.parse_stay("2026-09-15", "2026-09-15", "   ")
        self.assertIn("place", str(caught.exception))

    def test_a_backwards_range_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            renamer.parse_stay("2026-09-20", "2026-09-16", "Boston")
        self.assertIn("before the start", str(caught.exception))

    def test_surrounding_whitespace_is_forgiven(self):
        stay = renamer.parse_stay("  2026-09-15 ", " 2026-09-15  ", "  Boston ")
        self.assertEqual(stay.place, "Boston")
        self.assertEqual(stay.start.day, 15)


class TestPlaceResolution(unittest.TestCase):
    """Which stay a photo belongs to, given the list the user typed."""

    def setUp(self):
        self.nyc = renamer.parse_stay("2026-09-15", "2026-09-15", "New York City")
        self.boston = renamer.parse_stay("2026-09-16", "2026-09-20", "Boston")
        self.fenway = renamer.parse_stay("2026-09-18 13:00", "2026-09-18 18:00",
                                         "Fenway Park")
        self.stays = (self.nyc, self.boston, self.fenway)

    def place(self, when: str) -> str:
        return renamer.place_for(datetime.fromisoformat(when), self.stays)

    def test_a_photo_inside_one_stay_gets_it(self):
        self.assertEqual(self.place("2026-09-15 09:30"), "New York City")
        self.assertEqual(self.place("2026-09-17 09:30"), "Boston")

    def test_the_narrowest_overlapping_stay_wins(self):
        self.assertEqual(self.place("2026-09-18 14:00"), "Fenway Park")
        self.assertEqual(self.place("2026-09-18 19:00"), "Boston")

    def test_the_boundaries_are_inclusive(self):
        self.assertEqual(self.place("2026-09-15 00:00:00"), "New York City")
        self.assertEqual(self.place("2026-09-15 23:59:59"), "New York City")

    def test_no_match_is_an_empty_string_not_an_error(self):
        self.assertEqual(self.place("2026-09-14 12:00"), "")
        self.assertEqual(self.place("2026-09-21 12:00"), "")

    def test_no_stays_at_all_is_an_empty_string(self):
        self.assertEqual(renamer.place_for(datetime(2026, 9, 15), ()), "")

    def test_a_tie_keeps_the_earlier_stay_in_the_list(self):
        first = renamer.parse_stay("2026-09-15", "2026-09-15", "Museum")
        second = renamer.parse_stay("2026-09-15", "2026-09-15", "Park")
        self.assertEqual(
            renamer.place_for(datetime(2026, 9, 15, 12), (first, second)), "Museum")
        self.assertEqual(
            renamer.place_for(datetime(2026, 9, 15, 12), (second, first)), "Park")


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


class TestDirectoryIndex(unittest.TestCase):
    """The index is a cache of a folder listing. These are the ways a cache
    goes wrong: it misses a change we made, it blocks a name we just freed,
    or it never notices a change somebody else made."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_it_lists_a_directory_once(self):
        (self.dir / "a.jpg").write_bytes(b"x")
        index = renamer.DirectoryIndex()
        index.ensure({self.dir})
        self.assertEqual(index.names_for(self.dir), {"a.jpg"})

    def test_names_are_lowercased_because_windows_compares_that_way(self):
        (self.dir / "SHOUTING.JPG").write_bytes(b"x")
        index = renamer.DirectoryIndex()
        index.ensure({self.dir})
        self.assertEqual(index.names_for(self.dir), {"shouting.jpg"})

    def test_an_unreadable_directory_reads_as_empty_not_an_error(self):
        index = renamer.DirectoryIndex()
        index.ensure({self.dir / "does-not-exist"})
        self.assertEqual(index.names_for(self.dir / "does-not-exist"), set())

    def test_apply_moves_keeps_the_index_true(self):
        (self.dir / "old.jpg").write_bytes(b"x")
        index = renamer.DirectoryIndex()
        index.ensure({self.dir})
        index.apply_moves([(self.dir / "old.jpg", self.dir / "new.jpg")])
        self.assertEqual(index.names_for(self.dir), {"new.jpg"})

    def test_invalidate_picks_up_a_file_created_behind_our_back(self):
        index = renamer.DirectoryIndex()
        index.ensure({self.dir})
        self.assertEqual(index.names_for(self.dir), set())
        (self.dir / "appeared.jpg").write_bytes(b"x")   # as if from Explorer
        index.ensure({self.dir})                        # already listed: no re-read
        self.assertEqual(index.names_for(self.dir), set())
        index.invalidate()
        index.ensure({self.dir})
        self.assertEqual(index.names_for(self.dir), {"appeared.jpg"})

    def test_a_name_the_batch_is_vacating_can_be_taken_by_another_file(self):
        """The real a -> b, b -> a swap.

        Both target names are sitting on disk right now, so a naive "is this
        name taken?" says yes to both and de-duplicates them into swap_1 (1)
        and swap_2 (1). They are not collisions: this same batch is giving
        both names up. Chronological order puts swap_2 first, so the two
        files exchange names.
        """
        write_jpeg(self.dir / "swap_1.jpg", exif_date="2026:06:12 15:00:00")
        write_jpeg(self.dir / "swap_2.jpg", exif_date="2026:06:12 14:00:00")
        files = renamer.sort_files(renamer.scan_paths([self.dir])[0])
        self.assertEqual([f.name for f in files], ["swap_2.jpg", "swap_1.jpg"])

        plans = renamer.plan_renames(
            files, RenameSettings(pattern="{event}_{n}", event="swap", digits=1))
        self.assertEqual([(p.photo.name, p.new_name) for p in plans],
                         [("swap_2.jpg", "swap_1.jpg"),
                          ("swap_1.jpg", "swap_2.jpg")])
        self.assertFalse(any(p.conflict for p in plans),
                         "a name this batch is giving up was treated as taken")

    def test_a_file_outside_the_batch_still_blocks_the_name(self):
        write_jpeg(self.dir / "IMG_0001.jpg", exif_date="2026:06:12 14:22:33")
        (self.dir / "swap_1.jpg").write_bytes(b"not ours")
        files, _ = renamer.scan_paths([self.dir / "IMG_0001.jpg"])
        plans = renamer.plan_renames(
            files, RenameSettings(pattern="{event}_{n}", event="swap", digits=1))
        self.assertEqual(plans[0].new_name, "swap_1 (1).jpg")
        self.assertTrue(plans[0].conflict)

    def test_a_supplied_index_gives_the_same_answer_as_no_index(self):
        for i in (1, 2, 3):
            write_jpeg(self.dir / f"IMG_000{i}.jpg",
                       exif_date=f"2026:06:12 14:2{i}:00")
        (self.dir / "2026-06-12_beach_002.jpg").write_bytes(b"in the way")
        files = renamer.sort_files(renamer.scan_paths([self.dir])[0])
        settings = RenameSettings(event="beach")

        index = renamer.DirectoryIndex()
        self.assertEqual(
            [p.new_name for p in renamer.plan_renames(files, settings)],
            [p.new_name for p in renamer.plan_renames(files, settings, index=index)])


class TestPreviewTouchesNoDisk(unittest.TestCase):
    """The contract that stops this regressing silently.

    plan_renames() and sort_files() run on every keystroke. Given a directory
    index that is already populated, they must perform no filesystem calls at
    all — so the cost of the live preview depends on how many photos you are
    renaming, never on how many files share the folder.
    """

    def _counting_run(self, body):
        """Run `body` with os.scandir and Path.resolve counted."""
        calls = {"scandir": 0, "resolve": 0}
        real_scandir, real_resolve = os.scandir, Path.resolve

        def counting_scandir(*args, **kwargs):
            calls["scandir"] += 1
            return real_scandir(*args, **kwargs)

        def counting_resolve(self, *args, **kwargs):
            calls["resolve"] += 1
            return real_resolve(self, *args, **kwargs)

        os.scandir = counting_scandir
        Path.resolve = counting_resolve
        try:
            body()
        finally:
            os.scandir = real_scandir
            Path.resolve = real_resolve
        return calls

    def test_planning_with_a_warm_index_makes_no_syscalls(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            files = [PhotoFile(path=directory / f"IMG_{i:04d}.jpg",
                               taken_at=datetime(2026, 6, 12, 14, 0, i),
                               size=1234)
                     for i in range(50)]
            index = renamer.DirectoryIndex()
            index.ensure({directory})          # the one listing, taken up front
            settings = RenameSettings(event="beach")

            calls = self._counting_run(
                lambda: renamer.plan_renames(files, settings, index=index))

        self.assertEqual(calls["scandir"], 0,
                         "plan_renames re-listed the directory")
        self.assertEqual(calls["resolve"], 0,
                         "plan_renames resolved a path on the hot path")

    def test_sorting_makes_no_syscalls(self):
        files = [PhotoFile(path=Path(f"/tmp/IMG_{i}.jpg"),
                           taken_at=datetime(2026, 6, 12, 14, 0, i),
                           size=1)
                 for i in range(50)]
        calls = self._counting_run(lambda: renamer.sort_files(files))
        self.assertEqual(calls["resolve"], 0,
                         "sort_files resolved a path instead of using sort_key")

    def test_a_photofile_resolves_its_path_exactly_once(self):
        calls = self._counting_run(
            lambda: PhotoFile(path=Path("/tmp/IMG_0001.jpg"),
                              taken_at=datetime(2026, 6, 12, 14, 22, 33),
                              size=1))
        self.assertEqual(calls["resolve"], 1)


class TestDerivedFieldsStayInStep(unittest.TestCase):
    """PhotoFile caches three derived values. A cache that can go stale
    silently is worse than no cache, so the two things that can invalidate
    them have to refresh all three."""

    def test_set_taken_at_refreshes_the_display_and_the_sort_key(self):
        photo = make_photo("IMG_0001", taken="2026-06-12 14:22:33")
        self.assertEqual(photo.date_display, "12 Jun 2026 14:22")

        photo.set_taken_at(datetime(2024, 1, 2, 3, 4, 5), from_exif=True)
        self.assertEqual(photo.taken_at, datetime(2024, 1, 2, 3, 4, 5))
        self.assertTrue(photo.from_exif)
        self.assertEqual(photo.date_display, "02 Jan 2024 03:04")
        self.assertEqual(photo.sort_key[0], datetime(2024, 1, 2, 3, 4, 5))

    def test_relocate_refreshes_the_resolved_path_and_the_sort_key(self):
        photo = make_photo("IMG_0001")
        was = photo.resolved

        # Compare against resolve(), not the literal path: on Windows a
        # rooted path with no drive letter resolves onto the current drive
        # ("/tmp/x" -> "D:/tmp/x"), so a bare Path() could only ever match
        # on POSIX.
        new_path = Path("/tmp/2026-06-12_beach_001.jpg")
        photo.relocate(new_path)
        self.assertEqual(photo.name, "2026-06-12_beach_001.jpg")
        self.assertNotEqual(photo.resolved, was)
        self.assertEqual(photo.resolved, new_path.resolve())
        self.assertEqual(photo.sort_key[1], renamer.natural_key(photo.name))

    def test_the_cached_sort_key_still_sorts_naturally(self):
        files = [make_photo(f"IMG_{n}", taken="2026-06-12 14:22:33")
                 for n in (10, 9, 2)]
        self.assertEqual([f.name for f in renamer.sort_files(files)],
                         ["IMG_2.jpg", "IMG_9.jpg", "IMG_10.jpg"])


class TestExifBackfill(unittest.TestCase):
    """scan_paths(read_exif=False) is deliberately half a scan: it is what
    lets a 2,000-photo drop appear instantly. backfill_exif_dates is the other
    half, and the GUI runs it on a worker thread."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_skipping_exif_falls_back_to_the_file_date(self):
        write_jpeg(self.dir / "IMG_0001.jpg", exif_date="2026:06:12 14:22:33")
        files, _ = renamer.scan_paths([self.dir], read_exif=False)
        self.assertFalse(files[0].from_exif)
        self.assertNotEqual(files[0].taken_at, datetime(2026, 6, 12, 14, 22, 33))

    def test_the_backfill_finds_the_date_the_fast_scan_skipped(self):
        write_jpeg(self.dir / "IMG_0001.jpg", exif_date="2026:06:12 14:22:33")
        files, _ = renamer.scan_paths([self.dir], read_exif=False)

        results = []
        renamer.backfill_exif_dates(files, on_result=lambda *r: results.append(r))
        self.assertEqual(len(results), 1)

        photo, taken_at, from_exif = results[0]
        self.assertTrue(from_exif)
        # The worker hands values across; only the caller applies them.
        self.assertFalse(photo.from_exif)
        photo.set_taken_at(taken_at, from_exif=from_exif)
        self.assertEqual(photo.taken_at, datetime(2026, 6, 12, 14, 22, 33))
        self.assertEqual(photo.date_display, "12 Jun 2026 14:22")
        self.assertEqual(photo.sort_key[0], datetime(2026, 6, 12, 14, 22, 33))

    def test_every_file_is_reported_so_progress_can_be_honest(self):
        for i in (1, 2, 3):
            write_jpeg(self.dir / f"IMG_000{i}.jpg")      # no EXIF at all
        files, _ = renamer.scan_paths([self.dir], read_exif=False)
        results = []
        done = renamer.backfill_exif_dates(
            files, on_result=lambda *r: results.append(r))
        self.assertEqual(done, 3)
        self.assertEqual(len(results), 3)
        self.assertFalse(any(from_exif for _, _, from_exif in results))

    def test_should_stop_abandons_the_rest(self):
        for i in (1, 2, 3, 4):
            write_jpeg(self.dir / f"IMG_000{i}.jpg")
        files = renamer.sort_files(renamer.scan_paths([self.dir], read_exif=False)[0])
        results = []
        done = renamer.backfill_exif_dates(
            files,
            on_result=lambda *r: results.append(r),
            should_stop=lambda: len(results) >= 2)
        self.assertEqual((done, len(results)), (2, 2))

    def test_a_video_is_reported_without_being_opened(self):
        """Videos carry no EXIF, so re-reading them would be pure waste."""
        (self.dir / "clip.mp4").write_bytes(b"not really a video")
        files, _ = renamer.scan_paths([self.dir], read_exif=False)
        scanned_date = files[0].taken_at

        opened = []
        real_read = renamer.read_taken_at
        renamer.read_taken_at = lambda *a, **k: opened.append(1) or real_read(*a, **k)
        try:
            results = []
            renamer.backfill_exif_dates(files, on_result=lambda *r: results.append(r))
        finally:
            renamer.read_taken_at = real_read

        self.assertEqual(opened, [], "a video was re-read for EXIF it cannot have")
        self.assertEqual(results[0][1], scanned_date)

    def test_a_file_deleted_mid_backfill_keeps_the_date_it_had(self):
        write_jpeg(self.dir / "IMG_0001.jpg", exif_date="2026:06:12 14:22:33")
        files, _ = renamer.scan_paths([self.dir], read_exif=False)
        scanned_date = files[0].taken_at
        (self.dir / "IMG_0001.jpg").unlink()          # card pulled out

        results = []
        done = renamer.backfill_exif_dates(
            files, on_result=lambda *r: results.append(r))
        self.assertEqual(done, 1)
        self.assertEqual(results[0][1], scanned_date)


if __name__ == "__main__":
    unittest.main(verbosity=2)
