# Architecture Plan — Foto Renamer

Name photos after where you were. One feature, plus the two date fixes it depends on.
The GUI gains one panel; the core gains one token. Everything else from the exiftool
comparison is deliberately parked, and the reasons are recorded in `## Out of scope`.

## Status: done

All seven commits landed. What the work actually produced, measured rather than
predicted:

| | Before | After |
|---|---|---|
| An edited photo's date (`CreateDate` 12 Jun, `ModifyDate` 1 Sep) | 1 Sep | **12 Jun** |
| A clip's date (real time 2026-06-12 14:22:33) | 2026-05-28 20:26:40, 15 days off | **2026-06-12 14:22:33** |
| `scan_paths`, stat calls per file (2,000 files) | 4.0 | **2.0** |
| `scan_paths`, wall time on an SSD | 62.3 ms | 62.4 ms — the saving is in the calls, which is what an SD card over USB charges for |
| Unit tests | 96 | **148** |
| GUI drive checks | 57 | **83** |

Three places the plan was wrong, and what was done instead:

- **P6 claimed no existing test needed editing.** P4's wiring contradicts
  `test_a_video_is_reported_without_being_opened` (`test_renamer.py:1009`), which
  pinned the very skip P4 removes. It was rewritten to pin the new behaviour, with a
  second test keeping the old promise that a container with nothing in it still falls
  back to the file date. That is the only existing test this work edited.
- **P3's panel does not fit.** It asks for 216 px, which at the default window size
  costs the file list five of its nine rows. It folds away behind a one-line strip,
  opening it grows the window rather than squeezing the table, and it reopens by
  itself whenever there are stays to see.
- **P1's error message example never fires.** `strptime` reads `2026-9-15` correctly,
  so refusing it would be pedantry; only text with no date in it at all is an error.
  A related gap the plan left open: an end given to the minute now runs through the
  end of that minute, so the panel can offer 23:59 as the end of a day without
  quietly dropping its last 59 seconds.

Two things from the round before still bind anything written here:

- **Anything that changes a `PhotoFile`'s path or date must go through `relocate()`
  or `set_taken_at()`**, never a bare assignment — those refresh the cached
  `resolved`, `date_display` and `sort_key`.
- **`plan_renames()` and `sort_files()` must make no syscalls** when handed a warm
  `DirectoryIndex`. `TestPreviewTouchesNoDisk` enforces it.

## Context

Renaming a trip today means typing the city into the Event box, renaming, then
re-ticking the next day's photos and doing it again — once per city. The ask is to
declare the trip once ("15 Sep I was in New York City, 16–20 Sep Boston") and have
every photo pick up its own city from its own capture date.

That makes the capture date load-bearing in a way it was not before. A wrong date was
previously a cosmetic annoyance in the `Taken` column; now it puts a photo in the wrong
city. Two findings from a comparison against **exiftool** are therefore part of this
work rather than separate from it:

- **An edited photo takes its edit date.** `read_taken_at` (`renamer.py:160-166`) tries
  `DateTimeOriginal` → `ModifyDate` → `DateTimeOriginal`, skipping `CreateDate`
  (0x9004) entirely. Verified: a photo with `CreateDate` = 12 Jun and `ModifyDate` =
  1 Sep resolves to **1 Sep**. Any photo through Google Photos, WhatsApp or a resize
  tool has a rewritten `ModifyDate`.
- **Videos have no capture date at all.** `read_taken_at` gates on `IMAGE_EXTS`
  (`renamer.py:158`), so `.mp4/.mov/.3gp` fall to modified time, which copying off a
  phone rewrites. Verified against a hand-built MP4: real time `2026-06-12 14:22:33`,
  reported `2026-05-28 20:26:40` — **15 days off**, enough to land a clip in the wrong
  city.

Intended outcome: declare a trip once, rename the whole folder in one pass, with every
photo and video carrying the right place.

## Decisions taken with the repo owner

| Decision | Choice |
|---|---|
| Place entry | Trip list — City + From/To, built up, renamed in one pass |
| Overlap | Narrowest range wins; no match leaves `{place}` empty |
| Date fixes | Folded in — a wrong date means the wrong city |
| GPS / geolocation | **Fully dropped.** Owner shoots with GPS off; "useless feature that can be fully removed to keep it simple" |
| GUI latitude | Open season — a new panel is fine |
| Presets | None added, so `TestPresets` stays untouched (see P2) |

---

## P1 — A stay is data; resolving a place is a pure function

**Problem.** The place of a photo is a function of its capture date and a list the user
typed. Nothing about that needs a window, so none of it belongs in `app.py`.

