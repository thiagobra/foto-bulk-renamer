"""Core renaming logic for Foto Renamer.

This module deliberately contains NO GUI code. Everything here is a plain
function or a small dataclass, which means every rule can be unit-tested
without opening a window (see test_renamer.py).

Reading order if you are learning the code:
    1. PhotoFile / read_taken_at ..... how a file on disk becomes data
    2. expand_pattern ................ how "{date}_{event}_{n}" becomes text
    3. apply_cleanup / sanitize_stem . how that text is made Windows/web safe
    4. plan_renames .................. old name -> new name, for the preview
    5. apply_renames / undo_last ..... the only two functions that touch disk
"""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from pathlib import Path

# Pillow is only needed to read EXIF dates and draw thumbnails. Guarding the
# import keeps this module importable (and testable) without it installed.
try:
    from PIL import Image
except ImportError:  # pragma: no cover - Pillow is a hard runtime dependency
    Image = None


# --------------------------------------------------------------------------
# What we accept
# --------------------------------------------------------------------------

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".dng"}
VIDEO_EXTS = {".mp4", ".mov", ".3gp"}
SUPPORTED_EXTS = IMAGE_EXTS | VIDEO_EXTS

# Extensions Pillow can usually show a thumbnail for without extra plugins.
THUMBNAILABLE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# Windows forbids these characters in a file name, and these device names.
ILLEGAL_CHARS = '<>:"/\\|?*'
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                  *(f"LPT{i}" for i in range(1, 10))}

TEMP_SUFFIX = ".fotoren-tmp-"


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


def read_taken_at(path: Path) -> tuple[datetime, bool]:
    """Return (date the photo was taken, whether it came from EXIF).

    Falls back to the file's modified time, which is what videos and
    screenshots have instead of EXIF.
    """
    if Image is not None and path.suffix.lower() in IMAGE_EXTS:
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


def scan_paths(paths) -> tuple[list[PhotoFile], int]:
    """Turn dropped paths (files or folders) into PhotoFiles.

    Returns (files, number_of_skipped_paths). Folders are read one level deep,
    which is what dragging a camera folder in should do.
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
        resolved = p.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        taken_at, from_exif = read_taken_at(p)
        files.append(PhotoFile(path=p, taken_at=taken_at,
                               size=p.stat().st_size, from_exif=from_exif))
    return files, skipped


def sort_files(files: list[PhotoFile]) -> list[PhotoFile]:
    """Chronological order, falling back to Explorer-style name order."""
    return sorted(files, key=lambda f: (f.taken_at, natural_key(f.name)))


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

    @property
    def changed(self) -> bool:
        return self.new_name != self.photo.name

    @property
    def target(self) -> Path:
        return self.photo.path.with_name(self.new_name)


def _existing_names(directories: set[Path], batch: set[Path]) -> dict[Path, set[str]]:
    """Names already on disk per directory, ignoring the files we are renaming.

    Windows compares names case-insensitively, so we store them lowercased.
    """
    taken: dict[Path, set[str]] = {}
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


def plan_renames(files: list[PhotoFile], settings: RenameSettings) -> list[RenamePlan]:
    """Build the full old -> new list, with collisions already resolved.

    `files` must already be in the order the numbers should follow
    (use sort_files). Nothing here touches the disk except reading directory
    listings to spot collisions.
    """
    batch = {f.path.resolve() for f in files}
    directories = {f.path.parent for f in files}
    taken = _existing_names(directories, batch)

    plans: list[RenamePlan] = []
    for offset, photo in enumerate(files):
        stem = build_new_stem(photo, settings, settings.start + offset)
        # The extension is always lowercased: ".JPG" and ".jpg" are the same
        # file to Windows, and lowercase extensions are the universal
        # convention. The name itself is only lowercased if you ask for it.
        ext = photo.ext.lower()
        candidate = stem + ext
        directory = photo.path.parent

        # Explorer-style de-duplication: name (1).jpg, name (2).jpg …
        conflict = False
        counter = 1
        while candidate.lower() in taken[directory]:
            conflict = True
            candidate = f"{stem} ({counter}){ext}"
            counter += 1
        taken[directory].add(candidate.lower())
        plans.append(RenamePlan(photo=photo, new_name=candidate, conflict=conflict))
    return plans


# --------------------------------------------------------------------------
# 5. The only code that touches disk
# --------------------------------------------------------------------------

@dataclass
class RenameResult:
    renamed: list[tuple[str, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    log_path: Path | None = None


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


def _two_phase_move(moves: list[tuple[Path, Path]]) -> RenameResult:
    """Rename via temporary names so a full reshuffle (or an A<->B swap) is safe.

    Phase 1 moves every file to a unique temp name; if that fails we put
    everything back. Phase 2 moves the temps to their targets; a single
    failure there (file locked, permission denied) is reported and that one
    file is restored, while the rest still complete.
    """
    result = RenameResult()
    staged: list[tuple[Path, Path, Path]] = []   # (temp, target, original)

    for i, (src, dst) in enumerate(moves):
        temp = src.with_name(f"{src.name}{TEMP_SUFFIX}{i}")
        try:
            src.rename(temp)
        except OSError as exc:
            for temp_path, _dst, original in staged:   # roll phase 1 back
                try:
                    temp_path.rename(original)
                except OSError:
                    pass
            result.errors.append((src.name, str(exc)))
            return result
        staged.append((temp, dst, src))

    for temp, dst, original in staged:
        try:
            temp.rename(dst)
            result.renamed.append((original.name, dst.name))
        except OSError as exc:
            result.errors.append((original.name, str(exc)))
            try:
                temp.rename(original)
            except OSError:
                pass
    return result


def apply_renames(plans: list[RenamePlan], *, write_log: bool = True) -> RenameResult:
    """Rename every plan whose name actually changes, then write an undo log."""
    moves = [(p.photo.path, p.target) for p in plans if p.changed]
    if not moves:
        return RenameResult()

    result = _two_phase_move(moves)

    if write_log and result.renamed:
        directory_by_old = {p.photo.path.name: p.photo.path.parent for p in plans}
        pairs = [[str(directory_by_old[old] / old), str(directory_by_old[old] / new)]
                 for old, new in result.renamed]
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
    """Reverse the most recent batch. Files that have since moved are skipped."""
    log_path = last_log()
    if log_path is None:
        return RenameResult(errors=[("", "Nothing to undo.")])

    data = json.loads(log_path.read_text(encoding="utf-8"))
    moves: list[tuple[Path, Path]] = []
    result = RenameResult()
    for old_str, new_str in data.get("pairs", []):
        old, new = Path(old_str), Path(new_str)
        if new.exists():
            moves.append((new, old))
        else:
            result.errors.append((new.name, "no longer there — skipped"))

    if moves:
        moved = _two_phase_move(moves)
        result.renamed.extend(moved.renamed)
        result.errors.extend(moved.errors)

    # Mark the log as spent so the next undo steps further back in time.
    log_path.rename(log_path.with_suffix(".json.undone"))
    result.log_path = log_path
    return result
