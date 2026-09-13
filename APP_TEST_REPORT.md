# Full app test — 2026-09-13

No code touched. Ran every automated check the repo has, on a fresh Linux
container (Python 3.12.3 + Tk, under `xvfb`), and eyeballed the resulting
screenshots. Goal: confirm the app is fully working right now, not to
re-run the adversarial audit already recorded in `TEST.md`.

## Setup needed first

The environment had **no runtime dependencies installed** — not even
`python3-tk`. This is expected for a fresh container, not an app defect,
but worth recording since a bare `python3 app.py` fails immediately without
it: `ModuleNotFoundError: No module named 'tkinter'`. Installed via
`apt-get install python3-tk xvfb` and `pip install pillow sv-ttk
tkinterdnd2 pillow-heif`, matching `requirements.txt` exactly.

## Results — all green, exact counts match what the repo documents

| Suite | Command | Expected (README/CLAUDE.md) | Got |
|---|---|---|---|
| Core logic | `python -m unittest discover -p "test_*.py"` | 181 tests | **181, OK** |
| Packaging | (included in discover) | 10 tests | **10, OK** |
| GUI smoke | `xvfb-run -a python tools/gui_smoke.py` | 16/16 | **16 passed, 0 failed** |
| Full GUI sweep | `xvfb-run -a python tools/gui_drive_full.py all` | 122 checks | **122 checks, 0 failed** |
| Screenshots | `xvfb-run -a python tools/gui_drive_full.py shots <dir>` | 9 images | **all 9 regenerated, no errors** |

Every feature the GUI sweep exercises came back clean, covering all three
modes and every preset, rename → undo → undo-after-restart, multi-folder
batches, a file vanishing mid-batch, a locked/read-only file, thumbnails,
keyboard shortcuts, settings persistence, the Windows path-length cap,
HEIC/HEIF, video container dates, the Place panel (trips, overlapping
stays, all four Position values), the drag-to-reorder token strip, a
corrupted `settings.json`, and an off-screen/oversized saved window
geometry. Full detail is in the tool's own output; nothing is summarized
away here because nothing failed.

I also generated the 9 documented screenshots to a scratch directory (not
committed — repo working tree stayed clean throughout) and visually
inspected two of them: the main "New name" view and the open Place panel.
Both render exactly as `README.md` describes — dark Windows-11-style
theme, correct layout, live preview text, no visual glitches or missing
assets.

## What I did not verify (best guesses, not tested)

- **Real Windows desktop behaviour** — DPI awareness, the dark title bar,
  double-clicking `run.bat`/`build_exe.bat`, the built `.exe`. This
  container has no Windows host and no display beyond Xvfb. README already
  flags this as the one open gap; nothing here changes that. My best guess:
  low risk, since `test_packaging.py` already asserts the `.bat` files'
  CRLF endings, balanced blocks, and PyInstaller data-collection flags —
  the parts that are actually easy to get wrong headlessly.
- **Drag-and-drop from a real file manager** — `tkinterdnd2` is installed
  and imports fine, and the GUI sweep drives the equivalent code path
  through `Add photos…`, but an actual OS-level drag event needs a real
  desktop session to trigger.
- **PyInstaller build itself** — `build_exe.bat` was not run (Windows-only
  tool, and building was out of scope for "test every feature," not a
  runtime feature of the app).

## Bottom line

The app is fully working. All 181 + 10 unit tests, all 16 smoke checks, and
all 122 full-sweep checks pass with the exact counts the project's own docs
claim, and the rendered screenshots look correct. Nothing here contradicts
`TEST.md`'s "all nine closed" status — this run is a confirmation, not a new
audit.
