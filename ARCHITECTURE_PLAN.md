# Architecture Plan — Foto Renamer

Three changes to the core, drawn from how the highest-starred open-source renamers are
built. The GUI's design, layout and behaviour are deliberately unchanged.

## Context

The live preview is the feature this app is built around: every keystroke re-plans every
name. That design is right, but the current implementation pays the filesystem for facts
that never change, so the preview gets slower the fuller your photo folder is — not the
more photos you are renaming.

Measured on 2,000 files (Linux container SSD; Windows on an SD card will be worse):

| Hot path, per keystroke | Today | After |
|---|---|---|
| `plan_renames()` | **~130 ms** | ~45 ms |
| └ `_existing_names()` — `scandir` + realpath per entry | 79 ms | **0** |
| `refresh_preview()` ticked filter — `resolve()` per file | 26 ms | **0** |
| Tree redraw `strftime()` per row | 3 ms | **0** |
| Drop 2,000 photos | window frozen while Pillow opens each one | list visible immediately |

The remaining ~45 ms is pure string work and, unlike today's cost, **does not grow with the
size of the folder**. That is the real win: the preview stops being O(folder) and becomes
O(selection).

Nothing here changes a single output name. All 66 existing tests must pass untouched at
every step — that is the correctness contract for this work.

---

## A1 — Compute-once fields on `PhotoFile`

**Problem.** `Path.resolve()` is a realpath syscall. It runs once per file *per keystroke*
in three places (`app.py:882` ticked filter, `renamer.py:472` batch set, `app.py:749`
dedupe), plus `strftime` and `natural_key` recomputed per row per redraw.

**Peer precedent.** f2's `internal/file.Change` carries `SourcePath`, `TargetPath` and
`BaseDir` resolved once by the `find` stage; no later stage touches the filesystem.

**Design.** Derive-once fields on the dataclass, with mutation funnelled through two
methods so the cache can never silently go stale.

```python
@dataclass
class PhotoFile:
    path: Path
    taken_at: datetime
    size: int
    from_exif: bool = False

    # Derived once. resolve() is a syscall; strftime and natural_key are pure
    # but ran per row per keystroke. Caching them here is what makes the live
    # preview affordable.
    resolved:     Path  = field(init=False, repr=False, compare=False)
    date_display: str   = field(init=False, repr=False, compare=False)
    sort_key:     tuple = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._refresh_derived()

    def relocate(self, new_path: Path) -> None:
        """Point this file at its new name after a rename or an undo."""
        self.path = new_path
        self._refresh_derived()

    def set_taken_at(self, taken_at: datetime, *, from_exif: bool) -> None:
        self.taken_at, self.from_exif = taken_at, from_exif
        self._refresh_derived()

    def _refresh_derived(self) -> None:
        self.resolved     = self.path.resolve()
        self.date_display = self.taken_at.strftime("%d %b %Y %H:%M")
        self.sort_key     = (self.taken_at, natural_key(self.path.name))
```

**Call sites.** `renamer.sort_files` → `key=lambda f: f.sort_key`; `scan_paths` dedupes on
`pf.resolved`; `plan_renames` batch set → `{f.resolved for f in files}`. In `app.py`:
`refresh_preview`, `add_paths`, `remove_selected` use `.resolved`; `_apply_name_changes`
uses `photo.relocate(new_path)`; the tree renders `photo.date_display`.

**Why `init=False` matters.** `test_renamer.make_photo()` builds `PhotoFile` for paths that
do not exist on disk. `init=False` + `__post_init__` keeps that constructor signature
identical, so no test changes. `resolve()` on a missing path is non-strict and returns an
absolute path without error.

**Cost.** One extra syscall per file at scan time (which already `stat`s every file), in
exchange for removing N syscalls per keystroke, forever.

---

## A2 — Inject a directory index; stop re-listing on every keystroke

**Problem.** `_existing_names()` runs `os.scandir` *and* `Path(entry.path).resolve()` for
every file **already in the folder**, on every keystroke. A 3,000-photo folder costs 3,000
realpath calls to rename 20 files. The realpath is also unnecessary: within one directory
names are unique, so comparing `entry.name.lower()` is both correct and free.

**Peer precedent.** massren keeps authoritative name state in SQLite (`history.go`) rather
than re-listing; f2's `validate` stage builds its conflict view once per run and its
`checkPathExistsConflict` compares names, not resolved paths.

**Design.** A small owned type in `renamer.py`, with the disk read as its only side effect.

```python
class DirectoryIndex:
    """Which names are already taken, per directory.

    plan_renames() runs on every keystroke, so the listing is taken once and
    kept in step with our own renames instead of being re-read each time.
    """
    def __init__(self)            -> None:      self._names: dict[Path, set[str]] = {}
    def ensure(self, directories) -> None:      ...  # scandir only dirs not seen yet
    def names_for(self, directory)-> set[str]:  ...  # borrowed, never mutated
    def apply_moves(self, moves)  -> None:      ...  # discard old.name, add new.name
    def invalidate(self)          -> None:      self._names.clear()
```

`plan_renames` gains one keyword-only argument, defaulting to today's behaviour:

```python
def plan_renames(files, settings, *, index: DirectoryIndex | None = None):
    index = index or DirectoryIndex()     # every existing caller keeps working
    index.ensure({f.path.parent for f in files})
```

Collision checking becomes three name-set lookups and **zero syscalls**, with no set copies:

- `existing` — `index.names_for(directory)`, borrowed read-only
- `vacating` — names this batch is freeing in that directory (reusable)
- `claimed` — names this plan run has already handed out

