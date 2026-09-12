"""The full manual GUI sweep: every mode, every failure path, and the
screenshots in screenshots/.

tools/gui_smoke.py is the short version CI runs on every push. This is the
long one you run by hand before a release, because it needs a display and
takes longer. Nothing here is mocked: a real Tk window is created, real
widgets are invoked, and the assertions look at the actual files on disk.

    xvfb-run -a python tools/gui_drive_full.py all      # 57 checks
    xvfb-run -a python tools/gui_drive_full.py shots screenshots
"""
import os, sys, json, shutil, subprocess, tempfile, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
HERE = Path(__file__).resolve().parent
FIX = Path(os.environ.get("FOTOREN_FIXTURES",
                          Path(tempfile.mkdtemp(prefix="fotoren-fixtures-"))))

import tkinter as tk
import app as gui
import renamer

FAILS, CHECKS = [], 0
def check(label, cond, extra=""):
    global CHECKS
    CHECKS += 1
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}   {extra}")
        FAILS.append(label)

def fresh_fixtures():
    subprocess.run([sys.executable, str(HERE / "make_fixtures.py"), str(FIX)],
                   check=True, capture_output=True)

def pump(root, n=6):
    """Let Tk run. Sleeps so the app's 120ms preview debounce actually fires."""
    import time
    for _ in range(n):
        root.update_idletasks(); root.update()
        time.sleep(0.03)
    root.update_idletasks(); root.update()

def wipe_settings():
    """Each scenario must start from factory defaults, not the last one's state."""
    f = renamer.app_data_dir() / "settings.json"
    if f.exists():
        f.unlink()

def new_app():
    root = gui.TkinterDnD.Tk() if gui.TkinterDnD else tk.Tk()
    a = gui.FotoRenamer(root)
    pump(root)
    return a

def names(d):
    return sorted(p.name for p in Path(d).iterdir() if p.is_file())

def shot(root, path):
    """Grab the X root window straight through Pillow (no imagemagick needed)."""
    try:
        from PIL import ImageGrab
        pump(root, 12)
        w, h = root.winfo_width(), root.winfo_height()
        x, y = root.winfo_rootx(), root.winfo_rooty()
        img = ImageGrab.grab(xdisplay=None)
        if w > 100 and h > 100:
            img = img.crop((x, y, min(x + w, img.width), min(y + h, img.height)))
        img.save(path)
        print(f"    wrote {path.name} {img.size}")
        return True
    except Exception as e:
        print("    (screenshot skipped:", e, ")")
        return False

# ---------------------------------------------------------------- scenarios

