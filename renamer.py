"""Core renaming logic for Foto Renamer.

This module deliberately contains NO GUI code. Everything here is a plain
function or a small dataclass, which means every rule can be unit-tested
without opening a window (see test_renamer.py).

Reading order if you are learning the code:
    1. PhotoFile / read_taken_at ..... how a file on disk becomes data
    2. Stay / place_for .............. how a capture date becomes a place
    3. expand_pattern ................ how "{date}_{event}_{n}" becomes text
    4. apply_cleanup / sanitize_stem . how that text is made Windows/web safe
    5. plan_renames .................. old name -> new name, for the preview
    6. apply_renames / undo_last ..... the only two functions that touch disk
"""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

# Pillow is only needed to read EXIF dates and draw thumbnails. Guarding the
# import keeps this module importable (and testable) without it installed.
try:
    from PIL import Image
except ImportError:  # pragma: no cover - Pillow is a hard runtime dependency
    Image = None

# Pillow on its own CANNOT open .heic/.heif (the format Apple devices shoot in).
# pillow-heif is a plugin that teaches it how. It is optional: without it the
# app still renames HEIC files, it just cannot read their capture date or draw
# a thumbnail. HEIC_SUPPORT lets the UI say so honestly instead of pretending.
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIC_SUPPORT = True
except Exception:  # not installed, or too old to register
    HEIC_SUPPORT = False


# --------------------------------------------------------------------------
# What we accept
# --------------------------------------------------------------------------

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".dng"}
VIDEO_EXTS = {".mp4", ".mov", ".3gp"}
SUPPORTED_EXTS = IMAGE_EXTS | VIDEO_EXTS

# Extensions Pillow can decode for a thumbnail. .heic/.heif only join the set
# when pillow-heif is installed, which is exactly when Pillow can read them.
HEIC_EXTS = {".heic", ".heif"}
THUMBNAILABLE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
if HEIC_SUPPORT:
    THUMBNAILABLE_EXTS |= HEIC_EXTS

# Windows forbids these characters in a file name, and these device names.
ILLEGAL_CHARS = '<>:"/\\|?*'
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                  *(f"LPT{i}" for i in range(1, 10))}

TEMP_SUFFIX = ".fotoren-tmp-"

# Windows refuses any path of 260 characters or more (the classic MAX_PATH),
# counting the drive, every folder, the file name and a hidden terminating
# byte. That leaves 259 usable characters. Long-path support exists on Windows
# 10+ but is off by default and File Explorer still trips over it, so we stay
# inside the classic limit rather than produce names the user cannot open.
MAX_PATH = 260
MAX_PATH_USABLE = MAX_PATH - 1
# Most file systems also cap a single name at 255 characters.
MAX_NAME = 255
# Head-room kept free so the " (1)" de-duplication suffix always still fits.
DEDUPE_ROOM = 6


# --------------------------------------------------------------------------
# 1. A file on disk, as data
# --------------------------------------------------------------------------

@dataclass
class PhotoFile:
    """One file the user dropped in, plus the facts we need to rename it."""

    path: Path
    taken_at: datetime
    size: int
    from_exif: bool = False

    # Derived once, in __post_init__. resolve() is a realpath syscall; strftime
    # and natural_key are pure but used to be recomputed for every row on every
    # keystroke. Caching the three here is what makes the live preview cost
    # depend on how many photos you are renaming rather than on how many files
    # happen to share the folder.
    #
    # init=False keeps the constructor signature exactly as it was, so tests
    # that build a PhotoFile for a path that does not exist still work:
    # resolve() is non-strict and returns an absolute path for a missing file.
    resolved: Path = field(init=False, repr=False, compare=False)
    date_display: str = field(init=False, repr=False, compare=False)
    sort_key: tuple = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._refresh_derived()

    def relocate(self, new_path: Path) -> None:
        """Point this file at its new name after a rename or an undo.

        Always use this instead of assigning to .path — it is what keeps the
        derived fields from silently going stale.
        """
        self.path = new_path
        self._refresh_derived()

    def set_taken_at(self, taken_at: datetime, *, from_exif: bool) -> None:
        """Record a capture date read later (the background EXIF backfill)."""
        self.taken_at, self.from_exif = taken_at, from_exif
        self._refresh_derived()

    def _refresh_derived(self) -> None:
        self.resolved = self.path.resolve()
        self.date_display = self.taken_at.strftime("%d %b %Y %H:%M")
        self.sort_key = (self.taken_at, natural_key(self.path.name))

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def stem(self) -> str:
        """File name without the extension: IMG_1234.JPG -> IMG_1234"""
        return self.path.stem

    @property
    def ext(self) -> str:
        """Extension including the dot, e.g. '.jpg'."""
        return self.path.suffix


