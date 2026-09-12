# CLAUDE.md

Project guidance for Claude Code working in this repository.

## Workflow

**Always commit and push what you do.** This is a solo-dev repo — no pull requests, no
review gate. Finish a piece of work, commit it with a clear message, push it to the working
branch. Do not leave changes sitting uncommitted in the working tree and do not wait to be
asked.

- Push with `git push -u origin <branch>`.
- Commit messages: say what changed and why, in plain language.
- Only open a PR if explicitly asked for one.

## What this project is

A dark, Windows 11-styled desktop app for bulk-renaming phone photos, with a live preview of
every new name before anything touches disk and a one-click undo.

| File | Role |
|---|---|
| `renamer.py` | **Pure core.** Scanning, EXIF dates, pattern expansion, collision planning, the two-phase rename, the undo log. Imports no `tkinter` — keep it that way. |
| `app.py` | The Tk window. A shell over `renamer.py`; it should hold no naming logic. |
| `presets.py` | Naming presets as plain frozen dataclasses. Adding one is a two-line edit. |
| `test_renamer.py` | 66 unit tests over the core, no window needed. |
| `tools/gui_smoke.py`, `tools/gui_drive_full.py` | Headless GUI drives. CI runs the first one. |

## Rules that matter here

- **The core stays GUI-free.** Anything that can be tested without a window belongs in
  `renamer.py`.
- **Never break the existing tests to make a change fit.** All 66 must stay green; if a
  change needs a test edited, say so explicitly rather than quietly rewriting it.
- **The GUI's design and layout are settled.** Do not restyle, re-lay-out or "improve" the
  interface unless asked. Internal wiring changes are fine.
- **Nothing touches disk outside `apply_renames`/`undo_last`.** Planning must stay a pure
  function of (files, settings) so the live preview can run on every keystroke.
- Run `python -m unittest discover -v -p "test_*.py"` before committing.

## Current work

See `ARCHITECTURE_PLAN.md` for the agreed three-part core refactor (compute-once fields,
injected directory index, threaded EXIF backfill) and its verification plan.