**Peer precedent.** organize's rule engine keeps `locations → filters → actions` as
plain declarative data with the matching logic separate from any front end; f2's
`internal/file.Change` likewise carries resolved facts that no later stage recomputes.

**Design.** Two additions to `renamer.py`, both GUI-free and syscall-free:

```python
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

    Narrowest wins so a week in Boston and one afternoon at Fenway Park can both be
    declared: the afternoon is the more specific answer for the photos inside it.
    Ties keep the earlier stay, so the list order is a predictable tie-break rather
    than an accident.
    """
    best = None
    for stay in stays:
        if stay.start <= taken_at <= stay.end:
            if best is None or stay.span < best.span:
                best = stay
    return best.place if best is not None else ""
```

**Cost on the hot path.** `place_for` is O(stays) per file, called from
`build_new_stem`. A trip is a handful of stays, so twenty ticked files against ten
stays is two hundred integer comparisons per keystroke — below the noise floor of the
120 ms debounce, and no syscalls, so `TestPreviewTouchesNoDisk` stays green without
special handling.

**Parsing belongs here too**, so the GUI never interprets text:

```python
def parse_stay(start_text: str, end_text: str, place: str) -> Stay:
    """Build a Stay from what the user typed, or raise ValueError saying why.

    Accepts "2026-09-15" or "2026-09-15 13:00". A bare start date means from 00:00;
    a bare end date means through 23:59:59, which is what "I was there on the 15th"
    means.
    """
```

The error message is the GUI's status line, so it must read as a sentence:
`"2026-9-15 is not a date — use 2026-09-15"`, not a traceback.

---

## P2 — `{place}` is an ordinary token; the Position dropdown edits the pattern

**Problem.** The place has to compose with everything else — the owner's words: *"not
only the city is there, but also, for example, the dates, picture number and so on."*
It also has to be movable to the beginning, middle or end.

**Design.** `{place}` is just another token. `RenameSettings` gains one field,
`expand_pattern` gains one keyword-only parameter with a default, and `build_new_stem`
resolves the place before expanding:

```python
# renamer.py — RenameSettings
stays: tuple[Stay, ...] = ()

# renamer.py — expand_pattern signature, place defaults to "" so the three
# existing TestTokens calls keep working unchanged
def expand_pattern(pattern, *, photo, event, index, digits, place=""):
    values = {..., "place": place}

# renamer.py — build_new_stem, Mode.NEW_NAME branch
stem = expand_pattern(settings.pattern, photo=photo, event=settings.event,
                      index=index, digits=settings.digits,
                      place=place_for(photo.taken_at, settings.stays))
```

**Judgement call, open to reversal:** the Position dropdown **rewrites the pattern
text** rather than introducing a placement mode. Choosing "End (suffix)" turns
`{date}_{event}_{n}` into `{date}_{event}_{n}_{place}` in the pattern box.

Three reasons this is the simpler design. The pattern stays the single source of truth,
so the preview cannot disagree with it. You can *see* where the place went, which is the
transparency this repo asks for. And it reuses the whole existing expansion path — no
new mode, no new branch in `build_new_stem`, no interaction with Insert or Find &
replace.

**A photo outside every stay** renders `{place}` as `""`. The existing
`collapse_separators` cleanup (`renamer.py:356-359`) already strips the double
underscore and any trailing one, so `2026-09-22_lakeside-wedding_007.jpg` comes out
clean with no special case.

**No new presets.** `test_every_preset_key_is_covered` (`test_renamer.py:262`) asserts
`{p.key for p in PRESETS} == set(EXPECTED)`, and `test_preset_examples_match_reality`
(264) renders each preset's `example` with no stays configured — so a place-bearing
preset would have to advertise an example with the place missing. Adding none avoids
both problems and keeps `TestPresets` byte-for-byte untouched.

---

## P3 — The Place panel

**Problem.** The trip list needs somewhere to live, and every change needs to show its
effect immediately — *"at every choice of mine, an example will be shown."*

**Design.** One new panel, built from widgets already in use elsewhere in `app.py`. No
new fonts, no new theme work: it reuses the existing `sv-ttk` styles and the same label
treatment as the preset note at `app.py:1214-1216`.