def read_taken_at(path: Path, *, use_exif: bool = True) -> tuple[datetime, bool]:
    """Return (date the photo was taken, whether it came from EXIF).

    Falls back to the file's modified time, which is what videos and
    screenshots have instead of EXIF.

    use_exif=False skips the Pillow open entirely and goes straight to the
    modified time. That is not a second code path, just an early exit down the
    fallback this function already ends on: it lets a big drop appear in the
    window immediately while the real dates are read on a worker thread.
    """
    if use_exif and Image is not None and path.suffix.lower() in IMAGE_EXTS:
        try:
            with Image.open(path) as img:
                exif = img.getexif()
                # Real cameras write DateTimeOriginal (36867) into the Exif
                # sub-IFD (0x8769), and usually DateTime (306) into IFD0 too.
                candidates = [
                    exif.get_ifd(0x8769).get(36867),   # when the shutter fired
                    exif.get(306),                     # when the file was written
                    exif.get(36867),                   # some apps write it here
                ]
                for raw in candidates:
                    if raw:
                        text = str(raw).strip()
                        for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                            try:
                                return datetime.strptime(text, fmt), True
                            except ValueError:
                                continue
        except Exception:
            # A corrupt or unreadable header must never stop the app.
            pass
    return datetime.fromtimestamp(path.stat().st_mtime), False


def natural_key(text: str) -> list:
    """Sort key so IMG_9 comes before IMG_10 (Explorer-style ordering)."""
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", text)]


def scan_paths(paths, *, read_exif: bool = True) -> tuple[list[PhotoFile], int]:
    """Turn dropped paths (files or folders) into PhotoFiles.

    Returns (files, number_of_skipped_paths). Folders are read one level deep,
    which is what dragging a camera folder in should do.

    read_exif=False fills every date from the file's modified time, which is
    fast enough to run on the UI thread for thousands of files. The caller is
    then expected to correct those dates with backfill_exif_dates().
    """
    candidates: list[Path] = []
    skipped = 0
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            candidates.extend(sorted(c for c in p.iterdir() if c.is_file()))
        elif p.is_file():
            candidates.append(p)
        else:
            skipped += 1

    files: list[PhotoFile] = []
    seen: set[Path] = set()
    for p in candidates:
        if p.suffix.lower() not in SUPPORTED_EXTS:
            skipped += 1
            continue
        try:
            # The plain one-argument call stays the common path; only ask for
            # the skip when a worker thread is going to correct the dates after.
            taken_at, from_exif = (read_taken_at(p) if read_exif
                                   else read_taken_at(p, use_exif=False))
            size = p.stat().st_size
        except OSError:
            # Deleted, unplugged or unreadable between listing and reading it.
            skipped += 1
            continue
        # Build first, then de-duplicate on the resolved path the PhotoFile
        # already worked out, rather than paying for a second realpath here.
        photo = PhotoFile(path=p, taken_at=taken_at,
                          size=size, from_exif=from_exif)
        if photo.resolved in seen:
            continue
        seen.add(photo.resolved)
        files.append(photo)
    return files, skipped


