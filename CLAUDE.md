# CLAUDE.md

Project guidance for Claude Code working in this repository.

## Workflow

**ALWAYS commit. ALWAYS merge to `main`. ALWAYS push.** This is a solo-dev repo — no pull
requests, no review gate, no waiting. Work is not finished until it is **on `main` on
GitHub**. A file sitting on a feature branch is invisible to me and does not count as
delivered.

Every piece of work ends with this sequence, every time, without being asked:

```bash
git add -A
git commit -m "<what changed and why>"
git push -u origin <working-branch>     # keep the branch current
git checkout main
git pull origin main
git merge <working-branch>              # no PR, just merge it
git push origin main                    # THIS is the step that makes it visible
git checkout <working-branch>           # carry on where you were
```

- **Never end a turn with uncommitted changes or with work stranded on a branch.**
- If the merge conflicts, resolve it and finish the merge — do not stop and leave `main` behind.
- Say explicitly which branch the work landed on, so there is never any doubt.
- Only open a PR if I explicitly ask for one.

## What this project is

A dark, Windows 11-styled desktop app for bulk-renaming phone photos, with a live preview of
every new name before anything touches disk and a one-click undo.

| File | Role |
|---|---|
| `renamer.py` | **Pure core.** Scanning, EXIF dates, pattern expansion, collision planning, the two-phase rename, the undo log. Imports no `tkinter` — keep it that way. |
| `app.py` | The Tk window. A shell over `renamer.py`; it should hold no naming logic. |
| `presets.py` | Naming presets as plain frozen dataclasses. Adding one is a two-line edit. |
| `test_renamer.py` | 164 unit tests over the core, no window needed. |
| `tools/gui_smoke.py`, `tools/gui_drive_full.py` | Headless GUI drives. CI runs the first one. |
| `tools/bench.py` | Times the live preview against the old approach. Not a test; CI ignores it. |

## Rules that matter here

- **The core stays GUI-free.** Anything that can be tested without a window belongs in
  `renamer.py`.
- **Never break the existing tests to make a change fit.** All 174 must stay green; if a
  change needs a test edited, say so explicitly rather than quietly rewriting it.
- **The GUI's design and layout are settled.** Do not restyle, re-lay-out or "improve" the
  interface unless asked. Internal wiring changes are fine.
- **`expand_with_spans` must never disagree with `build_new_stem`.** It is a
  second, slower path through expansion and cleanup that carries a token name
  beside every character, so the window can colour the example line. If the two
  ever produce different text the window drops the colours — but the real fix
  is in `renamer.py`: `TestSpansMatchTheRealName` is what catches it, and a new
  cleanup step has to be taught to `_cleanup_tagged` as well as `apply_cleanup`.
- **Nothing touches disk outside `apply_renames`/`undo_last`.** Planning must stay a pure
  function of (files, settings) so the live preview can run on every keystroke.
- Run `python -m unittest discover -v -p "test_*.py"` before committing.
- **Anything that changes what the window looks like ends with fresh screenshots.**
  A picture in `README.md` or `screenshots/` showing a window that no longer exists is
  worse than no picture. After any UI change — a new control, a new panel, a changed
  label, a different state — regenerate them and commit them with the work:

  ```bash
  xvfb-run -a python tools/gui_drive_full.py shots screenshots   # Linux / CI
  python tools\gui_drive_full.py shots screenshots               # Windows
  ```

  Then look at the result before committing it, update the table in
  `screenshots/README.md` if what a shot shows has changed, and add a scenario to
  `sc_screenshots()` for any new piece of UI worth a picture of its own.

## Current work

Nothing outstanding. The last planned round — the `{place}` token, the Place panel,
the two capture-date fixes it depended on, and the scandir rewrite of the scan — is
done and shipped. Its plan and measured outcomes were kept in `ARCHITECTURE_PLAN.md`,
now deleted as spent; read it at `git show 034f6e1:ARCHITECTURE_PLAN.md` if you ever
need the reasoning.

Since then, the **token strip**: the pattern drawn as blocks you drag into a
new order, with the example line coloured to match. Three things it left
behind:

- **The pattern string is the only source of truth for order.** A drag calls
  `renamer.move_token` and sets `var_pattern`; the strip then redraws itself
  from that text. There is no second copy of the order anywhere, which is what
  stops the strip and the pattern box drifting apart.
