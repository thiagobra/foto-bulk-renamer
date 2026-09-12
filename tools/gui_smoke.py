"""Open the real Foto Renamer window and drive it, headless.

The unit suite in test_renamer.py covers the naming rules without a window.
This covers the half a window adds: that the layout actually builds, that all
three modes produce a live preview, and that a real rename can be undone.

Run it locally with:   xvfb-run -a python tools/gui_smoke.py
CI runs exactly this.  Exit code 0 means everything passed.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Keep the app's saved settings and undo history inside a throwaway folder, so
# running this never touches the real ones on the machine doing the testing.
SANDBOX = Path(tempfile.mkdtemp(prefix="fotoren-smoke-"))
os.environ["XDG_DATA_HOME"] = str(SANDBOX / "appdata")
os.environ["LOCALAPPDATA"] = str(SANDBOX / "appdata")

import tkinter as tk                                    # noqa: E402
from PIL import Image                                   # noqa: E402

import app as gui                                       # noqa: E402
import renamer                                          # noqa: E402

FAILURES: list[str] = []
PASSES = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSES
    if condition:
        PASSES += 1
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}   {detail}")
        FAILURES.append(label)


def pump(root: tk.Misc, rounds: int = 8) -> None:
    """Let Tk process events, slowly enough for the 120 ms preview debounce."""
    for _ in range(rounds):
        root.update_idletasks()
        root.update()
        time.sleep(0.03)
    root.update_idletasks()
    root.update()


def make_photos(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for index, (name, when) in enumerate((
            ("IMG_0003.JPG", "2026:06:12 14:22:33"),
            ("IMG_0001.JPG", "2026:06:12 14:20:11"),
            ("IMG_0010.JPG", "2026:06:12 14:21:05"))):
        image = Image.new("RGB", (64, 48), (index * 60, 90, 120))
        exif = image.getexif()
        exif.get_ifd(0x8769)[36867] = when       # DateTimeOriginal
        image.save(directory / name, "JPEG", exif=exif)


def main() -> int:
    photos = SANDBOX / "photos"
    make_photos(photos)
    originals = sorted(p.name for p in photos.iterdir())

    root = gui.TkinterDnD.Tk() if gui.TkinterDnD is not None else tk.Tk()
    app = gui.FotoRenamer(root)
    pump(root)
    check("the window builds without error", root.winfo_exists() == 1)

    app.add_paths([str(photos)])
    app.wait_for_dates()
    pump(root)
    check("all three photos were picked up", len(app.files) == 3, str(len(app.files)))
    check("EXIF capture dates were read, not file dates",
          all(f.from_exif for f in app.files))
    check("the list is in chronological order",
          app.files[0].name == "IMG_0001.JPG", app.files[0].name)

    app.var_event.set("smoke test")
    pump(root)
    check("New name mode previews a clean name",
          app.plans[0].new_name == "2026-06-12_smoke-test_001.jpg",
          app.plans[0].new_name)

    app.var_mode.set(renamer.Mode.INSERT.value)
    app._on_mode_change()
    app.var_insert_text.set("beach")
    pump(root)
    check("Insert mode previews and keeps the original name",
          any(p.new_name == "beach_IMG_0001.jpg" for p in app.plans),
          str([p.new_name for p in app.plans]))

    app.var_mode.set(renamer.Mode.REPLACE.value)
    app._on_mode_change()
    app.var_find.set("IMG")
    app.var_replace.set("foto")
    pump(root)
    check("Find & replace previews",
          any(p.new_name == "foto_0001.jpg" for p in app.plans),
          str([p.new_name for p in app.plans]))

    app.var_mode.set(renamer.Mode.NEW_NAME.value)
    app._on_mode_change()
    pump(root)

    app.do_rename()
    pump(root)
    renamed = sorted(p.name for p in photos.iterdir())
    check("the rename actually happened on disk",
          all(n.startswith("2026-06-12_smoke-test_") for n in renamed), str(renamed))
    check("the app's own list followed the files",
          all(f.path.exists() for f in app.files))
    check("Undo became available", "disabled" not in app.button_undo.state())

    app.do_undo()
    pump(root)
    check("undo restored the original names",
          sorted(p.name for p in photos.iterdir()) == originals,
          str(sorted(p.name for p in photos.iterdir())))

    # A name long enough to break Windows must be capped, not attempted.
    app.var_event.set("x" * 300)
    pump(root)
    longest = max(len(str(p.target)) for p in app.plans)
    check("long names are capped under the Windows path limit",
          longest <= renamer.MAX_PATH_USABLE, f"{longest} characters")

    root.destroy()
    print(f"\n{PASSES} checks passed, {len(FAILURES)} failed")
    for failure in FAILURES:
        print("  FAILED:", failure)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