```
┌─ Place ──────────────────────────────────────────────┐
│  City   [ New York City            ▾ ]               │
│  From   [ 2026-09-15 ] [ 00:00 ]                     │
│  To     [ 2026-09-15 ] [ 23:59 ]      [  Add  ]      │
│                                                      │
│  15 Sep              New York City            ✕      │
│  16 Sep – 20 Sep     Boston                   ✕      │
│  18 Sep 13:00–18:00  Fenway Park              ✕      │
│                                                      │
│  Position  [ End (suffix)          ▾ ]               │
│                                                      │
│  2026-09-15_lakeside-wedding_001_new-york-city.jpg   │
└──────────────────────────────────────────────────────┘
```

- **City** is an editable `ttk.Combobox` whose dropdown is the cities already used, kept
  in `settings.json`. Type a new one and it joins the list.
- **From / To** are plain entries — `YYYY-MM-DD` plus an optional `HH:MM`. No
  date-picker dependency (`tkcalendar` would be a new requirement for a two-field
  form). Validation is `parse_stay`; a bad value writes the `ValueError` text to the
  status line and adds nothing.
- **The list** is a compact `ttk.Treeview` — three columns, `✕` to delete, reusing the
  click-on-first-column pattern from `_on_tree_click` (`app.py:924-931`).
- **Position** is a four-value dropdown — `Off`, `Beginning`, `After the date`,
  `End (suffix)` — that rewrites `var_pattern` per P2. `Off` removes `{place}`.
- **The example line** is the live render of the first ticked photo, updating through
  the existing debounced `schedule_preview` trace (`app.py:998-1005`). Every widget
  here gets the same `trace_add("write", …)` treatment as the current inputs, so
  nothing new is needed to make it live.

**Wiring.** Stays persist through the existing `_load_settings` / `_save_settings`
(`app.py:292`, `359`) as a list of `[start_iso, end_iso, place]` triples —
`datetime.isoformat` round-trips through JSON with no custom encoder.
`current_settings()` (`app.py:968-996`) gains `stays=tuple(self.stays)`.

**Judgement call, open to reversal:** a `Place` column is *not* added to the file
table. The preview already shows the resolved name in the `→ New name` column, so a
separate column would repeat it. If it turns out to be hard to see which photos missed
a stay, the cheapest fix is tinting rows whose place came back empty, using the tag
machinery already at `app.py:1021-1029`.

---

## P4 — Fix the date chain, then teach videos to answer

**Problem.** Both defects from `## Context`. These are the smallest items on the list
and the only ones `{place}` cannot be trusted without.

**Peer precedent.** exiftool's canonical chain is
`DateTimeOriginal → CreateDate → ModifyDate`; `Exif.pm:2256-2262` names 0x9004
`CreateDate` with the note *"called DateTimeDigitized by the EXIF spec."*

**Design — the chain.** Four lines in `read_taken_at`, hoisting the sub-IFD lookup out
of the list at the same time:

```python
sub = exif.get_ifd(0x8769)
candidates = [
    sub.get(36867),    # DateTimeOriginal - when the shutter actually fired
    sub.get(36868),    # CreateDate - when it was digitised. NEW: was skipped
    exif.get(306),     # ModifyDate - any editor rewrites this, so it goes last
    exif.get(36867),   # some apps write DateTimeOriginal at the top level
]
```

**Design — videos.** A module-level function in `renamer.py`, standard library only, no
new dependency:

```python
MP4_EPOCH_OFFSET = 2_082_844_800   # 1904-01-01 -> 1970-01-01, in seconds


def read_video_taken_at(path: Path) -> datetime | None:
    """Capture time from an MP4/MOV/3GP container, or None if it has none.

    Seeks between box headers rather than reading the file, so a 4 GB clip costs the
    same as a 4 MB one. Returns None rather than raising on anything malformed - a
    container we cannot parse is not an error, it just means the modified time is
    still the best answer we have.
    """
```

It walks top-level boxes to `moov` and prefers, in this order:

1. **`moov/udta/©day`** — an ISO 8601 string (exiftool: `ContentCreateDate`,
   `QuickTime.pm:1608-1612`). A plain string, so no epoch guesswork.
2. **`moov/mvhd`** — the integer creation time. Always present, least trustworthy, and
   the place where the real-world traps live.

Four traps the `mvhd` read must handle, all taken from exiftool's own `RawConv` at
`QuickTime.pm:1357-1372`:

- **Version 1 stores the time as `uint64`, not `uint32`**, and shifts every later field
  by 4 bytes. The version is the first payload byte.
- **`raw >= 2082844800` → subtract the offset. `raw <` it → the value is already a Unix
  timestamp.** Many encoders write the wrong epoch; exiftool warns *"Patched incorrect
  time zero for QuickTime date/time tag"* and uses the value as-is. Without this
  branch, such a file dates to 1838.
- **`raw == 0` means absent**, not 1904.
- **Box size 1 means the real 64-bit size follows the header; size 0 means the box runs
  to EOF.** A bounded box count stops a malformed file looping.

**Judgement call, open to reversal:** the Apple `moov/meta/keys` + `ilst` path is
**not** implemented. It is the only source carrying a true UTC offset
(`com.apple.quicktime.creationdate`), but it needs a two-stage index mapping from the
`keys` table to `ilst` box types (`QuickTime.pm:9790-9822`) — roughly as much code as
the rest of the reader for a refinement that does not change which *day* a clip belongs
to, which is all `{place}` needs.

**Wiring.** `read_taken_at` gains a video branch beside the image one, and
`backfill_exif_dates` loses its shortcut — the comment *"A video has no EXIF"*
(`renamer.py:256-258`) is true but was doing the wrong thing, so videos now go through
the same read as images.

**The `from_exif` field keeps its name.** It now means "came from embedded metadata",
which for a video is not EXIF. Renaming it to `from_metadata` would touch the 2-tuple
at `renamer.py:216, 262`, the 3-tuple mirrored at `app.py:830-831, 833, 839, 847`, four
unpack sites in `test_renamer.py`, plus `gui_smoke.py:86` and `gui_drive_full.py:93`.
The docstring gets corrected; the identifier does not move. Simplicity wins over
accuracy of naming here.

---

## P5 — One directory read instead of three

**Problem.** `scan_paths` (`renamer.py:198-232`) asks the filesystem for the same
directory three times per file: `iterdir()` + `is_file()`, then `read_taken_at`'s own
`path.stat()`, then `p.stat().st_size`. Measured on 2,000 files: **6,000 stat/lstat
calls, 3.0 per file, 29.4 ms.** A single `os.scandir` pass returns `is_file` + size +
mtime together and produced identical results in **16.5 ms with 0 calls**.
`DirectoryIndex` already uses `os.scandir` (`renamer.py:554`); the scan never got the
same treatment.

This is the one path the user actually waits on — `app.py:783` is the only `scan_paths`
call site and always passes `read_exif=False`, the path whose docstring promises it is
*"fast enough to run on the UI thread for thousands of files."*

**Design.** Take `is_file` and `st_size` from the `DirEntry`, dropping two of the three
reads: **3.0 → 1.0 stat calls per file.** The remaining one is `read_taken_at`'s
internal `path.stat()` for the modified-time fallback.

**Deliberately stopping at 1, not 0.** Getting to zero means passing the DirEntry's
mtime into `read_taken_at`, which changes its signature — and `TestScanResilience`
(`test_renamer.py:740-751`) patches it with `def vanishing_read(path)`,
positional-only. That test exists to prove a file vanishing mid-scan is skipped rather
than crashing; breaking it to save one syscall is the wrong trade.

**Four things the rewrite must preserve**, each pinned by an existing test:

- `os.scandir` called as a **late module attribute** (`import os` + `os.scandir(...)`,
  never `from os import scandir`) — `TestPreviewTouchesNoDisk` counts it by rebinding
  `os.scandir`.
- the `sorted()` ordering, since `scandir` yields arbitrary order and ~20 tests index
  `files[0]`.
- `read_taken_at` called **bare and positionally** as a module global, for
  `TestScanResilience`'s patch to bite.
- `skipped` counting for unsupported extensions, missing paths and `OSError`, and the
  `resolved`-based dedupe — `test_scan_skips_unsupported_and_sorts_chronologically`
  (310) and `test_scan_accepts_individual_files_and_dedupes` (319).

---

## P6 — Prove it

**Existing tests that need editing: none.** Every addition is a new field with a
default, a new keyword-only parameter with a default, or a new function. All 96 stay
green untouched. The two things that would have forced edits are avoided by design: no
new preset (P2) and no renamed `from_exif` (P4).

**One shared helper needs extending**, which is an addition, not an edit: `write_jpeg`
(`test_renamer.py:48-57`) writes tag 306 and sub-IFD 36867 to the *same* value, so it
cannot express "CreateDate present, DateTimeOriginal absent". It gains optional per-tag
keywords; existing callers are unaffected.

New coverage, in the suite's own naming style:

| Class | What it pins |
|---|---|
| `TestStays` | `parse_stay` accepts date and date+time, rejects junk with a readable message; a bare end date covers through 23:59:59 |
| `TestPlaceResolution` | narrowest stay wins; ties keep the earlier; no match gives `""`; empty place collapses cleanly through `apply_cleanup` |
| `TestPlaceToken` | `{place}` in every position; composes with `{date}`/`{n}`; unknown-token behaviour unchanged |
| `TestDateChain` | `CreateDate` beats `ModifyDate`; `DateTimeOriginal` still beats both — the regression test for the `## Context` defect |
| `TestVideoDates` | v0 and v1 `mvhd`; the Unix-epoch patch branch; `raw == 0`; `udta/©day` preferred over `mvhd`; a truncated file returns `None` |
| `TestScanSyscalls` | `scan_paths` over a warm directory makes at most one stat per file |

**Fixtures.** `tools/make_fixtures.py` already writes a deliberately fake 72-byte
`clip.mp4` (`ftypmp42` + zeros, no `moov`) — the video reader must return `None` for
it, which keeps `gui_drive_full.py`'s assertion that exactly two files date from
modified time true. Its `jpg()` helper gains the same per-tag keywords as `write_jpeg`,
and it gains one *real* MP4 with a known `mvhd` time so the GUI drive exercises a video
that does answer.

**Bench.** `tools/bench.py` gains a scan-syscall row. Its `cell()`/`row()` helpers
already handle a `None` "before" column (`bench.py:120-138`) for exactly this case — a
measurement with no old counterpart.

---

## Sequencing

Each commit left the suite green on its own. All seven landed in this order.

| # | Commit | Files | Risk |
|---|---|---|---|
| 1 | `Stay`, `parse_stay`, `place_for` + tests | `renamer.py`, `test_renamer.py` | Low — pure additions, nothing calls them yet |
| 2 | `{place}` token, `RenameSettings.stays` | `renamer.py`, `test_renamer.py` | Low — defaults keep every caller working |
| 3 | Fix the date chain | `renamer.py`, `test_renamer.py` | Low — four lines, one new candidate |
| 4 | `read_video_taken_at` + wire into scan and backfill | `renamer.py`, `test_renamer.py`, `tools/make_fixtures.py` | **Medium — the only new parser, and the only change to what an existing file resolves to** |
| 5 | The Place panel | `app.py` | Medium — one new panel, settings persistence, no existing layout moved |
| 6 | `scandir` rewrite of `scan_paths` | `renamer.py`, `test_renamer.py`, `tools/bench.py` | Low — contained, four pinned behaviours listed in P5 |
| 7 | Docs | `README.md`, `CLAUDE.md` | Low |

Commits 1–3 are independent of each other. 4 depends on nothing but is the riskiest, so
it lands with its tests and the real fixture together. 5 depends on 1 and 2. 6 is
independent of all of it and could equally land first or last.

**Docs to settle while in there.** The test count is quoted inconsistently across the
repo — README says both 76 and 96, `CLAUDE.md` says 86 and 96. Commit 7 picks the real
number and uses it everywhere. README needs the new token in the table at
`README.md:63-74`, and `## Sorting and dates` (126) needs to stop implying videos have
no readable date.

## Verification

```bash
# The suite, the way CI runs it
python -m unittest discover -v -p "test_*.py"

# The headless GUI drive, including the Place panel
xvfb-run -a python tools/gui_smoke.py
xvfb-run -a python tools/gui_drive_full.py all

# The scan-syscall row
python tools/bench.py --files 2000
```

Then by hand on Windows, which is the only place the path rules behave for real: drop a
folder spanning two days, declare two stays with an overlapping few hours, and confirm
the narrower one wins for the photos inside it; confirm a `.mov` picks up the same city
as the `.jpg` beside it; confirm the trip list survives closing and reopening the app;
and confirm Undo restores every name.

## Out of scope

GPS and reverse geocoding — **removed by request**, not deferred: the owner shoots with
GPS off, so exiftool's 3.2 MB `Geolocation.dat` would add a multi-megabyte database to a
core that imports only the standard library plus Pillow, for a tag that will never be
present · the Apple `keys`/`ilst` UTC offset path (P4) · timezone-aware `taken_at`, so
`OffsetTimeOriginal` stays unread and the cross-clock sorting quirk stays · sub-second
burst uniqueness · paired-file renaming for Live Photos, RAW+JPEG and `.xmp` sidecars,
which needs `plan_renames`' `vacating`/`claimed` sets to become group-aware and is the
largest item on the audit · `{make}`/`{model}` tokens and the one-open-per-file metadata
refactor they would need · RAW formats beyond `.dng` · verifying the `.dng` row against
a real phone file, which this environment had none of · a `Place` column in the file
table (P3) · new presets (P2) · GUI restyling beyond adding the one panel.
