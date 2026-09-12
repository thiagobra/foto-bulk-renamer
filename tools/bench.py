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


def old_scan_paths(paths, *, read_exif=True):
    """The pre-P5 scan: iterdir + is_file, then a stat per file for the size.

    Kept verbatim so the syscall row below is measured rather than quoted.
    """
    candidates = []
    skipped = 0
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            candidates.extend(sorted(c for c in p.iterdir() if c.is_file()))
        elif p.is_file():
            candidates.append(p)
        else:
            skipped += 1

    files, seen = [], set()
    for p in candidates:
        if p.suffix.lower() not in renamer.SUPPORTED_EXTS:
            skipped += 1
            continue
        try:
            taken_at, from_exif = (renamer.read_taken_at(p) if read_exif
                                   else renamer.read_taken_at(p, use_exif=False))
            size = p.stat().st_size
        except OSError:
            skipped += 1
            continue
        photo = renamer.PhotoFile(path=p, taken_at=taken_at, size=size,
                                  from_exif=from_exif)
        if photo.resolved in seen:
            continue
        seen.add(photo.resolved)
        files.append(photo)
    return files, skipped


def count_fs_calls(work):
    """Run `work` with every filesystem question the scan can ask counted.

    DirEntry.stat() is counted too, by handing scandir's entries back wrapped:
    it is free on Windows, where the listing already carries the size, and one
    call on Unix, so leaving it out would flatter the result on Linux.
    """
    # listing  os.scandir / Path.iterdir — one per folder
    # stat     every stat that really happens, Path.is_file's own included
    # entry    DirEntry questions: free on Windows, at most one call on Unix
    calls = {"listing": 0, "stat": 0, "entry": 0}
    real_scandir, real_stat = os.scandir, Path.stat
    real_is_file, real_iterdir = Path.is_file, Path.iterdir

    class CountingEntry:
        def __init__(self, entry):
            self._entry, self.path, self.name = entry, entry.path, entry.name

        def is_file(self, **kw):
            calls["entry"] += 1
            return self._entry.is_file(**kw)

        def stat(self, **kw):
            calls["entry"] += 1
            return self._entry.stat(**kw)

    class CountingScandir:
        def __init__(self, *a, **kw):
            calls["listing"] += 1
            self._entries = real_scandir(*a, **kw)

        def __enter__(self):
            return (CountingEntry(entry) for entry in self._entries)

        def __exit__(self, *exc):
            return self._entries.__exit__(*exc)

    def counting_stat(self, *a, **kw):
        calls["stat"] += 1
        return real_stat(self, *a, **kw)

    def counting_is_file(self, *a, **kw):
        # Not counted itself: pathlib answers it with a stat, and that stat
        # goes through the counter below. Counting both would double it.
        return real_is_file(self, *a, **kw)

    def counting_iterdir(self, *a, **kw):
        calls["listing"] += 1
        return real_iterdir(self, *a, **kw)

    os.scandir = CountingScandir
    Path.stat, Path.is_file, Path.iterdir = (counting_stat, counting_is_file,
                                             counting_iterdir)
    try:
        work()
    finally:
        os.scandir, Path.stat = real_scandir, real_stat
        Path.is_file, Path.iterdir = real_is_file, real_iterdir
    return calls


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
    old_calls = count_fs_calls(
        lambda: old_scan_paths([photos], read_exif=False))
    new_calls = count_fs_calls(
        lambda: renamer.scan_paths([photos], read_exif=False))
    # Best of three for these two: they are the same work, and a single run
    # of each would mostly measure which one warmed the page cache.
    _, old_scan = time_it("", lambda: old_scan_paths([photos], read_exif=False))
    _, new_scan = time_it("", lambda: renamer.scan_paths([photos],
                                                         read_exif=False))

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
    row("  of which: the listing (iterdir vs scandir)", old_scan, new_scan)
    rule()
    print()
    print(f"{'The drop, in filesystem calls per file':<44}{'before':>7}{'after':>7}{'':>8}")
    print(f"{'':<44}{'calls':>7}{'calls':>7}{'':>8}")
    rule()

    def per_file(calls, key):
        return calls[key] / max(1, len(files))

    row("stat calls", per_file(old_calls, "stat"), per_file(new_calls, "stat"))
    row("directory listings", per_file(old_calls, "listing"),
        per_file(new_calls, "listing"))
    row("DirEntry questions  (free on Windows)",
        per_file(old_calls, "entry"), per_file(new_calls, "entry"))
    rule()
    print()
    print("On a fast SSD the two scans time the same; the saving is in the")
    print("calls, which is what an SD card over USB charges for.")
    print()
    print("Two of the four stats per file are gone: is_file's and the one for")
    print("the size, both answered by the listing now. The two left are")
    print("read_taken_at's modified-time fallback and the one pathlib spends")
    print("inside PhotoFile.resolve(). DirEntry questions are free on Windows,")
    print("where the listing already carries the answer.")
    print()
    print("'before' re-creates the old approach in this script; 'after' calls")
    print("the current code. The listing row is the one that used to grow with")
    print("the folder rather than with the job — try --ticked 20 to see it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