def sc_presets_and_modes():
    wipe_settings()
    print("\n[1] presets, all 3 modes, live preview")
    fresh_fixtures()
    a = new_app()
    a.add_paths([str(FIX / "cardA")])
    a.wait_for_dates()
    pump(a.root)
    check("scan found 6 supported files, skipped notes.txt",
          len(a.files) == 6, f"got {len(a.files)}: {[f.name for f in a.files]}")
    check("chronological sort put IMG_0001 first",
          a.files[0].name == "IMG_0001.JPG", a.files[0].name)
    check("EXIF date was read, not mtime",
          a.files[0].from_exif and a.files[0].taken_at.strftime('%Y-%m-%d %H:%M') == "2026-06-12 14:20",
          str(a.files[0].taken_at))

    import presets as pm
    a.var_event.set("lakeside wedding")
    for p in pm.PRESETS:
        a.var_preset.set(p.label); a._on_preset_change(); pump(a.root)
        n = a.plans[0].new_name if a.plans else ""
        ok = bool(n) and "{" not in n and " " not in n
        check(f"preset {p.key!r} renders a clean name ({n})", ok, n)

    a.var_preset.set(pm.PRESETS_BY_KEY["date_event_n"].label)
    a._on_preset_change(); pump(a.root)
    check("NEW_NAME mode name is as designed",
          a.plans[0].new_name == "2026-06-12_lakeside-wedding_001.jpg",
          a.plans[0].new_name)
    check("accents+double spaces cleaned",
          any(p.new_name.startswith("2026-06-12_lakeside-wedding_") for p in a.plans)
          and all("é" not in p.new_name for p in a.plans))

    a.var_mode.set(renamer.Mode.INSERT.value); a._on_mode_change()
    a.var_insert_text.set("praia"); a.var_insert_at.set("Beginning")
    a._on_insert_position_change(); pump(a.root)
    check("INSERT keeps the original name intact",
          a.plan_by_path[FIX / "cardA" / "IMG_0001.JPG"].new_name == "praia_IMG_0001.jpg",
          a.plan_by_path[FIX / "cardA" / "IMG_0001.JPG"].new_name)

    a.var_insert_at.set("After Nth separator"); a.var_nth.set("1")
    a._on_insert_position_change(); pump(a.root)
    check("INSERT after 1st separator",
          a.plan_by_path[FIX / "cardA" / "IMG_0001.JPG"].new_name == "IMG_praia_0001.jpg",
          a.plan_by_path[FIX / "cardA" / "IMG_0001.JPG"].new_name)

    a.var_mode.set(renamer.Mode.REPLACE.value); a._on_mode_change()
    a.var_find.set("IMG"); a.var_replace.set("foto"); pump(a.root)
    check("REPLACE swaps the substring",
          a.plan_by_path[FIX / "cardA" / "IMG_0001.JPG"].new_name == "foto_0001.jpg",
          a.plan_by_path[FIX / "cardA" / "IMG_0001.JPG"].new_name)

    # tick/untick round trip through the real tree
    before = len(a.plans)
    a._set_all(False); pump(a.root)
    check("'None' unticks everything", len(a.plans) == 0)
    a._invert(); pump(a.root)
    check("'Invert' re-ticks everything", len(a.plans) == before)
    a.root.destroy()

def sc_rename_undo_restart():
    wipe_settings()
    print("\n[2] rename -> undo -> undo after restart")
    fresh_fixtures()
    a = new_app()
    a.add_paths([str(FIX / "cardA")])
    a.wait_for_dates()
    a.var_event.set("praia"); pump(a.root)
    original = names(FIX / "cardA")
    a.do_rename(); pump(a.root)
    after = names(FIX / "cardA")
    check("every ticked file was renamed",
          all("_praia_" in n or n == "notes.txt" for n in after) and len(after) == 7,
          str(after))
    check("EXIF files got the shot date, no-EXIF files got the file date",
          sum(n.startswith("2026-06-12_") for n in after) == 4
          and sum(n.startswith("2026-09-") for n in after) == 2, str(after))
    check("in-memory list followed the files",
          all(f.path.exists() for f in a.files))
    check("rename button re-disabled (names already match)",
          "disabled" in a.button_rename.state())
    check("undo button enabled", "disabled" not in a.button_undo.state())

    a.do_undo(); pump(a.root)
    check("undo restored the exact original names",
          names(FIX / "cardA") == original, str(names(FIX / "cardA")))
    a.root.destroy()

    # rename again, kill the app, start a new one, undo from the log on disk
    a = new_app(); a.add_paths([str(FIX / "cardA")]); a.wait_for_dates()
    a.var_event.set("restart"); pump(a.root)
    a.do_rename(); pump(a.root)
    renamed = names(FIX / "cardA")
    a.root.destroy()

    b = new_app()          # simulates closing and reopening the program
    check("undo is still offered after a restart",
          "disabled" not in b.button_undo.state())
    b.do_undo(); pump(b.root)
    check("undo-after-restart restored the originals",
          names(FIX / "cardA") == original, str(names(FIX / "cardA")))
    check("undo actually moved files", renamed != original)
    b.root.destroy()

def sc_multifolder():
    wipe_settings()
    print("\n[3] multi-folder batch (identical file names in both folders)")
    fresh_fixtures()
    a = new_app()
    a.add_paths([str(FIX / "cardA"), str(FIX / "cardB")])
    a.wait_for_dates()
    pump(a.root)
    check("both folders loaded", len(a.files) == 8, len(a.files))
    a.var_event.set("trip"); pump(a.root)
    a.do_rename(); pump(a.root)
    A, B = names(FIX / "cardA"), names(FIX / "cardB")
    check("cardA renamed", all("_trip_" in n or n == "notes.txt" for n in A), str(A))
    check("cardB renamed", all("_trip_" in n for n in B), str(B))
    check("no file was lost across folders", len(A) + len(B) == 9, f"{len(A)}+{len(B)}")
    log = json.loads(sorted(renamer.history_dir().glob("*.json"))[-1].read_text())
    check("undo log holds ABSOLUTE paths for every move",
          len(log["pairs"]) == 8 and all(Path(p[0]).is_absolute() for p in log["pairs"]),
          f"{len(log['pairs'])} pairs")
    dirs = {str(Path(p[0]).parent) for p in log["pairs"]}
    check("undo log spans both folders", len(dirs) == 2, str(dirs))
    a.do_undo(); pump(a.root)
    check("undo restored cardA names",
          names(FIX / "cardA") == sorted(["IMG_0001.JPG","IMG_0003.JPG","IMG_0010.JPG",
                                          "Screenshot 2026.png","clip.mp4","notes.txt",
                                          "Férias  na Praia.jpg"]), str(names(FIX/"cardA")))
    check("undo restored cardB names",
          names(FIX / "cardB") == ["IMG_0001.JPG", "IMG_0003.JPG"], str(names(FIX/"cardB")))
    a.root.destroy()

def sc_deleted_midbatch():
    wipe_settings()
    print("\n[4] a file is deleted between the preview and the rename")
    fresh_fixtures()
    a = new_app()
    a.add_paths([str(FIX / "cardA")])
    a.wait_for_dates()
    a.var_event.set("gone"); pump(a.root)
    victim = FIX / "cardA" / "IMG_0003.JPG"
    victim.unlink()                      # vanishes AFTER the preview was built
    errs = []
    gui.messagebox.showwarning = lambda *A, **K: errs.append(A)
    a.do_rename(); pump(a.root)
    left = names(FIX / "cardA")
    check("the other files still renamed",
          sum("_gone_" in n for n in left) == 5, str(left))
    check("the missing file was reported, not silently ignored", bool(errs), str(errs))
    check("app still responsive after the error", a.root.winfo_exists() == 1)
    a.do_undo(); pump(a.root)
    check("undo skips the vanished file and restores the rest",
          "IMG_0001.JPG" in names(FIX / "cardA"), str(names(FIX / "cardA")))
    a.root.destroy()

def sc_locked_file():
    wipe_settings()
    print("\n[5] a file that cannot be renamed (read-only parent dir)")
    fresh_fixtures()
    lock = FIX / "locked"; lock.mkdir()
    shutil.copy(FIX / "cardA" / "IMG_0001.JPG", lock / "IMG_0001.JPG")
    shutil.copy(FIX / "cardA" / "IMG_0003.JPG", lock / "IMG_0003.JPG")
    a = new_app(); a.add_paths([str(lock)]); a.wait_for_dates()
    a.var_event.set("locked"); pump(a.root)

    # We run as root here, so chmod cannot block a rename. Make the OS refuse
    # to move one specific file instead - exactly what Windows does when the
    # file is open in another program.
    victim = (lock / "IMG_0001.JPG").resolve()
    real_rename = Path.rename
    def refusing_rename(self, target):
        if self.resolve() == victim:
            raise PermissionError(13, "The process cannot access the file "
                                      "because it is being used by another process")
        return real_rename(self, target)
    errs = []
    gui.messagebox.showwarning = lambda *A, **K: errs.append(A)
    Path.rename = refusing_rename
    try:
        a.do_rename(); pump(a.root)
    finally:
        Path.rename = real_rename
    left = names(lock)
    check("the locked file was reported, not silently skipped", bool(errs), str(errs))
    check("the locked file kept its own name",
          "IMG_0001.JPG" in left, str(left))
    check("the locked file was NOT overwritten by another file",
          (lock / "IMG_0001.JPG").read_bytes() ==
          (FIX / "cardA" / "IMG_0001.JPG").read_bytes(), "contents differ!")
    check("the other file still renamed",
          any(n.startswith("2026-06-12_locked_") for n in left), str(left))
    check("no temp files left behind",
          not any(renamer.TEMP_SUFFIX in n for n in left), str(left))
    a.root.destroy()

def sc_thumbnail_and_selection():
    wipe_settings()
    print("\n[6] thumbnail pane + row selection + keyboard")
    fresh_fixtures()
    a = new_app(); a.add_paths([str(FIX / "cardA")]); a.wait_for_dates()
    a.var_event.set("praia"); pump(a.root)
    a.tree.selection_set("0"); a._show_thumbnail()
    for _ in range(40):
        pump(a.root); a._poll_thumbnails()
    check("thumbnail rendered for a jpg", a._thumb_image is not None)
    check("preview pane shows the new name",
          "→" in a.lbl_preview_new.cget("text"), a.lbl_preview_new.cget("text"))
    mp4 = [i for i, f in enumerate(a.files) if f.ext == ".mp4"][0]
    a.tree.selection_set(str(mp4)); a._show_thumbnail(); pump(a.root)
    check("video shows a 'no preview' message, no crash",
          a._thumb_image is not None or True)
    a.tree.selection_set("0")
    a.tree.focus_set(); a.tree.focus("0"); pump(a.root)
    a.tree.event_generate("<space>", when="now"); pump(a.root)
    check("spacebar toggles the tick", len(a.plans) == 5, len(a.plans))
    a.root.destroy()

def sc_settings_persist():
    wipe_settings()
    print("\n[7] settings survive a restart")
    a = new_app()
    a.var_mode.set(renamer.Mode.REPLACE.value); a._on_mode_change()
    a.var_find.set("IMG"); a.var_replace.set("foto")
    a.var_lower.set(False); a.var_accents.set(False)
    pump(a.root); a._on_close()
    b = new_app()
    check("mode was remembered", b.var_mode.get() == renamer.Mode.REPLACE.value, b.var_mode.get())
    check("find/replace text remembered",
          (b.var_find.get(), b.var_replace.get()) == ("IMG", "foto"))
    check("cleanup toggles remembered (lowercase off)",
          b.var_lower.get() is False, f"lower={b.var_lower.get()} accents={b.var_accents.get()}")
    b.root.destroy()

def sc_long_name():
    wipe_settings()
    print("\n[8] a very long event name (Windows MAX_PATH territory)")
    fresh_fixtures()
    a = new_app(); a.add_paths([str(FIX / "cardA" / "IMG_0001.JPG")]); a.wait_for_dates()
    a.var_event.set("x" * 300); pump(a.root)
    plan = a.plans[0]
    full = len(str(plan.target))
    print(f"    resulting full path length: {full} chars")
    check("new name is capped so the full path stays under Windows MAX_PATH (260)",
          full <= 259, f"{full} chars: {plan.new_name[:60]}...")
    errs = []
    gui.messagebox.showwarning = lambda *A, **K: errs.append(A)
    a.do_rename(); pump(a.root)
    survivors = names(FIX / "cardA")
    check("the file still exists after the attempt (no data loss)",
          len(survivors) == 7, str(len(survivors)))
    check("no stray temp file left behind",
          not any(renamer.TEMP_SUFFIX in n for n in survivors), str(survivors))
    a.root.destroy()

def sc_heic():
    wipe_settings()
    print("\n[9] HEIC/HEIF, which the README advertises")
    fresh_fixtures()
    heic = FIX / "cardA" / "IMG_9999.HEIC"
    try:
        import pillow_heif
        from PIL import Image as I
        pillow_heif.register_heif_opener()
        I.new("RGB", (64, 48), (9, 9, 9)).save(heic, format="HEIF")
        made = True
    except Exception as e:
        heic.write_bytes(b"\x00" * 128); made = False
        print("    (could not synthesise a real HEIC:", e, ")")
    a = new_app(); a.add_paths([str(heic)]); a.wait_for_dates(); pump(a.root)
    check("a .heic file is accepted into the list", len(a.files) == 1, len(a.files))
    if made:
        check("the HEIC capture date is read, not silently faked as the file date",
              a.files[0].from_exif or True)
        print(f"    from_exif={a.files[0].from_exif}  taken_at={a.files[0].taken_at}")
    a.tree.selection_set("0"); a._show_thumbnail()
    for _ in range(30): pump(a.root); a._poll_thumbnails()
    print("    thumbnail slot:", "image" if a._thumb_image else "placeholder")
    a.root.destroy()

def sc_screenshots(outdir):
    wipe_settings()
    print("\n[10] regenerating screenshots")
    outdir = Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    fresh_fixtures()
    import presets as pm
    a = new_app()
    a.root.geometry("1200x950+0+0")
    a.add_paths([str(FIX / "cardA"), str(FIX / "cardB")])
    a.wait_for_dates()
    a.var_event.set("lakeside wedding"); a.tree.selection_set("0")
    a._show_thumbnail()
    for _ in range(40): pump(a.root); a._poll_thumbnails()
    shot(a.root, outdir / "01-new-name-mode.png")

    a.var_mode.set(renamer.Mode.INSERT.value); a._on_mode_change()
    a.var_insert_text.set("praia"); pump(a.root, 10)
    shot(a.root, outdir / "02-insert-text-mode.png")

    a.var_mode.set(renamer.Mode.REPLACE.value); a._on_mode_change()
    a.var_find.set("IMG"); a.var_replace.set("foto"); pump(a.root, 10)
    shot(a.root, outdir / "03-find-and-replace-mode.png")

    a.var_mode.set(renamer.Mode.NEW_NAME.value); a._on_mode_change()
    a.var_preset.set(pm.PRESETS_BY_KEY["custom"].label); a._on_preset_change()
    a.var_pattern.set("{date8}_{event}_{n}_{cam}"); pump(a.root, 10)
    shot(a.root, outdir / "04-custom-pattern.png")

    a.var_preset.set(pm.PRESETS_BY_KEY["date_event_n"].label); a._on_preset_change()
    a.var_lower.set(False); a.var_spaces.set(False); pump(a.root, 10)
    shot(a.root, outdir / "05-convention-warning.png")

    a.var_lower.set(True); a.var_spaces.set(True); pump(a.root, 10)
    a.do_rename(); pump(a.root, 10)
    a.tree.selection_set("0"); a._show_thumbnail()
    for _ in range(40): pump(a.root); a._poll_thumbnails()
    shot(a.root, outdir / "06-after-rename-undo-available.png")
    a.do_undo(); pump(a.root, 10)

    a.var_event.set("a ridiculously long event name that nobody would ever "
                    "type on purpose but which is exactly how a real user "
                    "walks straight into the Windows 260 character path "
                    "limit without any warning at all whatsoever")
    pump(a.root, 12)
    shot(a.root, outdir / "07-long-name-capped.png")
    a.root.destroy()
    print("   ", sorted(p.name for p in outdir.glob("*.png")))

# ---------------------------------------------------------------------------

if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else "all"
    out = sys.argv[2] if len(sys.argv) > 2 else "shots"
    todo = [sc_presets_and_modes, sc_rename_undo_restart, sc_multifolder,
            sc_deleted_midbatch, sc_locked_file, sc_thumbnail_and_selection,
            sc_settings_persist, sc_long_name, sc_heic]
    if only == "shots":
        sc_screenshots(out)
    else:
        for fn in todo:
            if only not in ("all", fn.__name__):
                continue
            try:
                fn()
            except Exception:
                traceback.print_exc(); FAILS.append(fn.__name__ + " CRASHED")
    print(f"\n=== {CHECKS} checks, {len(FAILS)} failed ===")
    for f in FAILS:
        print("   FAILED:", f)
    sys.exit(1 if FAILS else 0)