def backfill_exif_dates(files, *, on_result, should_stop=None) -> int:
    """Read the real capture dates for files scanned with read_exif=False.

    This is the slow half of scanning — Pillow has to open every image — and
    on a couple of thousand photos it is what freezes the window. So it lives
    here, in the GUI-free core, and knows nothing about threads: the caller
    decides what to run it on and what on_result does with each answer. That
    is what makes it testable without opening a window.

    on_result(photo, taken_at, from_exif) is called once per file, in order,
    including for files that turn out to have no EXIF date at all — the caller
    needs those to show honest progress. It must not mutate `photo` itself if
    it is running off the UI thread; hand the values across and apply them
    there.

    should_stop() is polled between files, so a newer scan can abandon this
    one. Returns how many files it got through.
    """
    done = 0
    for photo in files:
        if should_stop is not None and should_stop():
            return done
        if photo.ext.lower() not in IMAGE_EXTS:
            # A video has no EXIF; the modified time it already has is all
            # there is. Say so rather than paying to find out again.
            on_result(photo, photo.taken_at, photo.from_exif)
        else:
            try:
                taken_at, from_exif = read_taken_at(photo.path)
            except OSError:
                # Unplugged or deleted since the scan. The date it already
                # has stands, and the rename will report the failure.
                taken_at, from_exif = photo.taken_at, photo.from_exif
            on_result(photo, taken_at, from_exif)
        done += 1
    return done


def sort_files(files: list[PhotoFile]) -> list[PhotoFile]:
    """Chronological order, falling back to Explorer-style name order."""
    return sorted(files, key=lambda f: f.sort_key)


# --------------------------------------------------------------------------
# 1b. Where you were, as data
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Stay:
    """One period in one place: "I was in New York City on 15 Sep"."""

    start: datetime
    end: datetime          # inclusive, so a whole day ends at 23:59:59
    place: str

    @property
    def span(self):
        return self.end - self.start


def place_for(taken_at: datetime, stays) -> str:
    """The narrowest stay containing `taken_at`, or "" if none does.

    Narrowest wins so a week in Boston and one afternoon at Fenway Park can
    both be declared: the afternoon is the more specific answer for the photos
    inside it. Ties keep the earlier stay, so the list order is a predictable
    tie-break rather than an accident.

    Pure and syscall-free: this runs once per ticked file on every keystroke.
    """
    best = None
    for stay in stays:
        if stay.start <= taken_at <= stay.end:
            if best is None or stay.span < best.span:
                best = stay
    return best.place if best is not None else ""


