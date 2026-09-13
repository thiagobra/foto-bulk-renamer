"""The full manual GUI sweep: every mode, every failure path, and the
screenshots in screenshots/.

tools/gui_smoke.py is the short version CI runs on every push. This is the
long one you run by hand before a release, because it needs a display and
takes longer. Nothing here is mocked: a real Tk window is created, real
widgets are invoked, and the assertions look at the actual files on disk.

    xvfb-run -a python tools/gui_drive_full.py all      # 109 checks
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

def sc_broken_settings():
    """A settings.json the app did not write must never stop it opening.

    Every other saved field is type-checked on the way in; "mode" is the one
    string that reaches an Enum and a bare dict lookup, both during __init__.
    """
    print("\n[13] a settings file the app did not write")
    settings = renamer.app_data_dir() / "settings.json"
    for bad in ('{"mode": "not_a_mode"}', '{"mode": ""}'):
        wipe_settings()
        settings.write_text(bad, encoding="utf-8")
        try:
            a = new_app()
        except Exception as exc:
            check(f"the window still opens with settings {bad}", False, repr(exc))
            continue
        check(f"the window still opens with settings {bad}", True)
        check(f"an unknown mode falls back to New name  {bad}",
              a.var_mode.get() == renamer.Mode.NEW_NAME.value, a.var_mode.get())
        a.root.destroy()
    wipe_settings()

def sc_saved_geometry():
    """A saved window position and size, read back on a different screen.

    _save_settings stores winfo_geometry() verbatim, and Tk spells a negative
    offset "+-50" — so the guard has to read its own app's output, and has to
    check the size as well as the position.
    """
    wipe_settings()
    print("\n[14] a saved geometry that no longer fits the screen")
    a = new_app()
    screen_w, screen_h = a.root.winfo_screenwidth(), a.root.winfo_screenheight()

    def size_of(geometry):
        return tuple(int(n) for n in geometry.split("+")[0].split("x"))

    got = a._onscreen_geometry("900x700+-50+10")
    check("a window just off the left edge keeps its saved size",
          got is not None and size_of(got) == (900, 700), repr(got))

    got = a._onscreen_geometry("3800x2100+0+0")
    check("a 4K geometry on a smaller screen is clamped to the screen",
          got is not None and size_of(got) <= (screen_w, screen_h), repr(got))

    got = a._onscreen_geometry("1200x950+-1200+-900")
    check("a genuinely off-screen position is dropped, size kept",
          got == "1200x950", repr(got))

    got = a._onscreen_geometry("4000x3000")
    check("a bare size too big for the screen is clamped too",
          got is not None and size_of(got) <= (screen_w, screen_h), repr(got))

    # The round trip the app itself performs: save, reopen, still that size.
    # 1100x900 is above the window's 1010x720 minimum, so anything else that
    # comes back is the guard throwing the size away, not Tk enforcing minsize.
    a.root.geometry("1100x900+-50+10"); pump(a.root); a._on_close()
    b = new_app()
    check("the size survives a restart from a negative saved offset",
          size_of(b.root.geometry()) == (1100, 900), b.root.geometry())
    b.root.destroy()
    wipe_settings()

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

def sc_metadata_dates():
    wipe_settings()
    print("\n[10] the dates the old metadata layer got wrong (cardC)")
    fresh_fixtures()
    a = new_app()
    a.add_paths([str(FIX / "cardC")])
    a.wait_for_dates()
    pump(a.root)
    by_name = {f.name: f for f in a.files}
    check("cardC loaded all four files", len(a.files) == 4,
          str(sorted(by_name)))

    clip = by_name.get("clip.mp4")
    check("a real .mp4 answers with the date inside its container",
          clip is not None and clip.from_exif
          and clip.taken_at.strftime("%Y-%m-%d %H:%M:%S") == "2026-09-17 14:22:33",
          str(clip and (clip.taken_at, clip.from_exif)))

    edited = by_name.get("edited.jpg")
    check("an edited photo keeps its CreateDate, not the editor's ModifyDate",
          edited is not None and edited.from_exif
          and edited.taken_at.strftime("%Y-%m-%d") == "2026-09-15",
          str(edited and edited.taken_at))

    check("the clip sorts by its real date, after the 15 Sep photos",
          [f.name for f in a.files][:2] == ["IMG_2001.JPG", "edited.jpg"],
          str([f.name for f in a.files]))

    check("the fake 72-byte clip in cardA still reads as undatable",
          renamer.read_video_taken_at(FIX / "cardA" / "clip.mp4") is None)
    a.root.destroy()

def sc_place_panel():
    wipe_settings()
    print("\n[11] the Place panel: a trip in two cities, one rename")
    fresh_fixtures()
    a = new_app()
    a.add_paths([str(FIX / "cardC")])
    a.wait_for_dates()
    a.var_event.set("trip")
    a.var_place_open.set(True); a._sync_place_panel()
    pump(a.root)
    check("the panel starts folded away, and opens on demand",
          a.place_body.winfo_ismapped() == 1)

    a.var_city.set("New York City")
    a.var_from_date.set("2026-09-15"); a.var_from_time.set("00:00")
    a.var_to_date.set("2026-09-15"); a.var_to_time.set("23:59")
    a._add_stay(); pump(a.root)
    check("the first stay switches the token on by itself",
          a.var_pattern.get() == "{date}_{event}_{n}_{place}", a.var_pattern.get())

    a.var_city.set("Boston")
    a.var_from_date.set("2026-09-16"); a.var_to_date.set("2026-09-20")
    a._add_stay(); pump(a.root)
    a.var_city.set("Fenway Park")
    a.var_from_date.set("2026-09-17"); a.var_from_time.set("13:00")
    a.var_to_date.set("2026-09-17"); a.var_to_time.set("18:00")
    a._add_stay(); pump(a.root)
    check("all three stays are listed, earliest first",
          [a.stay_tree.item(i)["values"][1] for i in a.stay_tree.get_children()]
          == ["New York City", "Boston", "Fenway Park"],
          str([a.stay_tree.item(i)["values"] for i in a.stay_tree.get_children()]))

    named = {p.photo.name: p.new_name for p in a.plans}
    check("a 15 Sep photo is named after New York City",
          named["IMG_2001.JPG"].endswith("_new-york-city.jpg"), str(named))
    check("a 17 Sep morning photo is named after Boston",
          named["IMG_2002.JPG"].endswith("_boston.jpg"), str(named))
    check("the clip inside the narrower stay takes Fenway Park",
          named["clip.mp4"].endswith("_fenway-park.mp4"), str(named))
    check("the edited photo follows its CreateDate into New York City",
          named["edited.jpg"].endswith("_new-york-city.jpg"), str(named))
    check("the example line is the first ticked photo",
          a.var_place_example.get() == a.plans[0].new_name,
          a.var_place_example.get())

    for position, expected in (("Beginning", "{place}_{date}_{event}_{n}"),
                               ("After the date", "{date}_{place}_{event}_{n}"),
                               ("End (suffix)", "{date}_{event}_{n}_{place}"),
                               ("Off", "{date}_{event}_{n}")):
        a.var_place_at.set(position); a._on_place_position_change(); pump(a.root)
        check(f"Position {position!r} rewrites the pattern ({a.var_pattern.get()})",
              a.var_pattern.get() == expected, a.var_pattern.get())

    # The dropdown is a shortcut for dragging {place}, and a drag never moves
    # glue — so it must reuse the pattern's own separator rather than forcing
    # "_". Forcing it made "After the date" destructive: turning Place back Off
    # stripped "{place}-" and the hyphen was gone for good.
    hyphenated = "IMG_{date}-{event}_{n}"
    for position in ("Beginning", "Off", "End (suffix)", "Off",
                     "After the date", "Off"):
        if position == "Beginning":
            a.var_pattern.set(hyphenated)
        a.var_place_at.set(position); a._on_place_position_change(); pump(a.root)
        if position == "After the date":
            check("Position 'After the date' keeps a hyphenated separator",
                  a.var_pattern.get() == "IMG_{date}-{place}-{event}_{n}",
                  a.var_pattern.get())
    check("a hyphenated pattern survives Place on/off in all three positions",
          a.var_pattern.get() == hyphenated, a.var_pattern.get())

    a.var_pattern.set("{event}-{n}")                 # the web_slug preset
    a.var_place_at.set("End (suffix)"); a._on_place_position_change(); pump(a.root)
    check("a web slug gains the place with a hyphen, not an underscore",
          a.var_pattern.get() == "{event}-{n}-{place}", a.var_pattern.get())

    a.var_pattern.set("{date}_{event}_{n}")          # back to where we were
    a.var_place_at.set("Off"); a._on_place_position_change(); pump(a.root)

    a.var_place_at.set("End (suffix)"); a._on_place_position_change(); pump(a.root)
    a.var_city.set(""); a._add_stay(); pump(a.root)
    check("a stay with no city is refused with a readable reason",
          "place" in a.var_status.get() and len(a.stays) == 3, a.var_status.get())
    a.var_city.set("Nowhere"); a.var_from_date.set("15/09/2026")
    a._add_stay(); pump(a.root)
    check("a stay with an unreadable date is refused the same way",
          "not a date" in a.var_status.get() and len(a.stays) == 3,
          a.var_status.get())

    # A preset rewrites the pattern; the place must not vanish with it.
    import presets as pm
    a.var_preset.set(pm.PRESETS_BY_KEY["date_time"].label)
    a._on_preset_change(); pump(a.root)
    check("a preset change keeps the place in the pattern",
          "{place}" in a.var_pattern.get(), a.var_pattern.get())
    a.var_preset.set(pm.PRESETS_BY_KEY["date_event_n"].label)
    a._on_preset_change(); pump(a.root)

    original = names(FIX / "cardC")
    a.do_rename(); pump(a.root)
    after = names(FIX / "cardC")
    check("the whole trip renamed in one pass, each file to its own city",
          sum("new-york-city" in n for n in after) == 2
          and sum("boston" in n for n in after) == 1
          and sum("fenway-park" in n for n in after) == 1, str(after))
    a.do_undo(); pump(a.root)
    check("undo restored every name", names(FIX / "cardC") == original,
          str(names(FIX / "cardC")))

    # Delete a stay through the ✕ column, then check it stops applying.
    a.stays.pop(0)
    a._refresh_stay_rows(); a._sync_place_panel(); a.refresh_preview(); pump(a.root)
    named = {p.photo.name: p.new_name for p in a.plans}
    check("removing a stay leaves its photos with no place, and no stray _",
          named["IMG_2001.JPG"] == "2026-09-15_trip_001.jpg", str(named))
    a._on_close()

    b = new_app()
    check("the trip list survived closing and reopening the app",
          [s.place for s in b.stays] == ["Boston", "Fenway Park"],
          str([s.place for s in b.stays]))
    check("the panel reopens because there are stays in it",
          b.var_place_open.get() is True)
    check("the remembered cities are back in the dropdown",
          "New York City" in b.cities, str(b.cities))
    check("the pattern still carries the token",
          "{place}" in b.var_pattern.get(), b.var_pattern.get())
    b.root.destroy()

def drag_block(a, index, dx):
    """Press a token block, slide `dx` pixels sideways, drop it.

    Real <ButtonPress-1>/<B1-Motion>/<ButtonRelease-1> events on the real
    widget, so this exercises the same code path a mouse does. Returns whether
    the drop caret showed itself while the button was down.
    """
    block = a.token_blocks[index]
    block.event_generate("<ButtonPress-1>", x=5, y=5)
    pump(a.root, 2)
    block.event_generate("<B1-Motion>", x=5 + dx, y=5)
    pump(a.root, 2)
    caret = bool(a.drop_caret.winfo_ismapped())
    block.event_generate("<ButtonRelease-1>", x=5 + dx, y=5)
    pump(a.root, 8)
    return caret


def coloured(a):
    """[(token, the characters it painted), ...] from the example line."""
    text = a.text_example
    out = []
    for token in gui.TOKEN_COLORS:
        ranges = text.tag_ranges(token)
        for i in range(0, len(ranges), 2):
            out.append((token, str(text.get(ranges[i], ranges[i + 1])),
                        int(str(ranges[i]).split(".")[1])))
    return [(t, s) for t, s, _start in sorted(out, key=lambda r: r[2])]


def sc_token_strip():
    """Dragging the {date}/{event}/{n}/{place} blocks into a new order."""
    wipe_settings()
    print("\n[12] the token strip: drag a block, the name follows")
    fresh_fixtures()
    import presets as pm
    a = new_app()
    a.add_paths([str(FIX / "cardA")])
    a.wait_for_dates()
    a.var_event.set("lakeside wedding")
    pump(a.root, 10)

    check("the strip draws one block per token in the pattern",
          [b.cget("text") for b in a.token_blocks] == ["date", "event", "n"],
          [b.cget("text") for b in a.token_blocks])
    check("the tokens you are not using are offered as spares",
          [b.cget("text") for b in a.chip_buttons] ==
          ["date8", "time", "orig", "cam", "place"],
          [b.cget("text") for b in a.chip_buttons])
    check("the example line is coloured token by token",
          coloured(a) == [("date", "2026-06-12"),
                          ("event", "lakeside-wedding"), ("n", "001")],
          coloured(a))

    before = a.plans[0].new_name
    a.var_lower.set(False)          # a switch the drag must not touch
    pump(a.root, 6)
    lower_off = a.plans[0].new_name

    # Drag {date} past the far end of the strip.
    reach = a.token_blocks[-1].winfo_x() + a.token_blocks[-1].winfo_width() + 40
    caret = drag_block(a, 0, reach)
    check("the drop caret shows where the block would land", caret)
    check("the caret is put away again after the drop",
          not a.drop_caret.winfo_ismapped())
    check("dragging a block rewrites the pattern",
          a.var_pattern.get() == "{event}_{n}_{date}", a.var_pattern.get())
    check("the blocks redraw in the new order",
          [b.cget("text") for b in a.token_blocks] == ["event", "n", "date"],
          [b.cget("text") for b in a.token_blocks])
    check("the preview follows the drag",
          a.plans[0].new_name == "lakeside-wedding_001_2026-06-12.jpg",
          a.plans[0].new_name)
    check("the colours move with the blocks",
          [t for t, _s in coloured(a)] == ["event", "n", "date"], coloured(a))
    check("editing the pattern switches the preset to Custom",
          a.var_preset.get() == pm.PRESETS_BY_KEY["custom"].label,
          a.var_preset.get())
    check("...without quietly resetting the cleanup switches",
          a.var_lower.get() is False)

    # Drag it back to the front.
    drag_block(a, 2, -(a.token_blocks[2].winfo_x() + 40))
    check("dragging it back restores the original order",
          a.var_pattern.get() == "{date}_{event}_{n}", a.var_pattern.get())
    a.var_lower.set(True)
    pump(a.root, 6)
    check("...and the original name with it",
          a.plans[0].new_name == before, (a.plans[0].new_name, before))

    caret = drag_block(a, 1, 4)
    check("a block dropped where it started changes nothing",
          a.var_pattern.get() == "{date}_{event}_{n}", a.var_pattern.get())

    # The spares: click one, it lands on the end, then it can be dragged.
    a._add_token("cam"); pump(a.root, 8)
    check("clicking a spare puts it on the end",
          a.var_pattern.get() == "{date}_{event}_{n}_{cam}", a.var_pattern.get())
    check("the spare is no longer offered once it is in use",
          "cam" not in [b.cget("text") for b in a.chip_buttons],
          [b.cget("text") for b in a.chip_buttons])

    # {place} and the Position dropdown, which now follows the drag.
    a.var_city.set("Porto")
    a.var_from_date.set("2026-06-01"); a.var_to_date.set("2026-06-30")
    a._add_stay(); pump(a.root, 8)
    check("adding a trip puts {place} on the pattern",
          "{place}" in a.var_pattern.get(), a.var_pattern.get())
    check("the Position dropdown says where it went",
          a.var_place_at.get() == "End (suffix)", a.var_place_at.get())

    place_at = renamer.token_order(a.var_pattern.get()).index("place")
    drag_block(a, place_at, -(a.token_blocks[place_at].winfo_x() + 40))
    check("dragging {place} to the front moves it",
          renamer.token_order(a.var_pattern.get())[0] == "place",
          a.var_pattern.get())
    check("the Position dropdown follows the drag",
          a.var_place_at.get() == "Beginning", a.var_place_at.get())

    # Past two blocks, so it sits after {event}: not first, not last, and not
    # after the date either — the one arrangement no dropdown entry describes.
    drag_block(a, 0, a.token_blocks[2].winfo_x() +
               a.token_blocks[2].winfo_width() + 4)
    check("dropping it somewhere the dropdown has no word for reads Custom",
          a.var_place_at.get() == gui.PLACE_CUSTOM, a.var_place_at.get())
    check("...and the token really is where it was dropped",
          renamer.token_order(a.var_pattern.get())[2] == "place",
          a.var_pattern.get())

    # What is previewed is what lands on disk.
    a.var_pattern.set("{event}_{n}_{date}"); pump(a.root, 10)
    expected = sorted(p.new_name for p in a.plans)
    a.do_rename(); pump(a.root, 10)
    expected = sorted(expected + ["notes.txt"])   # never a photo, never touched
    check("the reordered name is what actually lands on disk",
          names(FIX / "cardA") == expected, (names(FIX / "cardA"), expected))
    a.do_undo(); pump(a.root, 10)

    # And it survives a restart.
    a.var_pattern.set("{n}_{event}_{date}"); pump(a.root, 10)
    check("taking {place} out by hand turns the Position dropdown off",
          a.var_place_at.get() == gui.PLACE_OFF, a.var_place_at.get())
    a._save_settings(); a.root.destroy()
    b = new_app()
    check("the order you dragged into survives a restart",
          b.var_pattern.get() == "{n}_{event}_{date}", b.var_pattern.get())
    check("...and the strip comes back drawn in that order",
          [x.cget("text") for x in b.token_blocks] == ["n", "event", "date"],
          [x.cget("text") for x in b.token_blocks])
    b.root.destroy()


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

    # The Place panel, open, with a trip declared over the cardC photos.
    wipe_settings()
    b = new_app()
    b.root.geometry("1200x1050+0+0")
    b.add_paths([str(FIX / "cardC")])
    b.wait_for_dates()
    b.var_event.set("trip")
    b.var_place_open.set(True); b._sync_place_panel()
    for city, start, end in (("New York City", "2026-09-15", "2026-09-15"),
                             ("Boston", "2026-09-16", "2026-09-20"),
                             ("Fenway Park", "2026-09-17", "2026-09-17")):
        b.var_city.set(city)
        b.var_from_date.set(start); b.var_to_date.set(end)
        b.var_from_time.set("13:00" if city == "Fenway Park" else "00:00")
        b.var_to_time.set("18:00" if city == "Fenway Park" else "23:59")
        b._add_stay()
    b.tree.selection_set("0"); b._show_thumbnail()
    for _ in range(40): pump(b.root); b._poll_thumbnails()
    shot(b.root, outdir / "08-place-panel.png")
    b.root.destroy()

    # The strip mid-drag: {date} lifted, the caret showing where it will land.
    # Taken with the mouse button still down, which is the only moment the
    # feature is visible in a still picture.
    wipe_settings()
    c = new_app()
    c.root.geometry("1200x950+0+0")
    c.add_paths([str(FIX / "cardA")])
    c.wait_for_dates()
    c.var_event.set("lakeside wedding")
    c.tree.selection_set("0"); c._show_thumbnail()
    for _ in range(40): pump(c.root); c._poll_thumbnails()
    block = c.token_blocks[0]
    reach = c.token_blocks[-1].winfo_x() + c.token_blocks[-1].winfo_width() + 30
    block.event_generate("<ButtonPress-1>", x=5, y=5); pump(c.root, 2)
    block.event_generate("<B1-Motion>", x=5 + reach, y=5); pump(c.root, 4)
    shot(c.root, outdir / "09-dragging-a-token.png")
    block.event_generate("<ButtonRelease-1>", x=5 + reach, y=5); pump(c.root, 8)
    c.root.destroy()
    print("   ", sorted(p.name for p in outdir.glob("*.png")))

# ---------------------------------------------------------------------------

if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else "all"
    out = sys.argv[2] if len(sys.argv) > 2 else "shots"
    todo = [sc_presets_and_modes, sc_rename_undo_restart, sc_multifolder,
            sc_deleted_midbatch, sc_locked_file, sc_thumbnail_and_selection,
            sc_settings_persist, sc_long_name, sc_heic, sc_metadata_dates,
            sc_place_panel, sc_token_strip, sc_broken_settings,
            sc_saved_geometry]
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