`is_taken(name) = name in claimed or (name in existing and name not in vacating)`

**Wiring in `app.py`.** `self.dir_index = renamer.DirectoryIndex()` in `__init__`;
`refresh_preview` passes `index=self.dir_index`; `_apply_name_changes` calls
`self.dir_index.apply_moves(moves)`.

**Staleness — the one place a cache must not be trusted.** If a file appears in Explorer
while the window is open, the index is stale and a collision could be missed. `do_rename`
therefore calls `self.dir_index.invalidate()` and re-plans once immediately before
`apply_renames()`, so the committed plan is always built on a fresh listing. One `scandir`
at commit time is irrelevant; one missed collision is not. Optional nicety: invalidate on
window `<FocusIn>` too.

---

## A3 — Read EXIF off the UI thread

**Problem.** `scan_paths()` opens every image with Pillow synchronously on the UI thread.
Dropping 2,000 photos freezes the window with no progress and no way to cancel.

**Peer precedent.** elodie runs exiftool as a persistent `-stay_open` subprocess
(`elodie/external/pyexiftool.py`) specifically because per-file metadata reads dominate wall
time; f2 reads EXIF lazily, only for files the `find` stage matched. Both keep the
expensive metadata read off the critical path.

**Design — reuse the machinery already in `app.py`.** `_load_thumbnail`/`_poll_thumbnails`
already implement worker-thread → `queue.Queue` → `root.after(120, …)` with a generation
token so a newer request supersedes an older one. EXIF backfill follows the same shape,
drained on the same 120 ms tick (no second timer).

1. `scan_paths(paths, *, read_exif: bool = True)` — `False` fills `taken_at` from `mtime`
   only. `read_taken_at()` already falls back to mtime, so this is a skip, not a new path.
2. New pure-core helper in `renamer.py`, testable with no window:
   `backfill_exif_dates(files, *, on_result, should_stop=...)` — the caller chooses the
   thread.
3. `add_paths` calls `scan_paths(..., read_exif=False)` → tree appears instantly → starts
   one daemon worker with an `_exif_token`.
4. Results drain into `photo.set_taken_at(...)`; status shows `reading dates… 340/2000`.
5. **On completion:** one `sort_files()` → `_populate_tree()` → `refresh_preview()`. The
   list reshuffles exactly once, into chronological order.

**Judgement call, open to reversal:** `RENAME` is **disabled while a backfill is in flight**
(button reads `reading dates…`). Loading stays instantly fast; this only blocks *committing*
numbers derived from provisional mtime order.

**CI will break without this:** `tools/gui_smoke.py:82` and ~10 call sites in
`tools/gui_drive_full.py` call `app.add_paths(...)` then assert on the preview immediately.
Both run in CI. A3 must add `app.wait_for_dates(timeout=…)` — pumps the Tk loop until the
backfill drains, matching the existing `pump(a.root)` helper — and insert it after every
`add_paths` in both scripts.

**Threading.** One worker thread first: simple, ordered, cancellable. A
`ThreadPoolExecutor(max_workers=4)` is an easy follow-up if measurement justifies it.

---

## A4 — Prove it stays fixed

f2 ships 4,850 lines of test against 8,185 of production and makes filesystem-free
assertions the default (`internal/testutil`). Two additions, matching that posture:

1. **Contract test** in `test_renamer.py` (runs in CI, Windows + Linux): monkeypatch
   `os.scandir` and `Path.resolve` with counting wrappers; assert `plan_renames(files,
   settings, index=prebuilt)` performs **zero** of each, and that `sort_files` performs zero
   `resolve()` calls. This is what stops the regression returning silently.
2. **`tools/bench.py`** — standalone, N configurable, prints the table above so the numbers
   can be measured on real Windows hardware. Not named `test_*.py`, so CI's
   `unittest discover -p "test_*.py"` ignores it; timings are machine-dependent and do not
   belong in a pass/fail gate.

New behaviour tests: index stays true across `apply_moves`; a vacating name is reusable
(the `a.jpg → b.jpg`, `b.jpg → a.jpg` swap); `invalidate()` picks up an externally created
file; backfill updates `taken_at`, `date_display` and `sort_key` together.

---

## Sequencing

Three commits, suite green at each. A1 and A2 are independent of A3 and can ship alone.

| # | Commit | Files | Risk |
|---|---|---|---|
| 1 | A1 compute-once fields | `renamer.py`, `app.py` | Low — no signature changes |
| 2 | A2 directory index + contract test | `renamer.py`, `app.py`, `test_renamer.py` | Low — opt-in argument |
| 3 | A3 threaded EXIF backfill | `renamer.py`, `app.py`, `tools/gui_*.py` | **Medium — the only one touching threading and CI scripts** |
| — | `tools/bench.py` | new file | None |

## Verification

```bash
python -m unittest discover -v -p "test_*.py"   # all 66 + new, must stay green
xvfb-run -a python tools/gui_smoke.py           # exactly what CI runs
xvfb-run -a python tools/gui_drive_full.py      # the fuller drive
python tools/bench.py --files 2000              # before/after numbers
```

Then by hand on Windows: drop a folder of 500+ photos into a folder that already holds
several thousand files, type continuously in the Event box and confirm the preview keeps
up; rename; undo; confirm names round-trip.

## Out of scope

GUI design and layout (unchanged by request) · new naming tokens or presets · EXIF format
coverage beyond Pillow + pillow-heif · a CLI entry point · splitting the `FotoRenamer` class.