# The three shapes a moment may be typed in, longest first so "2026-09-15
# 13:00" is never read as a bare date with trailing rubbish.
_MOMENT_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def _parse_moment(text: str, *, label: str, end_of_day: bool) -> datetime:
    """One side of a stay, or a ValueError a human can act on.

    A bare date means the whole day: from 00:00:00, through 23:59:59. That is
    what "I was there on the 15th" means, and it is the common case — the
    time boxes exist only for the afternoon you want to name separately.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError(f"type a {label} date — use 2026-09-15")
    for fmt in _MOMENT_FORMATS:
        try:
            moment = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == "%Y-%m-%d" and end_of_day:
            return moment.replace(hour=23, minute=59, second=59)
        return moment
    raise ValueError(f"{text} is not a date — use 2026-09-15")


def parse_stay(start_text: str, end_text: str, place: str) -> Stay:
    """Build a Stay from what the user typed, or raise ValueError saying why.

    Accepts "2026-09-15" or "2026-09-15 13:00". A bare start date means from
    00:00; a bare end date means through 23:59:59.

    Every message here ends up in the window's status line, so each one reads
    as a sentence rather than a traceback.
    """
    place = (place or "").strip()
    if not place:
        raise ValueError("type a place first — New York City, say")
    start = _parse_moment(start_text, label="start", end_of_day=False)
    end = _parse_moment(end_text, label="end", end_of_day=True)
    if end < start:
        raise ValueError("the end is before the start — swap them round")
    return Stay(start=start, end=end, place=place)


# --------------------------------------------------------------------------
# 2. Patterns and tokens
# --------------------------------------------------------------------------

TOKEN_HELP = {
    "{date}": "2026-06-12  (ISO date, sorts chronologically)",
    "{date8}": "20260612  (compact ISO date)",
    "{time}": "14-22-33  (time taken)",
    "{event}": "whatever you type in the Event box",
    "{orig}": "the original file name, without extension",
    "{cam}": "last 4 digits of the original name (traces back to the camera)",
    "{n}": "sequence number, zero-padded to the Digits box",
}


def camera_id(stem: str, length: int = 4) -> str:
    """Last `length` digits found in the original name, e.g. ...142233 -> 2233.

    Keeping this fragment is standard practice: it ties the renamed file back
    to the original shot on the camera card.
    """
    digits = "".join(ch for ch in stem if ch.isdigit())
    if digits:
        return digits[-length:]
    return stem[-length:]


def expand_pattern(pattern: str, *, photo: PhotoFile, event: str,
                   index: int, digits: int) -> str:
    """Replace every {token} in `pattern`. Unknown tokens are left alone
    so a typo is visible in the preview instead of silently vanishing."""
    values = {
        "date": photo.taken_at.strftime("%Y-%m-%d"),
        "date8": photo.taken_at.strftime("%Y%m%d"),
        "time": photo.taken_at.strftime("%H-%M-%S"),
        "event": event,
        "orig": photo.stem,
        "cam": camera_id(photo.stem),
        "n": str(index).zfill(max(1, digits)),
    }
    return re.sub(r"\{(\w+)\}",
                  lambda m: values.get(m.group(1), m.group(0)),
                  pattern)


# --------------------------------------------------------------------------
# 3. Cleanup and safety
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CleanupOptions:
    """The "modern practice" switches. All on by default."""

    lowercase: bool = True
    spaces_to_hyphens: bool = True
    strip_accents: bool = True
    collapse_separators: bool = True


def apply_cleanup(stem: str, opts: CleanupOptions) -> str:
    """Make a name web-safe and consistent, per current naming guidance."""
    out = stem
    if opts.strip_accents:
        # NFKD splits "é" into "e" + accent, then we drop the accent marks.
        out = "".join(ch for ch in unicodedata.normalize("NFKD", out)
                      if not unicodedata.combining(ch))
    if opts.spaces_to_hyphens:
        out = re.sub(r"\s+", "-", out)
    if opts.lowercase:
        out = out.lower()
    if opts.collapse_separators:
        out = re.sub(r"-{2,}", "-", out)
        out = re.sub(r"_{2,}", "_", out)
        out = out.strip("-_")
    return out


def fit_stem_to_path(stem: str, *, directory: Path, ext: str) -> tuple[str, bool]:
    """Shorten `stem` until directory/stem+ext fits inside Windows' MAX_PATH.

    Returns (stem, was_truncated). A name is only ever cut from the end, and
    any separator left dangling ("holiday-") is trimmed, so the result still
    reads like a name. If the *folder* alone is already too deep there is
    nothing a shorter name can fix; we return the stem untouched and flag it,
    and the rename is refused later rather than half-done.
    """
    budget = min(
        MAX_PATH_USABLE - len(str(directory)) - 1 - len(ext) - DEDUPE_ROOM,
        MAX_NAME - len(ext) - DEDUPE_ROOM,
    )
    if budget <= 0:
        return stem, True
    if len(stem) <= budget:
        return stem, False
    return (stem[:budget].rstrip("-_ .") or "unnamed"), True


def sanitize_stem(stem: str) -> str:
    """Always-on guard: strip anything Windows refuses in a file name."""
    out = "".join(ch for ch in stem if ch not in ILLEGAL_CHARS and ord(ch) >= 32)
    out = out.rstrip(" .")  # Windows silently drops these, so do it visibly
    if out.upper().split(".")[0] in RESERVED_NAMES:
        out = "_" + out
    return out or "unnamed"


def check_convention(stem: str) -> tuple[bool, str]:
    """Quiet feedback line under the preview: (is_ok, message)."""
    if " " in stem:
        return False, "spaces in name — hyphens sort and share better"
    if any(ord(ch) > 127 for ch in stem):
        return False, "non-ASCII characters may break on other systems"
    if re.search(r"(?<!\d)\d{1,2}$", stem):
        return False, "sequence not zero-padded — 007 sorts, 7 does not"
    if any(ch.isupper() for ch in stem):
        return True, "valid, though lowercase travels better"
    if re.match(r"^\d{4}-\d{2}-\d{2}", stem) or re.match(r"^\d{8}", stem):
        return True, "ISO date-first, web-safe"
    return True, "web-safe"


# --------------------------------------------------------------------------
# 4. The three modes -> a rename plan
# --------------------------------------------------------------------------

class Mode(str, Enum):
    NEW_NAME = "new_name"
    INSERT = "insert"
    REPLACE = "replace"


class InsertPosition(str, Enum):
    BEGINNING = "beginning"
    AFTER_NTH_SEPARATOR = "after_nth_separator"
    AT_CHAR = "at_char"
    END = "end"


@dataclass
class RenameSettings:
    """Everything the UI collects, in one object."""

    mode: Mode = Mode.NEW_NAME
    # New name mode
    pattern: str = "{date}_{event}_{n}"
    event: str = ""
    start: int = 1
    digits: int = 3
    # Insert text mode
    insert_text: str = ""
    insert_position: InsertPosition = InsertPosition.BEGINNING
    separator: str = "_"
    nth: int = 1
    char_index: int = 4
    # Find & replace mode
    find: str = ""
    replace_with: str = ""
    match_case: bool = False
    # Shared
    cleanup: CleanupOptions = field(default_factory=CleanupOptions)


def insert_into_stem(stem: str, text: str, *, position: InsertPosition,
                     separator: str = "_", nth: int = 1,
                     char_index: int = 0) -> str:
    """Put `text` somewhere inside an existing name, keeping the rest intact."""
    if not text:
        return stem
    if position is InsertPosition.BEGINNING:
        return f"{text}{separator}{stem}" if separator else text + stem
    if position is InsertPosition.END:
        return f"{stem}{separator}{text}" if separator else stem + text
    if position is InsertPosition.AT_CHAR:
        cut = max(0, min(len(stem), char_index))
        return stem[:cut] + text + stem[cut:]
    # AFTER_NTH_SEPARATOR: e.g. after the 1st "_" of IMG_20260612_142233
    sep = separator or "_"
    cut = -1
    for _ in range(max(1, nth)):
        found = stem.find(sep, cut + 1)
        if found == -1:
            break
        cut = found
    if cut == -1:                      # separator not found -> append instead
        return f"{stem}{sep}{text}"
    return stem[:cut + 1] + text + sep + stem[cut + 1:]


def build_new_stem(photo: PhotoFile, settings: RenameSettings, index: int) -> str:
    """Apply the active mode, then cleanup, then the always-on sanitiser.

    How far cleanup reaches depends on the mode, which is the behaviour you
    would expect from each one:

      * New name builds the whole name from scratch, so cleanup applies to all
        of it.
      * Insert text and Find & replace promise to leave the rest of the
        original name alone, so cleanup only touches the text you typed.
    """
    if settings.mode is Mode.NEW_NAME:
        stem = expand_pattern(settings.pattern, photo=photo,
                              event=settings.event, index=index,
                              digits=settings.digits)
        stem = apply_cleanup(stem, settings.cleanup)

    elif settings.mode is Mode.INSERT:
        text = apply_cleanup(settings.insert_text, settings.cleanup)
        stem = insert_into_stem(photo.stem, text,
                                position=settings.insert_position,
                                separator=settings.separator,
                                nth=settings.nth,
                                char_index=settings.char_index)

    else:  # Mode.REPLACE
        replacement = apply_cleanup(settings.replace_with, settings.cleanup)
        if not settings.find:
            stem = photo.stem
        elif settings.match_case:
            stem = photo.stem.replace(settings.find, replacement)
        else:
            stem = re.sub(re.escape(settings.find), replacement,
                          photo.stem, flags=re.IGNORECASE)

    return sanitize_stem(stem)


@dataclass
class RenamePlan:
    """One row of the preview table."""

    photo: PhotoFile
    new_name: str
    conflict: bool = False   # had to be de-duplicated with " (1)"
    truncated: bool = False  # had to be shortened to fit Windows' MAX_PATH
    too_long: bool = False   # the folder alone is too deep - cannot be fixed

    @property
    def changed(self) -> bool:
        return self.new_name != self.photo.name

    @property
    def target(self) -> Path:
        return self.photo.path.with_name(self.new_name)


# Returned for a directory that was never listed, so names_for() can hand out
# something read-only instead of raising or quietly inserting an empty set.
_NO_NAMES: frozenset[str] = frozenset()


class DirectoryIndex:
    """Which names are already taken, per directory.

    plan_renames() runs on every keystroke. Re-listing the folder each time
    means a 3,000-photo folder costs 3,000 directory entries just to rename
    twenty files, and the cost grows with the folder rather than with the job.
    So the listing is taken once and then kept in step with our own renames.

    Names are stored lowercased because Windows compares them that way, and
    within a single directory a name is unique — which is why comparing names
    is both correct and free, where the old code resolved every entry to a
    full path first.

    This is a cache of something another program can change underneath us.
    See invalidate(), and see FotoRenamer.do_rename for where that matters.
    """

    def __init__(self) -> None:
        self._names: dict[Path, set[str]] = {}

    def ensure(self, directories) -> None:
        """List any of `directories` not seen yet. The only disk access here."""
        for directory in directories:
            if directory in self._names:
                continue
            names: set[str] = set()
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        names.add(entry.name.lower())
            except OSError:
                # Unplugged card, or a folder we are not allowed to read. An
                # empty listing just means nothing is known to be in the way.
                pass
            self._names[directory] = names

    def names_for(self, directory: Path) -> frozenset[str] | set[str]:
        """The lowercased names taken in `directory`.

        Borrowed, not copied: read it, never mutate it. Use apply_moves() to
        record a change.
        """
        return self._names.get(directory, _NO_NAMES)

    def apply_moves(self, moves) -> None:
        """Fold our own completed renames in, so the index stays true."""
        for old, new in moves:
            freed = self._names.get(old.parent)
            if freed is not None:
                freed.discard(old.name.lower())
            taken = self._names.get(new.parent)
            if taken is not None:
                taken.add(new.name.lower())

    def invalidate(self) -> None:
        """Forget everything; the next ensure() re-reads from disk."""
        self._names.clear()


def plan_renames(files: list[PhotoFile], settings: RenameSettings, *,
                 index: DirectoryIndex | None = None) -> list[RenamePlan]:
    """Build the full old -> new list, with collisions already resolved.

    `files` must already be in the order the numbers should follow
    (use sort_files).

    Pass `index` to reuse a directory listing across calls — that is what the
    live preview does, and it is the difference between a keystroke costing a
    folder scan and costing nothing. Leave it out and one is built and thrown
    away, which is exactly the old behaviour, so every existing caller and
    test keeps working unchanged.
    """
    index = index or DirectoryIndex()
    directories = {f.path.parent for f in files}
    index.ensure(directories)

    # Three name sets decide whether a candidate is free, and none of them
    # touches the disk:
    #   existing  - what the index says is already in that folder
    #   vacating  - names this batch is giving up there, so they are fair game
    #               (this is what makes the a.jpg -> b.jpg, b.jpg -> a.jpg
    #               swap work instead of colliding with itself)
    #   claimed   - names this run has already handed out
    vacating: dict[Path, set[str]] = {d: set() for d in directories}
    claimed: dict[Path, set[str]] = {d: set() for d in directories}
    for photo in files:
        vacating[photo.path.parent].add(photo.name.lower())

    plans: list[RenamePlan] = []
    for offset, photo in enumerate(files):
        stem = build_new_stem(photo, settings, settings.start + offset)
        # The extension is always lowercased: ".JPG" and ".jpg" are the same
        # file to Windows, and lowercase extensions are the universal
        # convention. The name itself is only lowercased if you ask for it.
        ext = photo.ext.lower()
        directory = photo.path.parent
        stem, truncated = fit_stem_to_path(stem, directory=directory, ext=ext)
        too_long = truncated and len(str(directory / (stem + ext))) > MAX_PATH_USABLE
        candidate = stem + ext

        existing = index.names_for(directory)
        free_again = vacating[directory]
        taken_here = claimed[directory]

        # Explorer-style de-duplication: name (1).jpg, name (2).jpg …
        # Three set lookups per try, no function call and no syscall — this
        # loop runs once per ticked file on every keystroke.
        lowered = candidate.lower()
        conflict = False
        counter = 1
        while (lowered in taken_here
               or (lowered in existing and lowered not in free_again)):
            conflict = True
            candidate = f"{stem} ({counter}){ext}"
            lowered = candidate.lower()
            counter += 1
        taken_here.add(lowered)
        plans.append(RenamePlan(photo=photo, new_name=candidate, conflict=conflict,
                                truncated=truncated and not too_long,
                                too_long=too_long))
    return plans


# --------------------------------------------------------------------------
# 5. The only code that touches disk
# --------------------------------------------------------------------------

@dataclass
class RenameResult:
    # (old name, new name) — for the status line and the file list.
    renamed: list[tuple[str, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    log_path: Path | None = None
    # The same moves as absolute paths. Names alone are ambiguous once a
    # batch spans two folders (two cards can both hold an IMG_0001.jpg),
    # so everything that has to identify a file uses these.
    moved: list[tuple[Path, Path]] = field(default_factory=list)


def app_data_dir() -> Path:
    """%LOCALAPPDATA%\\FotoRenamer on Windows, ~/.local/share/FotoRenamer elsewhere."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    directory = base / "FotoRenamer"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def history_dir() -> Path:
    directory = app_data_dir() / "history"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _temp_name(src: Path, index: int) -> Path:
    """A free, unused name to park `src` under during phase 1.

    Two things have to hold. The name must not already exist - on POSIX,
    rename() would silently destroy whatever is sitting there. And the
    resulting path must not push us past Windows' MAX_PATH: keeping the
    original name makes a crashed batch easy to recover by hand, so we do
    that when it fits and fall back to a short marker name when it does not.
    """
    directory = src.parent
    for attempt in range(1000):
        tag = f"{TEMP_SUFFIX}{index}" if attempt == 0 else f"{TEMP_SUFFIX}{index}-{attempt}"
        long_form = src.with_name(f"{src.name}{tag}")
        temp = (long_form if len(str(long_form)) <= MAX_PATH_USABLE
                else directory / tag.lstrip("."))
        if not temp.exists():
            return temp
    # 1000 collisions means something is very wrong; let rename() report it.
    return src.with_name(f"{src.name}{TEMP_SUFFIX}{index}")


