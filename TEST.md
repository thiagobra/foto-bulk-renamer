# Audit — foto-renamer-jua

Adversarial bug hunt, 2026-09-12, python3.12 on Linux (xvfb). No fixes applied.

## The three suites first

| Suite | Expected | Got |
|---|---|---|
| `python3.12 -m unittest discover -p "test_*.py"` | 174, 1 skip | **174, OK, 0 skips** |
| `xvfb-run -a python3.12 tools/gui_smoke.py` | 16/16 | **16 passed, 0 failed** |
| `xvfb-run -a python3.12 tools/gui_drive_full.py all` | 109/109 | **110 checks, 0 failed** |

No failures. Two harmless count differences, reported for completeness:

- **0 skips, not 1.** The suite needs `pillow`, `sv-ttk`, `tkinterdnd2`, `pillow-heif`
  and `python3-tk`, none of which were installed here; I installed all of them. The
  skip you expect is presumably conditional on a missing optional dependency
  (`pillow-heif`), so with everything present nothing skips. Before installing,
  `test_renamer.py` did not import at all:
  `ModuleNotFoundError: No module named 'PIL'` — 11 tests ran instead of 174.
- **110 checks, not 109.** One more assertion than the docstring in
  `tools/gui_drive_full.py:9` and `CLAUDE.md` claim. All pass; the comment is stale.

## Defects

Ordered by how much damage they do. Each repro was run.

### 1. Find & replace treats the Replace box as a regex template — a backslash crashes the live preview

`renamer.py:907`

```python
stem = re.sub(re.escape(settings.find), replacement, photo.stem, flags=re.IGNORECASE)
```

`find` is escaped; `replacement` is not. Everything Python's `re` reads as a
replacement escape is honoured, and the invalid ones raise.

**Repro** — in the window, Find & replace mode, Match case **off**, Find `IMG`,
Replace `\1`:

```
File "app.py", line 1203, in refresh_preview
    settings = self.current_settings()
File "renamer.py", line 907, in build_new_stem
    stem = re.sub(re.escape(settings.find), replacement,
re.error: invalid group reference 1 at position 1
```

