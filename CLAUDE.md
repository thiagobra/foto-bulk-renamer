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
| `test_renamer.py` | 86 unit tests over the core, no window needed. |
| `tools/gui_smoke.py`, `tools/gui_drive_full.py` | Headless GUI drives. CI runs the first one. |
| `tools/bench.py` | Times the live preview against the old approach. Not a test; CI ignores it. |

## Rules that matter here

- **The core stays GUI-free.** Anything that can be tested without a window belongs in
  `renamer.py`.
- **Never break the existing tests to make a change fit.** All 96 must stay green; if a
  change needs a test edited, say so explicitly rather than quietly rewriting it.
- **The GUI's design and layout are settled.** Do not restyle, re-lay-out or "improve" the
  interface unless asked. Internal wiring changes are fine.
- **Nothing touches disk outside `apply_renames`/`undo_last`.** Planning must stay a pure
  function of (files, settings) so the live preview can run on every keystroke.
- Run `python -m unittest discover -v -p "test_*.py"` before committing.

## Current work

`ARCHITECTURE_PLAN.md` is **done** — compute-once fields, injected directory index,
threaded EXIF backfill, and the tests and bench that keep them honest. The document
carries the measured before/after numbers at the top.

Two things it leaves behind that are worth knowing before editing the core:

- **Anything that changes a `PhotoFile`'s path or date must go through `relocate()` or
  `set_taken_at()`**, never a bare assignment — those are what refresh the cached
  `resolved`, `date_display` and `sort_key`.
- **`plan_renames()` and `sort_files()` must make no syscalls** when handed a warm
  `DirectoryIndex`. `TestPreviewTouchesNoDisk` enforces that; if it fails, something
  has put the filesystem back on the per-keystroke path.