def _two_phase_move(moves: list[tuple[Path, Path]]) -> RenameResult:
    """Rename via temporary names so a full reshuffle (or an A<->B swap) is safe.

    Phase 1 moves every file to a unique temp name. A file that cannot be
    moved out of the way — open in another app, read-only, or deleted since
    the preview was built — is reported and skipped, and the rest of the
    batch still completes. Phase 2 moves the temps to their targets; a
    single failure there is reported and that one file is restored.
    """
    result = RenameResult()
    staged: list[tuple[Path, Path, Path]] = []   # (temp, target, original)
    blocked: set[Path] = set()   # names a skipped file is still sitting on

    for i, (src, dst) in enumerate(moves):
        temp = _temp_name(src, i)
        try:
            src.rename(temp)
        except OSError as exc:
            result.errors.append((src.name, str(exc)))
            if src.exists():
                # Still there, just out of reach: nothing may take its name.
                blocked.add(src.resolve())
            continue
        staged.append((temp, dst, src))

    for temp, dst, original in staged:
        if dst.resolve() in blocked:
            # POSIX rename() would silently overwrite the file we could not
            # move, so refuse this one rather than destroy it.
            result.errors.append(
                (original.name,
                 f"{dst.name} is still taken by a file that could not be renamed"))
            try:
                temp.rename(original)
            except OSError:
                result.errors.append(
                    (original.name, f"is currently named {temp.name}"))
            continue
        try:
            temp.rename(dst)
            result.renamed.append((original.name, dst.name))
            result.moved.append((original, dst))
        except OSError as exc:
            # Put this one back under its original name. If even that fails
            # the file is still on disk under its temporary name, so say so
            # instead of reporting it as if nothing had happened to it.
            try:
                temp.rename(original)
                result.errors.append((original.name, str(exc)))
            except OSError:
                result.errors.append(
                    (original.name,
                     f"{exc} — the file is currently named {temp.name}"))
    return result


