"""Measure what a keystroke in the live preview actually costs.

The preview re-plans every name on every keystroke. That is the right design,
but it only stays affordable if the work depends on how many photos you are
renaming rather than on how many files happen to share the folder. This script
measures both, so the claim can be checked on real hardware — an SD card on
Windows behaves nothing like a container's SSD.

    python tools/bench.py                     # 2,000 files, all ticked
    python tools/bench.py --files 5000
    python tools/bench.py --files 2000 --ticked 20    # the O(folder) point

It is deliberately NOT called test_*.py: CI runs `unittest discover -p
"test_*.py"`, and timings are machine-dependent, so they have no business in a
pass/fail gate.

Each row shows the old approach against the current one. The "before" columns
re-create what the code used to do — a fresh directory listing per keystroke,
resolve() per file, strftime() per row — so the comparison is like for like
rather than a number quoted from memory.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image                                        # noqa: E402

import renamer                                               # noqa: E402
from renamer import RenameSettings                           # noqa: E402


# --------------------------------------------------------------------------
# What the code used to do, kept here so the comparison is honest
# --------------------------------------------------------------------------

def old_existing_names(directories, batch):
    """The pre-A2 listing: scandir plus a realpath for every entry."""
    taken = {}
    for directory in directories:
        names = set()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if Path(entry.path).resolve() not in batch:
                        names.add(entry.name.lower())
        except OSError:
            pass
        taken[directory] = names
    return taken


def old_plan_renames(files, settings):
    """The pre-A2 planner, kept verbatim so 'before' is measured, not quoted.

    The only differences from the current one are the two this work removed:
    the directory listing is rebuilt on every call, and every file is resolved
    to spot which entries belong to the batch.
    """
    batch = {f.path.resolve() for f in files}
    directories = {f.path.parent for f in files}
    taken = old_existing_names(directories, batch)

    plans = []
    for offset, photo in enumerate(files):
        stem = renamer.build_new_stem(photo, settings, settings.start + offset)
        ext = photo.ext.lower()
        directory = photo.path.parent
        stem, truncated = renamer.fit_stem_to_path(stem, directory=directory, ext=ext)
        too_long = (truncated
                    and len(str(directory / (stem + ext))) > renamer.MAX_PATH_USABLE)
        candidate = stem + ext

        conflict = False
        counter = 1
        while candidate.lower() in taken[directory]:
            conflict = True
            candidate = f"{stem} ({counter}){ext}"
            counter += 1
        taken[directory].add(candidate.lower())
        plans.append(renamer.RenamePlan(photo=photo, new_name=candidate,
                                        conflict=conflict,
                                        truncated=truncated and not too_long,
                                        too_long=too_long))
    return plans


# --------------------------------------------------------------------------
# Timing
# --------------------------------------------------------------------------

def time_it(label, fn, repeats=3):
    """Best of `repeats`, in milliseconds. Best, not mean: we are after the
    cost of the work, not the cost of whatever else the machine was doing."""
    best = float("inf")
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - start)
    return label, best * 1000


def make_photos(directory: Path, count: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (48, 36), (90, 110, 130))
    exif = image.getexif()
    exif.get_ifd(0x8769)[36867] = "2026:06:12 14:22:33"
    for i in range(count):
        image.save(directory / f"IMG_{i:05d}.JPG", "JPEG", exif=exif)


def rule(width=74):
    print("-" * width)


def row(label, before_ms, after_ms):
    def cell(value):
        if value is None:
            return "      —"
        # Below a hundredth of a millisecond there is nothing left to measure;
        # printing 0.0 would look like a failed reading rather than a result.
        return "     ~0" if value < 0.01 else f"{value:7.1f}"

    if before_ms is None or after_ms is None:
        speedup = ""
    elif after_ms < 0.01:
        speedup = "   gone"
    else:
        speedup = f"  {before_ms / after_ms:5.1f}x"
    print(f"{label:<44}{cell(before_ms)}{cell(after_ms)}{speedup}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=2000,
                        help="photos to put in the folder (default 2000)")
    parser.add_argument("--ticked", type=int, default=None,
                        help="how many are ticked for rename (default: all)")
    parser.add_argument("--keep", action="store_true",
                        help="do not delete the generated photos afterwards")
    args = parser.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="fotoren-bench-"))
    photos = workdir / "photos"
    print(f"Building {args.files} photos in {photos} …")
    make_photos(photos, args.files)

    try:
        return report(photos, args)
    finally:
        if args.keep:
            print(f"\nLeft the photos in {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def report(photos: Path, args) -> int:
    settings = RenameSettings(event="lakeside wedding")

    # --- the drop --------------------------------------------------------
    _, slow_scan = time_it("", lambda: renamer.scan_paths([photos]), repeats=1)
    _, fast_scan = time_it("", lambda: renamer.scan_paths([photos], read_exif=False),
                           repeats=1)

    files = renamer.sort_files(renamer.scan_paths([photos], read_exif=False)[0])
    ticked_count = args.ticked if args.ticked is not None else len(files)
    ticked = files[:ticked_count]
    checked = {f.resolved for f in ticked}

    # --- the pieces of a keystroke ---------------------------------------
    directories = {f.path.parent for f in ticked}

    warm = renamer.DirectoryIndex()
    warm.ensure(directories)

    _, old_listing = time_it(
        "", lambda: old_existing_names(directories, {f.path.resolve() for f in ticked}))
    _, new_listing = time_it("", lambda: warm.ensure(directories))

    _, old_filter = time_it(
        "", lambda: [f for f in files if f.path.resolve() in checked])
    _, new_filter = time_it("", lambda: [f for f in files if f.resolved in checked])

    _, old_dates = time_it(
        "", lambda: [f.taken_at.strftime("%d %b %Y %H:%M") for f in files])
    _, new_dates = time_it("", lambda: [f.date_display for f in files])

    _, old_sort = time_it(
        "", lambda: sorted(files, key=lambda f: (f.taken_at,
                                                 renamer.natural_key(f.name))))
    _, new_sort = time_it("", lambda: sorted(files, key=lambda f: f.sort_key))

    _, old_plan = time_it("", lambda: old_plan_renames(ticked, settings))
    _, new_plan = time_it(
        "", lambda: renamer.plan_renames(ticked, settings, index=warm))

    print()
    print(f"{len(files)} files in the folder, {len(ticked)} ticked for rename")
    print(f"python {sys.version.split()[0]} on {sys.platform}")
    print()
    print(f"{'Hot path, per keystroke':<44}{'before':>7}{'after':>7}{'':>8}")
    print(f"{'':<44}{'ms':>7}{'ms':>7}{'':>8}")
    rule()
    row("plan_renames()  (the whole preview)", old_plan, new_plan)
    row("  of which: the directory listing", old_listing, new_listing)
    row("ticked filter  (resolve per file)", old_filter, new_filter)
    row("date column  (strftime per row)", old_dates, new_dates)
    row("sort_files()  (natural_key per row)", old_sort, new_sort)
    rule()
    row(f"drop {len(files)} photos  (UI thread)", slow_scan, fast_scan)
    rule()
    print()
    print("'before' re-creates the old approach in this script; 'after' calls")
    print("the current code. The listing row is the one that used to grow with")
    print("the folder rather than with the job — try --ticked 20 to see it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