- **`reorder_tokens` moves tokens, never glue.** The separators are slots. This
  is the whole reason a drag lands where you expect, and it is why
  `IMG_{date}_{n}` keeps its prefix.
- **The Place panel's Position dropdown is now a readout**, recomputed from the
  pattern in `_sync_place_position` on every preview. It gained a `Custom`
  value for arrangements it has no word for. Do not set it from the drag
  handlers — one place, off the pattern, or it starts lying.

Four things the plan's own round left behind, on top of the two below:

- **A photo's date now comes from a chain, not one tag**: `DateTimeOriginal`,
  `CreateDate`, `ModifyDate` for images, and the container's own boxes for videos.
  `from_exif` means "came from embedded metadata", which for a clip is not EXIF.
- **`place_for()` runs once per ticked file on every keystroke**, so it must stay
  pure and syscall-free, like everything else on that path.
- **The Place panel folds away.** It is 216px tall, which at the default window size
  would cost the file list five of its nine rows, so opening it grows the window
  rather than squeezing the table.
- **`{place}`'s position is the pattern text**, not a mode. The Position dropdown
  rewrites `var_pattern`; nothing in `renamer.py` knows where a place goes.

The round before that — compute-once fields, the injected directory index, the
threaded EXIF backfill — is also done, and its measured before/after numbers
(an 80x preview speed-up) are in git at `git show 2625218:ARCHITECTURE_PLAN.md`. Two
things it left behind are worth knowing before editing the core:

- **Anything that changes a `PhotoFile`'s path or date must go through `relocate()` or
  `set_taken_at()`**, never a bare assignment — those are what refresh the cached
  `resolved`, `date_display` and `sort_key`.
- **`plan_renames()` and `sort_files()` must make no syscalls** when handed a warm
  `DirectoryIndex`. `TestPreviewTouchesNoDisk` enforces that; if it fails, something
  has put the filesystem back on the per-keystroke path.

## Reference repos — where the architecture ideas came from

The open-source bulk renamers this project was compared against, highest-starred first
(star counts as of September 2026). Worth knowing before proposing anything new here:
each idea below arrived from a named peer, not from taste.

- **[exiftool/exiftool](https://github.com/exiftool/exiftool)** — 5.0k ★, Perl. The metadata
  engine nearly every other photo tool shells out to; 225 format modules, renames from any tag.
- **[tfeldmann/organize](https://github.com/tfeldmann/organize)** — 3.1k ★, Python. YAML rule
  engine (locations → filters → actions) where renaming is just one action; `sim` mode, no undo.
- **[ayoisaiah/f2](https://github.com/ayoisaiah/f2)** — 2.4k ★, Go. The closest functional twin:
  dry-run by default, JSON undo backup, EXIF/ID3 template variables.
  *Source of A1* — its `internal/file.Change` resolves paths once in the `find` stage and no
  later stage touches the filesystem.
- **[jmathai/elodie](https://github.com/jmathai/elodie)** — 1.5k ★, Python. Photo library
  organizer driven by a config.ini of placeholders; checksum DB for dedupe, no undo.
  *Source of A3* — runs exiftool as a persistent `-stay_open` subprocess to keep per-file
  metadata reads off the critical path (`elodie/external/pyexiftool.py`).
- **[laurent22/massren](https://github.com/laurent22/massren)** — 1.4k ★, Go. Renames by letting
  you edit the filenames in `$EDITOR`; SQLite undo history, UUID intermediate paths for swaps.
  *Source of A2* — keeps authoritative name state in a store (`history.go`) instead of
  re-listing the directory.

Also looked at, did not make the top five:

- **[andrewning/sortphotos](https://github.com/andrewning/sortphotos)** — 1.1k ★, Python.
  Sorts photos into date-based folder hierarchies via exiftool; optional `--rename`.
- **[ivandokov/phockup](https://github.com/ivandokov/phockup)** — 1.0k ★, Python. Organizes
  into `YYYY/MM/DD` via exiftool, with checksum-based duplicate handling.
- **[microsoft/PowerToys → PowerRename](https://github.com/microsoft/PowerToys/tree/main/src/modules/powerrename)**
  — 139k ★ (whole suite), C++. The only mainstream GUI peer with the same live-preview-plus-undo
  model, built as a Windows Explorer shell extension rather than a standalone app.