def apply_renames(plans: list[RenamePlan], *, write_log: bool = True) -> RenameResult:
    """Rename every plan whose name actually changes, then write an undo log."""
    result = RenameResult()
    doable = []
    for plan in plans:
        if not plan.changed:
            continue
        if plan.too_long:
            # Even an empty name would not fit. Renaming would only half-work,
            # so say why instead of letting the OS fail halfway through.
            result.errors.append(
                (plan.photo.name,
                 f"the folder path is too long for Windows "
                 f"({len(str(plan.photo.path.parent))} characters) — "
                 f"move the photos closer to the drive root"))
            continue
        doable.append((plan.photo.path, plan.target))
    if not doable:
        return result

    moved = _two_phase_move(doable)
    result.renamed.extend(moved.renamed)
    result.errors.extend(moved.errors)
    result.moved.extend(moved.moved)

    if write_log and result.moved:
        pairs = [[str(old), str(new)] for old, new in result.moved]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        log_path = history_dir() / f"{stamp}.json"
        log_path.write_text(json.dumps(
            {"timestamp": stamp, "pairs": pairs}, indent=2), encoding="utf-8")
        result.log_path = log_path
    return result


def last_log() -> Path | None:
    """Newest undo log that has not been used yet."""
    logs = sorted(history_dir().glob("*.json"))
    return logs[-1] if logs else None