`refresh_preview` runs from a Tk `after` callback, so this is an
"Exception in Tkinter callback" traceback the user never sees: the preview
simply freezes on the last good name and stays stale while they keep typing.
Confirmed against the real window (`\1`, `\`, `\g<2>` all reproduce).

**What's wrong**, three ways:

- Crashes on `\1`, `\`, `\x`, `\g<9>`, `a\3b` — anything with an invalid escape
  or group reference.
- Silently misbehaves where it doesn't crash: `\g<0>` inserts the matched text,
  `\\` collapses to one backslash.
- Disagrees with itself. `renamer.py:905` uses `str.replace` when Match case is
  **on**, which is literal. Same inputs, different answers:

  | Replace | Match case on | Match case off |
  |---|---|---|
  | `\1` | `1_1234` | **crash** |
  | `\` | `_1234` | **crash** |
  | `\N{FOO}` | `N{FOO}_1234` | **crash** |

  Only the `match_case` switch should change matching, never what the
  replacement text means.

### 2. Undo silently destroys a file sitting under the old name, and reports success

`renamer.py:1352` (`undo_last`) and `renamer.py:1267` (`_two_phase_move` phase 2)

```python
if new.exists():
    moves.append((new, old))      # nothing ever asks whether `old` is free
```

`undo_last` checks that the *source* still exists and never that the
*destination* is empty. Phase 2 then calls `temp.rename(dst)`, which on POSIX
replaces an existing file without a word. Unlike `plan_renames`, there is no
`DirectoryIndex` check and no de-duplication on the undo path at all.

**Repro**, end to end through the real window:

1. Point the app at a card, Event `trip`, press RENAME.
   `IMG_0001.JPG` → `2026-06-12_trip_001.jpg`. 6 files renamed.
2. Import again / copy a *new* `IMG_0001.JPG` onto the card by hand.
3. Press UNDO.

```
after undo: ['Férias  na Praia.jpg', 'IMG_0001.JPG', 'IMG_0003.JPG', ...]
status:     Undone — 6 files restored
```

The new `IMG_0001.JPG` is gone — overwritten by the renamed photo — and the
status line says everything was restored. **No race is needed**; step 2 is an
ordinary second import, and undo is the one feature the user is told is safe.

`app.py:1828 do_undo` calls straight into `renamer.undo_last()` with no
re-check, so nothing upstream catches it either — unlike `do_rename`
(`app.py:1808`), which deliberately re-lists and re-plans first.

On Windows `os.rename` raises `FileExistsError` instead of overwriting, so the
data loss is POSIX-only; the *silence* is on both. The undo log is still
retired (`renamer.py:1370`), so the record of what was clobbered goes too.

### 3. apply_renames overwrites an unrelated file that landed on the target after planning

`renamer.py:1267`

Same missing check on the rename side. `plan_renames` resolves collisions
against `DirectoryIndex`, but `_two_phase_move` phase 2 re-checks only
`blocked` (files that failed phase 1) — never whether `dst` exists for some
other reason.

**Repro**:

```python
files = [PhotoFile(path=d/"a.jpg", ...)]
plans = plan_renames(files, RenameSettings(pattern="shot"))
(d/"shot.jpg").write_bytes(b"PRECIOUS")   # phone sync, Dropbox, a second card
apply_renames(plans)
# -> renamed [('a.jpg','shot.jpg')], errors [], and PRECIOUS is gone
```

Narrower than #2 — `app.py:1802 do_rename` invalidates the index and re-plans
immediately before committing, so this is a TOCTOU window rather than a
reliable repro — but the comment there ("it could mean missing a collision and
overwriting someone's file") shows the risk was understood and the fix was put
only at the planning layer, not at the layer that actually writes.

### 4. A corrupt `mode` in settings.json stops the window opening at all

`app.py:1169`, reached from `app.py:1203` inside `__init__` (`app.py:273`)

```python
mode=Mode(self.var_mode.get()),
```

`_load_settings` (`app.py:353`) accepts any string for `mode` — it checks only
`isinstance(data.get(key), str)`. `__init__` then ends with `refresh_preview()`,
so an unrecognised value raises before the window is ever shown.

**Repro** — put either of these in `%LOCALAPPDATA%\FotoRenamer\settings.json`:

```json
{"mode": "not_a_mode"}
{"mode": ""}
```

```
ValueError: 'not_a_mode' is not a valid Mode
```

The app does not start. There is no in-app way to recover — the user has to
find and delete the settings file.

**What's wrong**: it breaks the loader's own stated contract. `_stays_from_saved`
(`app.py:378`) says *"a broken settings file must never stop the app opening"*,
and every other field honours that — I tried an empty file, truncated JSON, a
bare list, a string, `null`, a number, wrong types throughout, a bogus `preset`,
a bogus `insert_at`, a bogus `place_at`, huge `nth`/`char`/`digits`, and broken
`stays` rows. **All of those open fine.** `mode` is the only field with no guard,
and it is the one that is fatal.

Related: when construction dies this way it leaves a half-built widget tree with
a live 120 ms preview timer, which then fires into the dead instance from
whatever event loop runs next.

### 5. A hand-edited or truncated undo log crashes undo instead of being skipped

`renamer.py:1340`, `renamer.py:1347`, `renamer.py:1351`

`json.loads` is wrapped in `except (OSError, ValueError)`, which catches bad
*syntax* but not valid JSON of the wrong *shape*. And the `pairs` filter checks
list-ness and length but never that the two elements are strings.

**Repro** — write any of these as the newest `*.json` in
`<app data>/FotoRenamer/history/`, then press UNDO:

| Log contents | Result |
|---|---|
| `null` | `AttributeError: 'NoneType' object has no attribute 'get'` |
| `[]` | `AttributeError: 'list' object has no attribute 'get'` |
| `{"pairs": [[1,2]]}` | `TypeError: argument should be a str or an os.PathLike object ... not 'int'` |

The exception escapes `undo_last` into `app.py:1829 do_undo`, so UNDO throws
and — because the bad log is never retired — keeps throwing on every press. The
docstring at `renamer.py:1342` promises the opposite: *"A truncated or
hand-edited log is useless; retire it rather than failing forever on the same
file."* `{"pairs": "nope"}`, `{"pairs": [["a"]]}`, `""` and `{` are all handled
correctly; it is only the non-dict top level and non-string elements that get
through.

### 6. DEDUPE_ROOM is one character short — the 1000th collision overflows MAX_PATH

`renamer.py:81`, consumed at `renamer.py:1147` and `renamer.py:1164`

`fit_stem_to_path` reserves 6 characters for the de-duplication suffix, which
covers `" (999)"`. The 1000th collision needs `" (1000)"` — 7.

**Repro**: 1200 files in a deep folder, all rendering the same stem
(a pattern with no `{n}`, e.g. a long `{event}`):

```
longest full path = 260   (MAX_PATH_USABLE = 259)
offending row: ...yyyyy (1199).jpg   truncated=True  too_long=False
```

**What's wrong**: the planned path is one over the cap the code exists to
enforce, and the row is flagged `truncated` (cosmetic) rather than `too_long`
(refused), so `apply_renames` goes ahead and lets Windows fail the rename
mid-batch — exactly the outcome the `too_long` check at `renamer.py:1291` was
written to prevent. `MAX_NAME` is unaffected (240 max observed).

### 7. Place → Position "After the date" rewrites your separator, and Off cannot put it back

`app.py:1543`, with `app.py:1526 _pattern_without_place`

```python
pattern, swapped = re.subn(r"(\{date8?\})", r"\1_{place}", base, count=1)
```

The glue is hardcoded `_`, ignoring what the pattern already used. Turning
Place off then strips `{place}` *and the separator that followed it*, so the
original character is gone for good.

**Repro** — pattern `IMG_{date}-{event}_{n}`, open the Place panel:

```
Position = After the date  ->  IMG_{date}_{place}-{event}_{n}
Position = Off             ->  IMG_{date}_{event}_{n}          <- the "-" is now "_"
```

Verified in the window across `Beginning / Off / End (suffix) / Off / After the
date / Off`: the pattern comes back as `IMG_{date}_{event}_{n}`, not the
`IMG_{date}-{event}_{n}` it started as.

**What's wrong**: a read-only-looking dropdown silently edits a part of the
pattern it was not asked about, and it is not reversible in the UI. This is the
opposite of the drag path — `reorder_tokens` (`renamer.py:690`) is documented as
*"moves tokens, never glue"* precisely so `IMG_{date}_{n}` keeps its shape. The
dropdown is meant to be a shortcut for the same operation.

`Beginning` and `End (suffix)` also hardcode `_` (so `{date}-{event}-{n}`
becomes `{place}_{date}-{event}-{n}`, mixed separators), but those two do round
-trip cleanly through Off. `After the date` is the destructive one.

### 8. Tk's own geometry string is rejected, so a window near the left or top edge loses its saved size

`app.py:410`

```python
match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", geometry.strip())
```

Tk writes negative offsets as `+-N`, not `-N`. `_save_settings` (`app.py:434`)
stores `self.root.winfo_geometry()` verbatim, so the string it saves is a string
this regex cannot match.

**Repro**:

```
set +-50+10   -> winfo_geometry() = '400x300+-50+10'   regex matches: False
set +100+100  -> winfo_geometry() = '400x300+100+100'  regex matches: True
```

Both fallbacks then fail (`\d+x\d+` does not match either), so
`_onscreen_geometry` returns `None` and the whole geometry is discarded.

**What's wrong**: the function's job is to drop a bad *position* while keeping
the size (`app.py:418` returns `f"{width}x{height}"` for exactly that). Here it
throws away the size too, so a user whose window sits even 50px off the left or
top edge gets the default 1200x950 back on every single launch, forever.

### 9. Saved geometry is checked for position but never for size

`app.py:402-418`

The bounds check only looks at `x` and `y`. `width` and `height` are parsed and
passed through untouched.

**Repro** — `{"geometry": "3800x2100+0+0"}` in settings.json, opened on a
1366x768 screen:

```
screen: 1366 x 768
saved 3800x2100+0+0 -> window is now 3800x2100+0+0
_onscreen_geometry returned: 3800x2100+0+0
```

**What's wrong**: this is the same scenario the function was written for — the
docstring says *"Windows users unplug the second monitor the app was last on"* —
and moving from a 4K monitor to a laptop panel is the commonest form of it. The
window opens larger than the screen, and because the RENAME button sits at the
bottom of the layout (`app.py:924 _build_footer`) it is off-screen with no way
to reach it. A deliberately absurd `99999x99999` took the X server down here
with `BadAlloc (X_CreatePixmap)`; the realistic 3800x2100 case just makes the
app unusable.

## Where I found nothing

Probed hard, came up clean. Listed so the coverage is on the record.

**`expand_with_spans` vs `build_new_stem`** — the invariant holds.
**63,180 combinations** checked: every 0/1/2/3-token permutation of all eight
tokens in `TOKEN_HELP`, crossed with three glues, four `CleanupOptions`
settings, six Event strings (including accented, all-spaces, `___`, `...`) and
six original names (including `CON.jpg`, `é.jpg`, `....jpg`). Plus 18 hand-picked
odd patterns: empty, pure glue (`___`), a single token, duplicates (`{n}_{n}`),
unknown (`{dat}`, `{}`, `{DATE}`, `{ n }`), unbalanced (`{date}{`, `}{`,
`{{n}}`), literals and mixed separators. Zero disagreements, zero crashes, every
span inside bounds. Also re-checked through the live window across all nine
presets and every drag below.

**The token strip.** Every block dragged to every slot for 10 patterns,
including one-token, `{n}_{n}`, `IMG_{date}_{n}` and `x{date}y{event}z{n}w`,
plus out-of-range indices from -2 to k+2. No token lost or duplicated, glue
never moved, `split_pattern` stayed lossless, out-of-range drops returned the
pattern untouched, and every result matched the expected permutation. Through
the real window: drops at x = -100000, -1, 0, 5, 100000 — off both ends of the
strip — never crashed and never lost a token; an empty pattern draws 0 blocks
and 8 spares, `_drag_motion` with no blocks is a no-op, and clicking a spare on
an empty pattern gives `{date}`.

**Place.** Narrowest stay wins over a nested one; identical stays tie-break to
the earlier (`Narrow` beat `Same`); adjacent day boundaries land on the right
side; a photo outside every stay renders `""` and the leftover separator is
cleaned up; a zero-span stay still matches its instant; end-before-start and an
empty place are both rejected with a readable message. The Position dropdown
round-trips all five values, follows a dragged `{place}` through every
from/to pair (I asserted the readout against an independently computed
expectation), reads `Custom` for arrangements it has no word for, and goes
`Off` when `{place}` is deleted by hand. `After the date` with no date token
falls back to Beginning and says so. (The separator damage is #7; placement
itself is correct.)

**Dates.** The EXIF chain resolves in the documented order — `edited.jpg`, which
carries CreateDate 15 Sep and ModifyDate 1 Nov, correctly reads 15 Sep 12:00.
MP4 `mvhd` v0 and v1 both decode to `2026-09-17 14:22:33`; `©day` reads with and
without the 4-byte text header, in `T`, space and colon forms, and date-only.
Garbage and impossible `©day` payloads (`2026-13-45`) fall through to `mvhd`
rather than raising. Every malformed container returned `None`: empty file, 8
zero bytes, ftyp only, truncated moov, size-0 moov, 4 KB of `urandom`, a
`0xFFFFFFFF` size, and 300 chained empty `moov` boxes (0.000 s — `MAX_BOXES`
holds). `mvhd` 0 → `None`, a Unix-epoch value → patched as documented. HEIC
without pillow-heif and a PNG with no EXIF both fall back to mtime;
`HEIC_SUPPORT` reports honestly.

**Disk.** Two files rendering one name de-duplicate to `shot.jpg` /
`shot (1).jpg` and undo restores both. `a→b, b→a` swaps contents correctly via
the temp phase and undoes. A case-only rename `A.JPG → a.jpg` is not flagged as
a collision with itself and lands. Two folders holding the same `IMG_0001.JPG`
both rename with no false conflict. A file deleted between preview and commit is
reported and the other two still go through, with no temp files left behind. A
file that refuses to move (simulated `PermissionError`, since running as root
makes a read-only directory no obstacle) is reported, left untouched, and the
rest of the batch completes; in the `a↔b` swap where `a` cannot move, the
`blocked` path correctly refuses rather than destroying it and restores `b`.
The MAX_PATH cap holds at 253/259 for a normal long name; a genuinely too-deep
folder sets `too_long` and the rename is refused with a readable message rather
than half-done. No undo log is written when zero files moved. `CON`, `NUL`,
`COM1`, `LPT9`, `con.tar.gz` and `  CON  ` all get the `_` prefix; `CONS` and a
planned Event of `CON` are handled. Undo ran clean after every one of these.
(The exceptions are #2, #3 and #6.)

**Modes, empty and out-of-range inputs.** Through the real window: empty Find,
empty Replace, empty Insert text, empty pattern, empty separator; regex
metacharacters in Find (`.`, `(`, `[a-z]`, `\`, `*`) are all escaped correctly
and match literally; nth = ``, `0`, `-3`, `99`, `abc`, `1e9`; char index = ``,
`-5`, `0`, `9999`, `xyz`; start = ``, `-5`, `0`, `abc`, a 20-digit number;
digits = ``, `0`, `-2`, `abc`, `300`; separators ``, `_`, `###`. nth past the
last separator appends instead, a char index beyond the name clamps to the end,
a negative one clamps to the start, and a negative start is floored at 0 so no
stray hyphen appears. Every combination previewed without an exception. (The one
crash is #1, and it is in the Replace box, not the Find box.)

**Invariants.** `renamer.py` contains no occurrence of the string `tkinter`.
With a warm `DirectoryIndex`, `plan_renames` and `sort_files` across 5 patterns
and 20 files made **zero** syscalls — I counted by wrapping `os.scandir` and
`Path.resolve` / `.stat` / `.exists`. Nothing wrote to disk during previewing: I
wrapped `builtins.open` (w/x/a/+ modes) and `Path.rename` / `write_text` /
`write_bytes` / `mkdir` / `unlink` / `touch` / `replace` and then drove 5 pattern
changes, all three modes, tick-all / invert, the Place panel opening and closing,
and a `<FocusIn>` index invalidation. Zero writes.

**Settings round-trip.** Pattern, event, start, digits, mode, both cleanup
switches, the stay list, the learned cities and the Place position all
round-tripped through save-and-reopen unchanged.

## Not a defect (checked, discarded)

- `_photo_for_row("")` raises `ValueError` (`app.py:1114`), but no UI path
  reaches it: `_on_tree_click` guards `if iid:` (`app.py:1123`) and
  `_toggle_selected_rows` passes only real row ids from `tree.selection()`.
- `{n}` numbering is per batch, not per folder, so two folders in one run get
  `001` and `002`. That is the documented design, not a collision bug.
- `mvhd` values between the 1904 and 1970 epochs are genuinely ambiguous;
  `_mp4_epoch_to_datetime` (`renamer.py:254`) applies exiftool's own heuristic
  and says so.
- A backwards stay (end before start) survives `_stays_from_saved`
  (`app.py:378`) where `parse_stay` would reject it, but it is inert: its
  `start <= taken_at <= end` test can never pass.