def undo_last() -> RenameResult:
    """Reverse the most recent batch. Files that have since moved are skipped.

    An undo can be partly blocked - a photo may be open in another program
    right now. When that happens the log is rewritten to hold only the moves
    that still need reversing, so pressing Undo again after closing that
    program finishes the job. The log is only retired once there is genuinely
    nothing left in it to undo, which is what stops a failed undo from
    throwing away the user's only way back.
    """
    log_path = last_log()
    if log_path is None:
        return RenameResult(errors=[("", "Nothing to undo.")])

    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A truncated or hand-edited log is useless; retire it rather than
        # failing forever on the same file.
        _retire_log(log_path)
        return RenameResult(errors=[("", "The undo record is unreadable — skipped.")])

    pairs = [p for p in data.get("pairs", []) if isinstance(p, list) and len(p) == 2]
    moves: list[tuple[Path, Path]] = []
    result = RenameResult()
    for old_str, new_str in pairs:
        old, new = Path(old_str), Path(new_str)
        if new.exists():
            moves.append((new, old))
        else:
            result.errors.append((new.name, "no longer there — skipped"))

    if moves:
        moved = _two_phase_move(moves)
        result.renamed.extend(moved.renamed)
        result.errors.extend(moved.errors)
        result.moved.extend(moved.moved)

    # A file that is still sitting under its NEW name was not put back.
    remaining = [pair for pair in pairs if Path(pair[1]).exists()]
    if remaining:
        log_path.write_text(json.dumps(
            {"timestamp": data.get("timestamp", ""), "pairs": remaining}, indent=2),
            encoding="utf-8")
    else:
        _retire_log(log_path)
    result.log_path = log_path
    return result


def _retire_log(log_path: Path) -> None:
    """Mark a log as spent so the next Undo steps further back in time."""
    try:
        log_path.rename(log_path.with_suffix(".json.undone"))
    except OSError:
        pass
    _prune_history()


def _prune_history(keep: int = 200) -> None:
    """Stop the history folder growing without limit over years of use."""
    try:
        spent = sorted(history_dir().glob("*.json.undone"))
    except OSError:
        return
    for old in spent[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass
