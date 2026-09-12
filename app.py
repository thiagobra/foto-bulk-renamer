"""Foto Renamer — a dark, Windows 11-styled bulk photo renamer.

Run it with:   python app.py
Build the exe: build_exe.bat

All the naming rules live in renamer.py and presets.py; this file is only the
window. If you are reading the code to learn, the interesting methods are:

    _build_ui()          the whole layout, top to bottom
    refresh_preview()    recalculates every "new name" cell as you type
    do_rename()          the one button that touches your files
"""

from __future__ import annotations

import json
import queue
import re
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import sv_ttk
from PIL import Image, ImageTk

import presets as preset_module
import renamer
from renamer import (CleanupOptions, InsertPosition, Mode, RenameSettings,
                     SUPPORTED_EXTS)

# Optional: drag & drop from Explorer. The app still works without it.
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:  # pragma: no cover
    TkinterDnD = None
    DND_FILES = None


# --------------------------------------------------------------------------
# Palette — Windows 11 dark, with the system accent blue
# --------------------------------------------------------------------------

ACCENT = "#4cc2ff"
BG = "#1c1c1c"
CARD = "#242424"
SUNKEN = "#181818"
STRIPE = "#2a2a2a"
LINE = "#3a3a3a"
TEXT = "#f2f2f2"
MUTED = "#9a9a9a"
GREEN = "#6ccb5f"
AMBER = "#f2c14e"
RED = "#ff8a8a"

THUMB_SIZE = (232, 174)

INSERT_LABELS = {
    "Beginning": InsertPosition.BEGINNING,
    "After Nth separator": InsertPosition.AFTER_NTH_SEPARATOR,
    "At character N": InsertPosition.AT_CHAR,
    "End": InsertPosition.END,
}
INSERT_LABEL_BY_VALUE = {v: k for k, v in INSERT_LABELS.items()}

# Where the Place panel puts {place} in the pattern. These rewrite the pattern
# text rather than switching on a placement mode, so the pattern box stays the
# one source of truth and you can see where the place went.
PLACE_OFF = "Off"
PLACE_POSITIONS = (PLACE_OFF, "Beginning", "After the date", "End (suffix)")


# --------------------------------------------------------------------------
# Windows-only polish (no-ops everywhere else)
# --------------------------------------------------------------------------

def set_dpi_awareness() -> None:
    """Crisp text on high-DPI screens. Must run before the window exists.

    1 is PROCESS_SYSTEM_DPI_AWARE, not per-monitor. That is deliberate: Tk
    cannot re-scale a window that is dragged to a monitor with a different
    scaling factor, so claiming per-monitor awareness (2) would give us
    correct-looking text on one screen and tiny text on the other. System
    awareness lets Windows bitmap-stretch instead, which is slightly soft
    but never wrong. shcore only exists on Windows 8.1 and newer, hence the
    fallback to the older all-or-nothing call.
    """
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # SYSTEM_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# DWMWA_USE_IMMERSIVE_DARK_MODE. Microsoft renumbered this attribute during
# Windows 10: builds from 20H1 (18985) onward use 20, the earlier 1809-1909
# builds used 19. Trying both costs nothing and covers every machine that has
# a dark title bar at all.
DARK_MODE_ATTRIBUTES = (20, 19)


def dark_title_bar(window: tk.Misc) -> None:
    """Paint the window frame dark, so it matches the app instead of glowing white."""
    if sys.platform != "win32":
        return
    import ctypes
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        enabled = ctypes.c_int(1)
        for attribute in DARK_MODE_ATTRIBUTES:
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(enabled),
                    ctypes.sizeof(enabled)) == 0:        # 0 is S_OK
                break
        # The frame is only repainted when the window next changes size, so
        # nudge it by one pixel and back, or the bar stays white until the
        # user happens to resize it.
        window.update_idletasks()
        width, height = window.winfo_width(), window.winfo_height()
        if width > 1 and height > 1:
            window.geometry(f"{width}x{height + 1}")
            window.update_idletasks()
            window.geometry(f"{width}x{height}")
    except Exception:
        pass


def resource_path(relative: str) -> Path:
    """Where a bundled file lives, whether we run from source or from the .exe.

    PyInstaller unpacks a --onefile build into a temporary folder and records
    it in sys._MEIPASS, so a plain "assets/icon.ico" would not be found there.
    """
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def set_window_icon(window: tk.Misc) -> None:
    """Use our own icon instead of Tk's default feather. Never fatal."""
    try:
        ico = resource_path("assets/icon.ico")
        if sys.platform == "win32" and ico.exists():
            window.iconbitmap(default=str(ico))
            return
        png = resource_path("assets/icon.png")
        if png.exists():
            window._icon_image = tk.PhotoImage(file=str(png))
            window.iconphoto(True, window._icon_image)
    except Exception:
        pass


def pick_font() -> str:
    families = set(tkfont.families())
    for candidate in ("Segoe UI Variable Text", "Segoe UI", "Inter",
                      "DejaVu Sans", "Helvetica"):
        if candidate in families:
            return candidate
    return "TkDefaultFont"


def _human_size(size: int) -> str:
    """1234 -> '1.2 KB', 5_400_000 -> '5.4 MB'."""
    if size >= 1_048_576:
        return f"{size / 1_048_576:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} bytes"


def pick_mono() -> str:
    families = set(tkfont.families())
    for candidate in ("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier"):
        if candidate in families:
            return candidate
    return "TkFixedFont"


# --------------------------------------------------------------------------

class FotoRenamer:

    def __init__(self, root: tk.Misc):
        self.root = root
        self.font = pick_font()
        self.mono = pick_mono()

        self.files: list[renamer.PhotoFile] = []
        self.checked: set[Path] = set()          # resolved paths that are ticked
        self.plans: list[renamer.RenamePlan] = []
        self.plan_by_path: dict[Path, renamer.RenamePlan] = {}
        # One folder listing, reused across keystrokes instead of re-read on
        # each one. Kept in step by apply_moves, thrown away by invalidate.
        self.dir_index = renamer.DirectoryIndex()

        # The window height to go back to when the Place panel folds away.
        self._height_without_place: int | None = None

        self._preview_job: str | None = None
        self._tick_job: str | None = None        # the 120 ms background tick
        self._thumb_token = 0
        self._thumb_queue: queue.Queue = queue.Queue()
        self._thumb_image: ImageTk.PhotoImage | None = None
        # EXIF backfill: reading capture dates is the slow half of scanning,
        # so it runs on a worker thread. _exif_total > 0 means one is in
        # flight, which is what disables RENAME.
        self._exif_token = 0
        self._exif_queue: queue.Queue = queue.Queue()
        self._exif_done = 0
        self._exif_total = 0
        self._exif_read: set[Path] = set()       # resolved paths already tried
        self._scan_note = ""                     # status line to put back after

        # The trip list, and the cities it has taught us. Both are filled in
        # by _load_settings before any widget exists, so the panel can render
        # them as it is built.
        self.stays: list[renamer.Stay] = []
        self.cities: list[str] = []

        self.settings_file = renamer.app_data_dir() / "settings.json"
        self._restored_geometry = False
        self._make_variables()
        self._saved = self._load_settings()
        self._build_styles()
        self._build_ui()
        self._register_dnd()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        # Coming back from Explorer is the likeliest moment for the folder to
        # have changed behind us, so drop the cached listing and re-plan.
        self.root.bind("<FocusIn>", self._on_focus_in, add="+")
        # Cancel the pending timers when the window goes, or Tk fires them
        # into a dead interpreter. tools/ destroys the root directly, so this
        # cannot live in _on_close.
        self.root.bind("<Destroy>", self._on_destroy, add="+")
        self._schedule_tick()
        self._on_preset_change()
        # _on_preset_change resets the clean-up switches to the preset's own
        # defaults, so the switches the user saved have to be put back AFTER
        # it runs - otherwise "remember my settings" silently does not.
        self._restore_cleanup_switches()
        self._on_mode_change()
        self._on_insert_position_change()
        self.refresh_preview()

    # -- state ------------------------------------------------------------

    def _make_variables(self) -> None:
        default = preset_module.DEFAULT_PRESET
        self.var_preset = tk.StringVar(value=default.label)
        self.var_pattern = tk.StringVar(value=default.pattern)
        self.var_event = tk.StringVar(value="")
        self.var_start = tk.StringVar(value="1")
        self.var_digits = tk.StringVar(value="3")

        self.var_mode = tk.StringVar(value=Mode.NEW_NAME.value)

        self.var_insert_text = tk.StringVar(value="")
        self.var_insert_at = tk.StringVar(value="Beginning")
        self.var_separator = tk.StringVar(value="_")
        self.var_nth = tk.StringVar(value="1")
        self.var_char = tk.StringVar(value="4")

        self.var_find = tk.StringVar(value="")
        self.var_replace = tk.StringVar(value="")
        self.var_match_case = tk.BooleanVar(value=False)

        today = datetime.now().strftime("%Y-%m-%d")
        self.var_city = tk.StringVar(value="")
        self.var_from_date = tk.StringVar(value=today)
        self.var_from_time = tk.StringVar(value="00:00")
        self.var_to_date = tk.StringVar(value=today)
        self.var_to_time = tk.StringVar(value="23:59")
        self.var_place_at = tk.StringVar(value=PLACE_OFF)
        self.var_place_example = tk.StringVar(value="")
        self.var_place_open = tk.BooleanVar(value=False)
        self.var_place_toggle = tk.StringVar(value="")

        self.var_lower = tk.BooleanVar(value=True)
        self.var_spaces = tk.BooleanVar(value=True)
        self.var_accents = tk.BooleanVar(value=True)
        self.var_collapse = tk.BooleanVar(value=True)

        self.var_counts = tk.StringVar(value="no files yet")
        self.var_status = tk.StringVar(value="Drop photos in to get started.")
        self.var_hint = tk.StringVar(value="")
        self.var_example = tk.StringVar(value="")
        self.var_event_label = tk.StringVar(value="Event")
        self.var_rename_button = tk.StringVar(value="RENAME")

        # Any change to these should refresh the preview.
        for var in (self.var_pattern, self.var_event, self.var_start,
                    self.var_digits, self.var_insert_text, self.var_insert_at,
                    self.var_separator, self.var_nth, self.var_char,
                    self.var_find, self.var_replace, self.var_match_case,
                    self.var_lower, self.var_spaces, self.var_accents,
                    self.var_collapse):
            var.trace_add("write", self.schedule_preview)

    def _restore_cleanup_switches(self) -> None:
        for key, var in (("lower", self.var_lower), ("spaces", self.var_spaces),
                         ("accents", self.var_accents),
                         ("collapse", self.var_collapse)):
            if isinstance(self._saved.get(key), bool):
                var.set(self._saved[key])

    def _load_settings(self) -> dict:
        try:
            data = json.loads(self.settings_file.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        mapping = {
            "preset": self.var_preset, "pattern": self.var_pattern,
            "event": self.var_event, "start": self.var_start,
            "digits": self.var_digits, "mode": self.var_mode,
            "insert_text": self.var_insert_text, "insert_at": self.var_insert_at,
            "separator": self.var_separator, "nth": self.var_nth,
            "char": self.var_char, "find": self.var_find,
            "replace": self.var_replace, "place_at": self.var_place_at,
        }
        for key, var in mapping.items():
            if isinstance(data.get(key), str):
                var.set(data[key])
        for key, var in (("match_case", self.var_match_case),
                         ("place_open", self.var_place_open),
                         ("lower", self.var_lower), ("spaces", self.var_spaces),
                         ("accents", self.var_accents),
                         ("collapse", self.var_collapse)):
            if isinstance(data.get(key), bool):
                var.set(data[key])
        self.cities = [c for c in data.get("cities", []) if isinstance(c, str)]
        self.stays = self._stays_from_saved(data.get("stays"))
        if self.stays:
            # A trip list you cannot see is a trap: once there is one, the
            # panel opens whatever was saved.
            self.var_place_open.set(True)
        geometry = self._onscreen_geometry(data.get("geometry"))
        if geometry:
            try:
                self.root.geometry(geometry)
                self._restored_geometry = True
            except tk.TclError:
                pass
        return data

    @staticmethod
    def _stays_from_saved(saved) -> list[renamer.Stay]:
        """Rebuild the trip list from settings.json.

        Stored as [start_iso, end_iso, place] triples, which datetime round-
        trips with no custom JSON encoder. A row that does not read back is
        dropped rather than taking the whole file down with it: a broken
        settings file must never stop the app opening.
        """
        stays: list[renamer.Stay] = []
        if not isinstance(saved, list):
            return stays
        for row in saved:
            if not (isinstance(row, list) and len(row) == 3):
                continue
            try:
                start, end, place = row
                stays.append(renamer.Stay(start=datetime.fromisoformat(start),
                                          end=datetime.fromisoformat(end),
                                          place=str(place)))
            except (TypeError, ValueError):
                continue
        stays.sort(key=lambda stay: (stay.start, stay.end))
        return stays

    def _onscreen_geometry(self, geometry) -> str | None:
        """Drop a saved position that would open the window off-screen.

        Windows users unplug the second monitor the app was last on. Without
        this the window reopens at coordinates nobody can reach.
        """
        if not isinstance(geometry, str):
            return None
        match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", geometry.strip())
        if not match:
            return geometry if re.fullmatch(r"\d+x\d+", geometry.strip()) else None
        width, height, x, y = (int(g) for g in match.groups())
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        # Keep the title bar reachable: at least 120x40 px of it must be visible.
        if -width + 120 <= x <= screen_w - 120 and 0 <= y <= screen_h - 40:
            return geometry
        return f"{width}x{height}"

    def _save_settings(self) -> None:
        data = {
            "preset": self.var_preset.get(), "pattern": self.var_pattern.get(),
            "event": self.var_event.get(), "start": self.var_start.get(),
            "digits": self.var_digits.get(), "mode": self.var_mode.get(),
            "insert_text": self.var_insert_text.get(),
            "insert_at": self.var_insert_at.get(),
            "separator": self.var_separator.get(), "nth": self.var_nth.get(),
            "char": self.var_char.get(), "find": self.var_find.get(),
            "replace": self.var_replace.get(),
            "match_case": self.var_match_case.get(),
            "lower": self.var_lower.get(), "spaces": self.var_spaces.get(),
            "accents": self.var_accents.get(),
            "collapse": self.var_collapse.get(),
            "geometry": self.root.winfo_geometry(),
            "place_at": self.var_place_at.get(),
            "place_open": self.var_place_open.get(),
            "cities": self.cities,
            "stays": [[stay.start.isoformat(), stay.end.isoformat(), stay.place]
                      for stay in self.stays],
        }
        try:
            self.settings_file.write_text(json.dumps(data, indent=2),
                                          encoding="utf-8")
        except OSError:
            pass

    def _on_close(self) -> None:
        self._save_settings()
        self.root.destroy()

    def _on_destroy(self, event) -> None:
        """Tk fires <Destroy> for every child widget too; only the window counts."""
        if event.widget is not self.root:
            return
        self._cancel_tick()
        if self._preview_job is not None:
            try:
                self.root.after_cancel(self._preview_job)
            except tk.TclError:
                pass
            self._preview_job = None

    # -- styling ----------------------------------------------------------

    def _build_styles(self) -> None:
        sv_ttk.set_theme("dark")
        style = ttk.Style()

        style.configure("TLabel", font=(self.font, 10))
        style.configure("Title.TLabel", font=(self.font, 20, "bold"))
        style.configure("Subtitle.TLabel", font=(self.font, 10), foreground=MUTED)
        style.configure("Muted.TLabel", font=(self.font, 9), foreground=MUTED)
        style.configure("Count.TLabel", font=(self.font, 10), foreground=ACCENT)
        style.configure("Field.TLabel", font=(self.font, 10), foreground=MUTED)
        style.configure("Mono.TLabel", font=(self.mono, 9), foreground=MUTED)
        style.configure("Ok.TLabel", font=(self.font, 9), foreground=GREEN)
        style.configure("Warn.TLabel", font=(self.font, 9), foreground=AMBER)

        # Deliberately taller than default controls.
        style.configure("Big.Accent.TButton", font=(self.font, 11, "bold"),
                        padding=(30, 17))
        style.configure("Tall.TButton", font=(self.font, 10), padding=(16, 11))
        style.configure("Chip.TButton", font=(self.mono, 9), padding=(9, 5))
        style.configure("Place.Toggle.TButton", font=(self.font, 10),
                        padding=(12, 7))
        style.configure("Seg.Toggle.TButton", font=(self.font, 10), padding=(20, 12))
        style.configure("TEntry", padding=(8, 9))
        style.configure("TCombobox", padding=(8, 9))
        style.configure("TSpinbox", padding=(6, 9))
        style.configure("TCheckbutton", font=(self.font, 10))

        style.configure("Stay.Treeview", rowheight=26, font=(self.font, 10))
        style.configure("Treeview", rowheight=34, font=(self.font, 10),
                        background=SUNKEN, fieldbackground=SUNKEN,
                        borderwidth=0)
        style.configure("Treeview.Heading", font=(self.font, 9, "bold"),
                        padding=(6, 8))
        style.map("Treeview", background=[("selected", "#33507a")],
                  foreground=[("selected", TEXT)])

        # The Combobox popup is a classic Tk listbox, so it needs its own colours.
        self.root.option_add("*TCombobox*Listbox.background", CARD)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#101010")
        self.root.option_add("*TCombobox*Listbox.font", (self.font, 10))

    # -- layout -----------------------------------------------------------

    def _build_ui(self) -> None:
        self.root.title("Foto Renamer")
        self.root.configure(background=BG)
        self.root.minsize(1010, 720)
        # winfo_geometry() is "1x1+0+0" until the window is mapped, so it
        # cannot be used to tell "no size yet" from "restored size".
        if not self._restored_geometry:
            self.root.geometry("1200x950")

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        self._build_header()
        self._build_body()
        self._build_controls()
        self._build_footer()

    def _build_header(self) -> None:
        header = ttk.Frame(self.root, padding=(24, 16, 24, 6))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text="Foto Renamer",
                  style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Bulk-rename your phone photos, safely",
                  style="Subtitle.TLabel").grid(row=1, column=0, sticky="w",
                                                pady=(2, 0))
        ttk.Label(header, textvariable=self.var_counts,
                  style="Count.TLabel").grid(row=0, column=1, sticky="e")

    def _build_body(self) -> None:
        body = ttk.Frame(self.root, padding=(24, 4, 24, 4))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, minsize=272)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body, style="Card.TFrame", padding=14)
        left.grid(row=0, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        self._build_drop_zone(left)
        self._build_tree(left)
        self._build_selection_buttons(left)
        self._build_preview_pane(body)

    def _build_drop_zone(self, parent: ttk.Frame) -> None:
        self.zone = tk.Canvas(parent, height=64, bg=CARD, highlightthickness=0,
                              takefocus=0)
        self.zone.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        self.add_button = ttk.Button(self.zone, text="Add photos…",
                                     style="Tall.TButton", command=self.add_files)
        self._zone_items: dict[str, int] = {}
        self.zone.bind("<Configure>", lambda _e: self._draw_zone())
        self.zone.bind("<Button-1>", lambda _e: self.add_files())
        self._zone_active = False

    def _draw_zone(self, active: bool | None = None) -> None:
        """Dashed 'drop here' strip, centred as one text + button group."""
        if active is not None:
            self._zone_active = active
        self.zone.delete("all")
        width = max(self.zone.winfo_width(), 320)
        height = self.zone.winfo_height() or 64
        colour = ACCENT if self._zone_active else LINE
        self.zone.create_rectangle(3, 3, width - 3, height - 3, dash=(6, 5),
                                   outline=colour, width=2)

        text = ("Release to add these photos" if self._zone_active
                else "Drag photos here from Explorer   ·")
        measure = tkfont.Font(family=self.font, size=11).measure(text)
        button_width = self.add_button.winfo_reqwidth() or 150
        left = (width - (measure + 16 + button_width)) / 2
        self.zone.create_text(left, height / 2, text=text, anchor="w",
                              fill=TEXT if self._zone_active else MUTED,
                              font=(self.font, 11))
        self.zone.create_window(left + measure + 16, height / 2, anchor="w",
                                window=self.add_button)

    def _build_tree(self, parent: ttk.Frame) -> None:
        wrapper = ttk.Frame(parent)
        wrapper.grid(row=1, column=0, sticky="nsew")
        wrapper.columnconfigure(0, weight=1)
        wrapper.rowconfigure(0, weight=1)

        columns = ("check", "old", "new", "date")
        # height is in rows: asks for a list tall enough to be useful,
        # while the grid still lets it grow with the window.
        self.tree = ttk.Treeview(wrapper, columns=columns, show="headings",
                                 selectmode="extended", height=9)
        self.tree.heading("check", text="")
        self.tree.heading("old", text="Current name")
        self.tree.heading("new", text="→   New name")
        self.tree.heading("date", text="Taken")
        self.tree.column("check", width=46, minwidth=46, anchor="center",
                         stretch=False)
        self.tree.column("old", width=250, minwidth=140, anchor="w")
        self.tree.column("new", width=320, minwidth=160, anchor="w")
        self.tree.column("date", width=158, minwidth=130, anchor="w",
                         stretch=False)

        self.tree.tag_configure("stripe", background=STRIPE)
        self.tree.tag_configure("unticked", foreground=MUTED)
        self.tree.tag_configure("conflict", foreground=RED)
        self.tree.tag_configure("toolong", foreground=AMBER)
        self.tree.grid(row=0, column=0, sticky="nsew")

        bar = ttk.Scrollbar(wrapper, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        bar.grid(row=0, column=1, sticky="ns")

        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<space>", lambda _e: self._toggle_selected_rows())
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_thumbnail())
        self.tree.bind("<Delete>", lambda _e: self.remove_selected())

    def _build_selection_buttons(self, parent: ttk.Frame) -> None:
        row = ttk.Frame(parent)
        row.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        for text, command in (("Select all", lambda: self._set_all(True)),
                              ("None", lambda: self._set_all(False)),
                              ("Invert", self._invert),
                              ("Remove from list", self.remove_selected),
                              ("Clear", self.clear_list)):
            ttk.Button(row, text=text, style="Tall.TButton",
                       command=command).pack(side="left", padx=(0, 8))

    def _build_preview_pane(self, parent: ttk.Frame) -> None:
        card = ttk.Frame(parent, style="Card.TFrame", padding=14)
        card.grid(row=0, column=1, sticky="nsew", padx=(14, 0))
        card.columnconfigure(0, weight=1)

        self.thumb_canvas = tk.Canvas(card, width=THUMB_SIZE[0],
                                      height=THUMB_SIZE[1], bg=SUNKEN,
                                      highlightthickness=0)
        self.thumb_canvas.grid(row=0, column=0, pady=(0, 12))

        self.lbl_preview_name = ttk.Label(card, text="—", style="Mono.TLabel",
                                          wraplength=THUMB_SIZE[0], justify="left")
        self.lbl_preview_name.grid(row=1, column=0, sticky="w")
        self.lbl_preview_meta = ttk.Label(card, text="Select a row to preview it",
                                          style="Muted.TLabel", justify="left")
        self.lbl_preview_meta.grid(row=2, column=0, sticky="w", pady=(6, 0))

        self.lbl_preview_new = ttk.Label(card, text="", style="Ok.TLabel",
                                         wraplength=THUMB_SIZE[0], justify="left")
        self.lbl_preview_new.grid(row=3, column=0, sticky="w", pady=(10, 0))
        self._clear_thumbnail("Select a row")

    # -- the controls card -------------------------------------------------

    def _build_controls(self) -> None:
        card = ttk.Frame(self.root, style="Card.TFrame", padding=(16, 12))
        card.grid(row=2, column=0, sticky="ew", padx=24, pady=(8, 2))
        card.columnconfigure(0, weight=1)

        segmented = ttk.Frame(card)
        segmented.grid(row=0, column=0, sticky="w", pady=(0, 12))
        for text, mode in (("New name", Mode.NEW_NAME),
                           ("Insert text", Mode.INSERT),
                           ("Find & replace", Mode.REPLACE)):
            ttk.Radiobutton(segmented, text=text, value=mode.value,
                            variable=self.var_mode, style="Seg.Toggle.TButton",
                            command=self._on_mode_change).pack(side="left",
                                                               padx=(0, 8))

        self.panels = ttk.Frame(card)
        self.panels.grid(row=1, column=0, sticky="ew")
        self.panels.columnconfigure(0, weight=1)
        self.panel_new = self._build_panel_new(self.panels)
        self.panel_insert = self._build_panel_insert(self.panels)
        self.panel_replace = self._build_panel_replace(self.panels)

        cleanup = ttk.Frame(card)
        cleanup.grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Label(cleanup, text="Clean up", style="Field.TLabel").pack(
            side="left", padx=(0, 12))
        for text, var in (("lowercase", self.var_lower),
                          ("spaces → hyphens", self.var_spaces),
                          ("strip accents", self.var_accents),
                          ("tidy separators", self.var_collapse)):
            ttk.Checkbutton(cleanup, text=text, variable=var).pack(side="left",
                                                                   padx=(0, 16))

        self.lbl_hint = ttk.Label(card, textvariable=self.var_hint,
                                  style="Ok.TLabel")
        self.lbl_hint.grid(row=3, column=0, sticky="w", pady=(8, 0))

    def _build_panel_new(self, parent: ttk.Frame) -> ttk.Frame:
        panel = ttk.Frame(parent)
        # minsize stops the fields collapsing when the row next to them is wide.
        panel.columnconfigure(1, weight=1, minsize=360)
        panel.columnconfigure(2, weight=0)

        ttk.Label(panel, text="Preset", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 12), pady=(0, 7))
        self.combo_preset = ttk.Combobox(
            panel, textvariable=self.var_preset, state="readonly",
            values=preset_module.PRESET_LABELS, font=(self.font, 10))
        self.combo_preset.grid(row=0, column=1, sticky="ew", pady=(0, 7))
        self.combo_preset.bind("<<ComboboxSelected>>",
                               lambda _e: self._on_preset_change())
        ttk.Label(panel, textvariable=self.var_example, style="Mono.TLabel").grid(
            row=0, column=2, sticky="w", padx=(18, 0), pady=(0, 7))

        ttk.Label(panel, text="Pattern", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 12), pady=(0, 7))
        self.entry_pattern = ttk.Entry(panel, textvariable=self.var_pattern,
                                       font=(self.mono, 10))
        self.entry_pattern.grid(row=1, column=1, sticky="ew", pady=(0, 7))

        chips = ttk.Frame(panel)
        chips.grid(row=1, column=2, sticky="w", padx=(18, 0), pady=(0, 7))
        self.chip_buttons: list[ttk.Button] = []
        for token in ("{date}", "{date8}", "{time}", "{event}", "{orig}",
                      "{cam}", "{n}", "{place}"):
            button = ttk.Button(chips, text=token, style="Chip.TButton",
                                command=lambda t=token: self._insert_token(t))
            button.pack(side="left", padx=(0, 5))
            self.chip_buttons.append(button)

        self.lbl_event = ttk.Label(panel, textvariable=self.var_event_label,
                                    style="Field.TLabel")
        self.lbl_event.grid(row=2, column=0, sticky="w", padx=(0, 12))
        self.entry_event = ttk.Entry(panel, textvariable=self.var_event,
                                      font=(self.font, 11))
        self.entry_event.grid(row=2, column=1, sticky="ew")

        numbers = ttk.Frame(panel)
        numbers.grid(row=2, column=2, sticky="w", padx=(18, 0))
        ttk.Label(numbers, text="Start", style="Field.TLabel").pack(
            side="left", padx=(0, 8))
        self.spin_start = ttk.Spinbox(numbers, from_=0, to=99999, width=6,
                                       textvariable=self.var_start,
                                       font=(self.font, 10))
        self.spin_start.pack(side="left", padx=(0, 18))
        ttk.Label(numbers, text="Digits", style="Field.TLabel").pack(
            side="left", padx=(0, 8))
        self.spin_digits = ttk.Spinbox(numbers, from_=1, to=8, width=5,
                                        textvariable=self.var_digits,
                                        font=(self.font, 10))
        self.spin_digits.pack(side="left")

        self.lbl_note = ttk.Label(panel, text="", style="Muted.TLabel")
        self.lbl_note.grid(row=3, column=1, columnspan=2, sticky="w", pady=(6, 0))

        self._build_place_panel(panel)
        return panel

    def _build_place_panel(self, parent: ttk.Frame) -> None:
        """The trip list: "15 Sep I was in New York City, 16-20 Sep Boston".

        No naming rule lives here. The boxes are handed to renamer.parse_stay,
        which either returns a Stay or raises a sentence for the status line,
        and the list itself is plain data that current_settings passes down.

        It only appears in New name mode, which is the only mode {place} means
        anything in: Insert text and Find & replace promise to leave the
        original name alone.
        """
        shell = ttk.Frame(parent)
        shell.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        shell.columnconfigure(0, weight=1)
        ttk.Separator(shell, orient="horizontal").grid(
            row=0, column=0, sticky="ew", pady=(0, 8))

        # Folded away until it is wanted. The panel is 200 pixels tall, and
        # spending those on every user who never names a photo after a city
        # would cost the file list five of its rows.
        self.button_place = ttk.Button(shell, textvariable=self.var_place_toggle,
                                       style="Place.Toggle.TButton",
                                       command=self._toggle_place_panel)
        self.button_place.grid(row=1, column=0, sticky="w")

        place = ttk.Frame(shell)
        self.place_body = place
        place.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        place.columnconfigure(1, weight=1)

        form = ttk.Frame(place)
        form.grid(row=1, column=0, sticky="nw", padx=(0, 18))

        ttk.Label(form, text="City", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 12), pady=(0, 5))
        self.combo_city = ttk.Combobox(form, textvariable=self.var_city,
                                       values=self.cities, width=24,
                                       font=(self.font, 10))
        self.combo_city.grid(row=0, column=1, columnspan=2, sticky="ew",
                             pady=(0, 5))

        for row, (label, date_var, time_var) in enumerate((
                ("From", self.var_from_date, self.var_from_time),
                ("To", self.var_to_date, self.var_to_time)), start=1):
            ttk.Label(form, text=label, style="Field.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 12), pady=(0, 5))
            ttk.Entry(form, textvariable=date_var, width=13,
                      font=(self.mono, 10)).grid(row=row, column=1, sticky="w",
                                                 pady=(0, 5))
            ttk.Entry(form, textvariable=time_var, width=7,
                      font=(self.mono, 10)).grid(row=row, column=2, sticky="w",
                                                 padx=(8, 0), pady=(0, 5))

        self.button_add_stay = ttk.Button(form, text="Add", style="Tall.TButton",
                                          command=self._add_stay)
        self.button_add_stay.grid(row=1, column=3, rowspan=2, padx=(14, 0),
                                  sticky="ns")

        # The list. Three columns, the last one a ✕ that deletes the row —
        # the same click-on-a-column trick the file list uses for its ticks.
        self.stay_tree = ttk.Treeview(place, columns=("when", "place", "del"),
                                      show="headings", selectmode="browse",
                                      style="Stay.Treeview", height=4)
        self.stay_tree.heading("when", text="When")
        self.stay_tree.heading("place", text="Place")
        self.stay_tree.heading("del", text="")
        self.stay_tree.column("when", width=190, minwidth=130, anchor="w")
        self.stay_tree.column("place", width=190, minwidth=110, anchor="w")
        self.stay_tree.column("del", width=40, minwidth=40, anchor="center",
                              stretch=False)
        self.stay_tree.tag_configure("stripe", background=STRIPE)
        self.stay_tree.grid(row=1, column=1, sticky="nsew")
        self.stay_tree.bind("<Button-1>", self._on_stay_click)

        footer = ttk.Frame(place)
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Label(footer, text="Position", style="Field.TLabel").pack(
            side="left", padx=(0, 12))
        self.combo_place_at = ttk.Combobox(footer, textvariable=self.var_place_at,
                                           state="readonly", width=18,
                                           values=list(PLACE_POSITIONS),
                                           font=(self.font, 10))
        self.combo_place_at.pack(side="left")
        self.combo_place_at.bind("<<ComboboxSelected>>",
                                 lambda _e: self._on_place_position_change())
        ttk.Label(footer, textvariable=self.var_place_example,
                  style="Mono.TLabel").pack(side="left", padx=(18, 0))

        self._refresh_stay_rows()
        self._sync_place_panel()

    def _build_panel_insert(self, parent: ttk.Frame) -> ttk.Frame:
        panel = ttk.Frame(parent)
        panel.columnconfigure(1, weight=1, minsize=360)

        ttk.Label(panel, text="Text", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 12), pady=(0, 7))
        ttk.Entry(panel, textvariable=self.var_insert_text,
                  font=(self.font, 11)).grid(row=0, column=1, sticky="ew",
                                             pady=(0, 7))
        ttk.Label(panel, text="the original name is kept intact",
                  style="Muted.TLabel").grid(row=0, column=2, sticky="w",
                                             padx=(18, 0), pady=(0, 7))

        ttk.Label(panel, text="Insert at", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 12))
        options = ttk.Frame(panel)
        options.grid(row=1, column=1, columnspan=2, sticky="w")
        self.combo_insert = ttk.Combobox(options, textvariable=self.var_insert_at,
                                          state="readonly", width=22,
                                          values=list(INSERT_LABELS),
                                          font=(self.font, 10))
        self.combo_insert.pack(side="left")
        self.combo_insert.bind("<<ComboboxSelected>>",
                               lambda _e: self._on_insert_position_change())

        ttk.Label(options, text="Separator", style="Field.TLabel").pack(
            side="left", padx=(18, 8))
        ttk.Entry(options, textvariable=self.var_separator, width=4,
                  font=(self.mono, 10)).pack(side="left")

        self.lbl_which = ttk.Label(options, text="", style="Field.TLabel")
        self.lbl_which.pack(side="left", padx=(18, 8))
        self.spin_nth = ttk.Spinbox(options, from_=1, to=20, width=5,
                                     textvariable=self.var_nth,
                                     font=(self.font, 10))
        self.spin_char = ttk.Spinbox(options, from_=0, to=200, width=5,
                                      textvariable=self.var_char,
                                      font=(self.font, 10))
        return panel

    def _build_panel_replace(self, parent: ttk.Frame) -> ttk.Frame:
        panel = ttk.Frame(parent)
        panel.columnconfigure(1, weight=1)
        panel.columnconfigure(3, weight=1)

        ttk.Label(panel, text="Find", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 10))
        ttk.Entry(panel, textvariable=self.var_find, font=(self.font, 11)).grid(
            row=0, column=1, sticky="ew")
        ttk.Label(panel, text="Replace with", style="Field.TLabel").grid(
            row=0, column=2, sticky="w", padx=(18, 10))
        ttk.Entry(panel, textvariable=self.var_replace,
                  font=(self.font, 11)).grid(row=0, column=3, sticky="ew")
        ttk.Checkbutton(panel, text="Match case",
                        variable=self.var_match_case).grid(row=0, column=4,
                                                           padx=(18, 0))
        return panel

    def _build_footer(self) -> None:
        footer = ttk.Frame(self.root, padding=(24, 8, 24, 16))
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(1, weight=1)

        self.button_undo = ttk.Button(footer, text="↩  Undo last rename",
                                      style="Tall.TButton", command=self.do_undo)
        self.button_undo.grid(row=0, column=0, sticky="w")
        ttk.Label(footer, textvariable=self.var_status, style="Muted.TLabel").grid(
            row=0, column=1, sticky="w", padx=16)
        self.button_rename = ttk.Button(footer,
                                        textvariable=self.var_rename_button,
                                        style="Big.Accent.TButton",
                                        command=self.do_rename)
        self.button_rename.grid(row=0, column=2, sticky="e")
        self._refresh_undo_button()

    # -- drag & drop -------------------------------------------------------

    def _register_dnd(self) -> None:
        if TkinterDnD is None:
            return
        for widget in (self.root, self.zone, self.tree):
            try:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)
                widget.dnd_bind("<<DropEnter>>",
                                lambda _e: self._draw_zone(active=True))
                widget.dnd_bind("<<DropLeave>>",
                                lambda _e: self._draw_zone(active=False))
            except Exception:
                pass

    def _on_drop(self, event) -> None:
        self._draw_zone(active=False)
        paths = self.root.tk.splitlist(event.data)
        self.add_paths(paths)

    # -- the file list -----------------------------------------------------

    def add_files(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in sorted(SUPPORTED_EXTS))
        chosen = filedialog.askopenfilenames(
            title="Choose photos to rename",
            filetypes=[("Photos and videos", patterns), ("All files", "*.*")])
        if chosen:
            self.add_paths(chosen)

    def add_paths(self, paths) -> None:
        # read_exif=False keeps the slow part off the UI thread: the list
        # appears at once with file dates, and _start_exif_backfill corrects
        # them behind it.
        found, skipped = renamer.scan_paths(paths, read_exif=False)
        known = {f.resolved for f in self.files}
        added = [f for f in found if f.resolved not in known]
        self.files = renamer.sort_files(self.files + added)
        self.checked.update(f.resolved for f in added)

        notes = [f"{len(added)} added"]
        if skipped:
            notes.append(f"{skipped} skipped (not a photo or video)")
        if not renamer.HEIC_SUPPORT and any(f.ext.lower() in renamer.HEIC_EXTS
                                            for f in added):
            notes.append("HEIC dates fall back to the file date "
                         "(pip install pillow-heif to read them)")
        self._scan_note = " · ".join(notes)
        self.var_status.set(self._scan_note)
        self._populate_tree()
        self._start_exif_backfill()        # before the preview, so RENAME
        self.refresh_preview()             # is already showing as blocked

    def _start_exif_backfill(self) -> None:
        """Read the real capture dates on a worker thread.

        Same shape as the thumbnail loader already in this file: a daemon
        thread posts results to a queue, the 120 ms tick drains them on the UI
        thread, and a token makes a newer scan supersede an older one. The
        worker never touches a PhotoFile or a widget — it only reads paths and
        posts values.

        It sweeps every file whose date has not been read yet, not just the
        ones just added: starting a new worker abandons the running one, so
        whatever that one had not reached still needs reading. Drop a folder,
        then drop a second one while the first is still going, and both end up
        with real dates.
        """
        pending = [f for f in self.files if f.resolved not in self._exif_read]
        self._exif_token += 1              # abandons any backfill still running
        self._exif_done, self._exif_total = 0, len(pending)
        if not pending:
            return
        threading.Thread(target=self._read_exif_dates,
                         args=(pending, self._exif_token),
                         daemon=True).start()

    def _read_exif_dates(self, files, token: int) -> None:
        """Runs off the UI thread; results are picked up by _drain_exif_queue."""
        renamer.backfill_exif_dates(
            files,
            on_result=lambda photo, taken_at, from_exif:
                self._exif_queue.put((token, photo, taken_at, from_exif)),
            should_stop=lambda: token != self._exif_token)
        self._exif_queue.put((token, None, None, False))     # finished marker

    def _drain_exif_queue(self) -> None:
        finished = False
        try:
            while True:
                token, photo, taken_at, from_exif = self._exif_queue.get_nowait()
                if token != self._exif_token:
                    continue                 # a newer scan superseded this one
                if photo is None:
                    finished = True
                    continue
                self._exif_done += 1
                self._exif_read.add(photo.resolved)
                if from_exif and taken_at != photo.taken_at:
                    photo.set_taken_at(taken_at, from_exif=True)
        except queue.Empty:
            pass

        if finished:
            self._exif_total = 0
            # One reshuffle, at the end: the list settles into chronological
            # order exactly once rather than jumping about as dates land.
            self.files = renamer.sort_files(self.files)
            self._populate_tree()
            self.refresh_preview()            # also puts RENAME back
            self.var_status.set(self._scan_note)
        elif self._exif_total:
            self.var_status.set(
                f"reading dates… {self._exif_done}/{self._exif_total}")

    def wait_for_dates(self, timeout: float = 60.0) -> bool:
        """Pump the event loop until the EXIF backfill has finished.

        Only the headless drive scripts in tools/ need this: they call
        add_paths() and assert on the preview straight away, which the
        threaded backfill would otherwise race. Returns False on timeout.
        """
        deadline = time.monotonic() + timeout
        while self._exif_total:
            if time.monotonic() > deadline:
                return False
            self.root.update_idletasks()
            self.root.update()
            self._drain_exif_queue()
            time.sleep(0.005)
        return True

    def remove_selected(self) -> None:
        doomed = [photo for photo in map(self._photo_for_row, self.tree.selection())
                  if photo is not None]
        if not doomed:
            return
        # Untick before dropping them, while we still hold the PhotoFiles and
        # can read the resolved path they already worked out.
        self.checked -= {f.resolved for f in doomed}
        # Forget that we read their dates, so re-adding one reads it again.
        self._exif_read -= {f.resolved for f in doomed}
        gone = {f.path for f in doomed}
        self.files = [f for f in self.files if f.path not in gone]
        self.var_status.set(f"{len(gone)} removed from the list")
        self._populate_tree()
        self.refresh_preview()

    def clear_list(self) -> None:
        self.files, self.checked = [], set()
        self._exif_read.clear()
        self._exif_token += 1              # abandon any backfill still running
        self._exif_done = self._exif_total = 0
        self.var_status.set("List cleared.")
        self._populate_tree()
        self.refresh_preview()

    def _set_all(self, value: bool) -> None:
        self.checked = {f.resolved for f in self.files} if value else set()
        self.refresh_preview()

    def _invert(self) -> None:
        everything = {f.resolved for f in self.files}
        self.checked = everything - self.checked
        self.refresh_preview()

    def _photo_for_row(self, iid: str) -> renamer.PhotoFile | None:
        """The PhotoFile behind a tree row.

        Row ids are the file's index in self.files (see _populate_tree), so
        this is a direct lookup rather than a scan for a matching path.
        """
        index = int(iid)
        return self.files[index] if 0 <= index < len(self.files) else None

    def _on_tree_click(self, event) -> None:
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#1":     # the tick column
            return
        iid = self.tree.identify_row(event.y)
        if iid:
            self._toggle_rows([iid])

    def _toggle_selected_rows(self) -> None:
        self._toggle_rows(self.tree.selection())

    def _toggle_rows(self, iids) -> None:
        for iid in iids:
            photo = self._photo_for_row(iid)
            if photo is None:
                continue
            resolved = photo.resolved
            if resolved in self.checked:
                self.checked.discard(resolved)
            else:
                self.checked.add(resolved)
        self.refresh_preview()

    def _populate_tree(self) -> None:
        """Rebuild the rows. Cheap per-keystroke updates go through
        refresh_preview instead, which only rewrites the New name column."""
        # Remember which rows were highlighted so the preview pane does not
        # go blank every time the list is rebuilt (e.g. after renaming).
        selected = [int(iid) for iid in self.tree.selection()]

        self.tree.delete(*self.tree.get_children())
        for index, photo in enumerate(self.files):
            self.tree.insert("", "end", iid=str(index),
                             values=("", photo.name, "", ""))

        restore = [str(i) for i in selected if i < len(self.files)]
        if restore:
            self.tree.selection_set(restore)
            self.tree.focus(restore[0])
            self.tree.see(restore[0])

    # -- the live preview --------------------------------------------------

    def current_settings(self) -> RenameSettings:
        def whole(var: tk.StringVar, fallback: int) -> int:
            try:
                return int(str(var.get()).strip())
            except (TypeError, ValueError):
                return fallback

        return RenameSettings(
            mode=Mode(self.var_mode.get()),
            pattern=self.var_pattern.get(),
            event=self.var_event.get(),
            # The Start spinbox floor is 0; typing a negative by hand would
            # otherwise put a stray hyphen in every name ("_-05").
            start=max(0, whole(self.var_start, 1)),
            digits=whole(self.var_digits, 3),
            insert_text=self.var_insert_text.get(),
            insert_position=INSERT_LABELS.get(self.var_insert_at.get(),
                                               InsertPosition.BEGINNING),
            separator=self.var_separator.get(),
            nth=whole(self.var_nth, 1),
            char_index=whole(self.var_char, 0),
            find=self.var_find.get(),
            replace_with=self.var_replace.get(),
            match_case=self.var_match_case.get(),
            stays=tuple(self.stays),
            cleanup=CleanupOptions(lowercase=self.var_lower.get(),
                                    spaces_to_hyphens=self.var_spaces.get(),
                                    strip_accents=self.var_accents.get(),
                                    collapse_separators=self.var_collapse.get()),
        )

    def schedule_preview(self, *_args) -> None:
        """Debounce: recalculate 120ms after you stop typing, not per keystroke."""
        if self._preview_job is not None:
            try:
                self.root.after_cancel(self._preview_job)
            except tk.TclError:
                pass
        self._preview_job = self.root.after(120, self.refresh_preview)

    def refresh_preview(self) -> None:
        self._preview_job = None
        settings = self.current_settings()
        ticked = [f for f in self.files if f.resolved in self.checked]
        self.plans = (renamer.plan_renames(ticked, settings, index=self.dir_index)
                      if ticked else [])
        self.plan_by_path = {p.photo.path: p for p in self.plans}

        for index, photo in enumerate(self.files):
            iid = str(index)
            if not self.tree.exists(iid):
                continue
            plan = self.plan_by_path.get(photo.path)
            is_checked = photo.resolved in self.checked
            tags = ["stripe"] if index % 2 else []
            if not is_checked:
                tags.append("unticked")
            elif plan is not None and plan.too_long:
                tags.append("conflict")
            elif plan is not None and plan.truncated:
                tags.append("toolong")
            elif plan is not None and plan.conflict:
                tags.append("conflict")
            self.tree.item(iid, tags=tags,
                           values=("☑" if is_checked else "☐",
                                   photo.name,
                                   plan.new_name if plan else "—",
                                   photo.date_display))

        # The Place panel's example: the first ticked photo as it will come
        # out. It costs nothing here because the plan is already built.
        self.var_place_example.set(self.plans[0].new_name if self.plans else "")

        changing = [p for p in self.plans if p.changed]
        self.var_counts.set(f"{len(self.files)} files · {len(self.plans)} ticked")
        if self._exif_total:
            # The numbers follow capture order, so committing before every
            # date is in could hand out a sequence the finished list would
            # never produce. Loading stays instant; only the commit waits.
            self.var_rename_button.set("reading dates…")
            self.button_rename.state(["disabled"])
        else:
            self.var_rename_button.set(
                f"RENAME {len(changing)} FILE{'S' if len(changing) != 1 else ''}"
                if changing else "RENAME")
            self.button_rename.state(["!disabled"] if changing else ["disabled"])
        self._update_hint()
        self._update_preview_labels()

    def _on_focus_in(self, event) -> None:
        """The window (not just a widget inside it) got focus again."""
        if event.widget is not self.root:
            return
        self.dir_index.invalidate()
        self.schedule_preview()

    def _update_hint(self) -> None:
        if not self.plans:
            self.var_hint.set("")
            return
        # Windows' 260-character path limit outranks any style advice.
        blocked = [p for p in self.plans if p.too_long]
        if blocked:
            self.var_hint.set(
                f"⚠  {len(blocked)} file(s): the folder path is already too long "
                f"for Windows — move the photos closer to the drive root")
            self.lbl_hint.configure(style="Warn.TLabel")
            return
        shortened = [p for p in self.plans if p.truncated]
        if shortened:
            self.var_hint.set(
                f"⚠  {len(shortened)} name(s) shortened to stay under Windows' "
                f"260-character path limit")
            self.lbl_hint.configure(style="Warn.TLabel")
            return
        stem = Path(self.plans[0].new_name).stem
        ok, message = renamer.check_convention(stem)
        self.var_hint.set(("✓  " if ok else "⚠  ") + message)
        self.lbl_hint.configure(style="Ok.TLabel" if ok else "Warn.TLabel")

    # -- preview pane ------------------------------------------------------

    def _clear_thumbnail(self, message: str) -> None:
        self.thumb_canvas.delete("all")
        self.thumb_canvas.create_text(THUMB_SIZE[0] / 2, THUMB_SIZE[1] / 2,
                                      text=message, fill=MUTED,
                                      font=(self.font, 10))

    def _selected_photo(self) -> renamer.PhotoFile | None:
        selection = self.tree.selection()
        return self._photo_for_row(selection[0]) if selection else None

    def _update_preview_labels(self) -> None:
        photo = self._selected_photo()
        if photo is None:
            self.lbl_preview_name.configure(text="—")
            self.lbl_preview_meta.configure(text="Select a row to preview it")
            self.lbl_preview_new.configure(text="")
            return
        plan = self.plan_by_path.get(photo.path)
        source = "EXIF" if photo.from_exif else "file date"
        if (not photo.from_exif and photo.ext.lower() in renamer.HEIC_EXTS
                and not renamer.HEIC_SUPPORT):
            source = "file date — install pillow-heif to read HEIC"
        self.lbl_preview_name.configure(text=photo.name)
        self.lbl_preview_meta.configure(
            text=f"{photo.taken_at.strftime('%d %b %Y · %H:%M:%S')}  ({source})\n"
                 f"{_human_size(photo.size)}")
        self.lbl_preview_new.configure(
            text=f"→  {plan.new_name}" if plan else "not ticked")

    def _show_thumbnail(self) -> None:
        self._update_preview_labels()
        photo = self._selected_photo()
        if photo is None:
            self._clear_thumbnail("Select a row")
            return
        ext = photo.ext.lower()
        if ext not in renamer.THUMBNAILABLE_EXTS:
            if ext in renamer.HEIC_EXTS:
                self._clear_thumbnail("install pillow-heif\nto preview HEIC")
            else:
                self._clear_thumbnail(f"no preview for {ext}")
            return
        self._thumb_token += 1
        token = self._thumb_token
        self._clear_thumbnail("loading…")
        threading.Thread(target=self._load_thumbnail,
                         args=(photo.path, token), daemon=True).start()

    def _load_thumbnail(self, path: Path, token: int) -> None:
        """Runs off the UI thread; the result is picked up by _poll_thumbnails."""
        try:
            with Image.open(path) as img:
                img.thumbnail(THUMB_SIZE)
                self._thumb_queue.put((token, img.convert("RGB").copy()))
        except Exception:
            self._thumb_queue.put((token, None))

    def _schedule_tick(self) -> None:
        self._tick_job = self.root.after(120, self._on_tick)

    def _on_tick(self) -> None:
        self._tick_job = None          # this one has fired: nothing left to cancel
        self._poll_thumbnails()

    def _cancel_tick(self) -> None:
        if self._tick_job is None:
            return
        try:
            self.root.after_cancel(self._tick_job)
        except tk.TclError:
            pass                       # already fired, or the window has gone
        self._tick_job = None

    def _poll_thumbnails(self) -> None:
        """The app's single 120 ms tick, draining both background workers.

        Kept under its original name because tools/gui_drive_full.py pumps it
        by hand in several places. Those hand-pumped calls replace the pending
        tick rather than adding to it — otherwise every manual call would
        leave another chain of after() callbacks running behind it.
        """
        self._cancel_tick()
        self._drain_thumbnail_queue()
        self._drain_exif_queue()
        self._schedule_tick()

    def _drain_thumbnail_queue(self) -> None:
        try:
            while True:
                token, image = self._thumb_queue.get_nowait()
                if token != self._thumb_token:
                    continue                       # a newer row was selected
                if image is None:
                    self._clear_thumbnail("cannot preview this file")
                else:
                    self._thumb_image = ImageTk.PhotoImage(image)
                    self.thumb_canvas.delete("all")
                    self.thumb_canvas.create_image(THUMB_SIZE[0] / 2,
                                                   THUMB_SIZE[1] / 2,
                                                   image=self._thumb_image)
        except queue.Empty:
            pass

    # -- reacting to the controls -------------------------------------------

    def _on_mode_change(self) -> None:
        for panel in (self.panel_new, self.panel_insert, self.panel_replace):
            panel.grid_remove()
        active = {Mode.NEW_NAME.value: self.panel_new,
                  Mode.INSERT.value: self.panel_insert,
                  Mode.REPLACE.value: self.panel_replace}[self.var_mode.get()]
        active.grid(row=0, column=0, sticky="ew")
        self.refresh_preview()

    def _on_preset_change(self) -> None:
        preset = preset_module.PRESET_BY_LABEL.get(self.var_preset.get(),
                                                    preset_module.DEFAULT_PRESET)
        is_custom = preset.key == "custom"
        if not is_custom:
            self.var_pattern.set(preset.pattern)
        self.entry_pattern.configure(state="normal" if is_custom else "readonly")
        for chip in self.chip_buttons:
            chip.state(["!disabled"] if is_custom else ["disabled"])

        self.var_lower.set(preset.cleanup.lowercase)
        self.var_spaces.set(preset.cleanup.spaces_to_hyphens)
        self.var_accents.set(preset.cleanup.strip_accents)
        self.var_collapse.set(preset.cleanup.collapse_separators)

        self.var_event_label.set(preset.event_label)
        self.var_example.set(f"e.g.   {preset.example}")
        self.lbl_note.configure(text=preset.note)

        # A preset overwrites the pattern, which would take {place} with it.
        # Putting it straight back is what keeps the Position dropdown and
        # the pattern box telling the same story.
        if self.var_place_at.get() != PLACE_OFF:
            self._apply_place_position()

        state = "normal" if preset.needs_event or is_custom else "disabled"
        self.entry_event.configure(state=state)
        for widget in (self.spin_start, self.spin_digits):
            widget.configure(state="normal" if preset.uses_number or is_custom
                             else "disabled")
        self.refresh_preview()

    def _on_insert_position_change(self) -> None:
        position = INSERT_LABELS.get(self.var_insert_at.get())
        self.spin_nth.pack_forget()
        self.spin_char.pack_forget()
        if position is InsertPosition.AFTER_NTH_SEPARATOR:
            self.lbl_which.configure(text="After separator #")
            self.spin_nth.pack(side="left")
        elif position is InsertPosition.AT_CHAR:
            self.lbl_which.configure(text="After character")
            self.spin_char.pack(side="left")
        else:
            self.lbl_which.configure(text="")
        self.refresh_preview()

    # -- the Place panel ---------------------------------------------------

    @staticmethod
    def _moment_text(date_var: tk.StringVar, time_var: tk.StringVar) -> str:
        """Join the date box and the time box into one thing to parse.

        An empty time box is left off entirely, which is what gives a bare
        date its "the whole day" meaning in renamer.parse_stay.
        """
        date, time = date_var.get().strip(), time_var.get().strip()
        return f"{date} {time}" if date and time else date

    @staticmethod
    def _format_stay(stay: renamer.Stay) -> str:
        """How one row of the trip list reads: "15 Sep", "16 Sep – 20 Sep",
        "18 Sep 13:00–18:00"."""
        whole_day = ((stay.start.hour, stay.start.minute) == (0, 0)
                     and (stay.end.hour, stay.end.minute) == (23, 59))
        first = stay.start.strftime("%d %b").lstrip("0")
        last = stay.end.strftime("%d %b").lstrip("0")
        if whole_day:
            return first if first == last else f"{first} – {last}"
        if stay.start.date() == stay.end.date():
            return f"{first} {stay.start:%H:%M}–{stay.end:%H:%M}"
        return f"{first} {stay.start:%H:%M} – {last} {stay.end:%H:%M}"

    def _refresh_stay_rows(self) -> None:
        """Redraw the trip list. Row ids are the index into self.stays."""
        self.stay_tree.delete(*self.stay_tree.get_children())
        for index, stay in enumerate(self.stays):
            self.stay_tree.insert("", "end", iid=str(index),
                                  tags=(["stripe"] if index % 2 else []),
                                  values=(self._format_stay(stay),
                                          stay.place, "✕"))

    def _add_stay(self) -> None:
        try:
            stay = renamer.parse_stay(
                self._moment_text(self.var_from_date, self.var_from_time),
                self._moment_text(self.var_to_date, self.var_to_time),
                self.var_city.get())
        except ValueError as exc:
            # parse_stay writes its errors as sentences for exactly this line.
            self.var_status.set(str(exc))
            return

        self.stays.append(stay)
        self.stays.sort(key=lambda item: (item.start, item.end))
        if stay.place not in self.cities:
            self.cities.append(stay.place)
            self.cities.sort(key=str.lower)
            self.combo_city.configure(values=self.cities)
        self._refresh_stay_rows()
        self._sync_place_panel()
        self.var_status.set(f"{stay.place}: {self._format_stay(stay)}.")

        # A trip list the pattern never asks for would do nothing at all, so
        # the first stay switches the token on. It lands in the pattern box
        # where it can be seen, and Off puts it back.
        if self.var_place_at.get() == PLACE_OFF:
            self.var_place_at.set("End (suffix)")
            self._apply_place_position()
        self.refresh_preview()

    def _on_stay_click(self, event) -> None:
        if self.stay_tree.identify_region(event.x, event.y) != "cell":
            return
        if self.stay_tree.identify_column(event.x) != "#3":     # the ✕ column
            return
        iid = self.stay_tree.identify_row(event.y)
        if not iid:
            return
        index = int(iid)
        if 0 <= index < len(self.stays):
            gone = self.stays.pop(index)
            self._refresh_stay_rows()
            self._sync_place_panel()
            self.var_status.set(f"Removed {gone.place}.")
            self.refresh_preview()

    @staticmethod
    def _pattern_without_place(pattern: str) -> str:
        """The pattern with {place} and its leftover separator taken out."""
        out = re.sub(r"\{place\}[_-]?", "", pattern)
        out = re.sub(r"[_-]\{place\}", "", out)
        return out.strip("_-")

    def _apply_place_position(self) -> None:
        """Rewrite the pattern so {place} sits where the dropdown says.

        Rewriting the text rather than adding a placement mode is what keeps
        the preview and the pattern box from ever disagreeing — and it reuses
        the whole existing expansion path, so there is no new branch anywhere
        in renamer.py.
        """
        base = self._pattern_without_place(self.var_pattern.get())
        choice = self.var_place_at.get()
        if choice == "After the date":
            pattern, swapped = re.subn(r"(\{date8?\})", r"\1_{place}",
                                       base, count=1)
            if not swapped:              # no date token to sit after
                pattern = f"{{place}}_{base}" if base else "{place}"
        elif choice == "Beginning":
            pattern = f"{{place}}_{base}" if base else "{place}"
        elif choice == "End (suffix)":
            pattern = f"{base}_{{place}}" if base else "{place}"
        else:                            # Off
            pattern = base
        self.var_pattern.set(pattern)

    def _toggle_place_panel(self) -> None:
        self.var_place_open.set(not self.var_place_open.get())
        self._sync_place_panel(resize=True)

    def _sync_place_panel(self, *, resize: bool = False) -> None:
        """Show or hide the panel, and say on the strip which it is.

        resize=True is the click: the window grows by what the panel needs and
        shrinks back when it closes, so the panel takes its room from the
        screen rather than from the file list. It is left off when the window
        is first built, where the saved geometry is already the right size.
        """
        opening = self.var_place_open.get()
        if opening:
            self.place_body.grid()
            self.var_place_toggle.set("▾   Place")
        else:
            self.place_body.grid_remove()
            count = len(self.stays)
            trips = f"{count} trip{'s' if count != 1 else ''} set"
            self.var_place_toggle.set(
                f"▸   Place — {trips if count else 'name photos after where you were'}")
        # An open panel needs a taller window than the app's usual floor, or
        # the RENAME button ends up below the bottom edge.
        self.root.minsize(1010, 860 if opening else 720)
        if resize:
            self._resize_for_place_panel(opening)

    def _resize_for_place_panel(self, opening: bool) -> None:
        try:
            self.root.update_idletasks()
            needed = self.place_body.winfo_reqheight() + 10
            width, height = self.root.winfo_width(), self.root.winfo_height()
            if opening:
                # Remember what to go back to: on a short screen the growth
                # is capped, and subtracting the full panel height on the way
                # out would leave the window smaller than it started.
                self._height_without_place = height
                height = min(height + needed, self.root.winfo_screenheight() - 80)
            elif self._height_without_place is not None:
                height = self._height_without_place
            else:
                height = max(height - needed, self.root.minsize()[1])
            self.root.geometry(f"{width}x{height}")
        except tk.TclError:          # the window is going away
            pass

    def _on_place_position_change(self) -> None:
        self._apply_place_position()
        self.refresh_preview()

    def _insert_token(self, token: str) -> None:
        self.entry_pattern.insert(self.entry_pattern.index("insert"), token)

    # -- the two actions that touch disk ------------------------------------

    def do_rename(self) -> None:
        # The index is a cache of a folder listing, and a file can have
        # appeared in Explorer since it was taken. Everywhere else a stale
        # listing only costs an ugly preview; here it could mean missing a
        # collision and overwriting someone's file. So re-list and re-plan
        # once, and commit that. One scandir at commit time is irrelevant.
        self.dir_index.invalidate()
        self.refresh_preview()

        changing = [p for p in self.plans if p.changed]
        if not changing:
            self.var_status.set("Nothing to rename — the names already match.")
            return

        result = renamer.apply_renames(changing)
        self._apply_name_changes(result.moved)
        self.var_status.set(
            f"Renamed {len(result.renamed)} file"
            f"{'s' if len(result.renamed) != 1 else ''}"
            + (f" · {len(result.errors)} failed" if result.errors else
               " · undo is available"))
        if result.errors:
            detail = "\n".join(f"{name}: {why}" for name, why in result.errors[:12])
            messagebox.showwarning("Some files could not be renamed", detail)
        self._refresh_undo_button()

    def do_undo(self) -> None:
        result = renamer.undo_last()
        if not result.renamed and result.errors:
            self.var_status.set(result.errors[0][1] or "Nothing to undo.")
        else:
            self._apply_name_changes(result.moved)
            message = (f"Undone — {len(result.renamed)} file"
                       f"{'s' if len(result.renamed) != 1 else ''} restored")
            if result.errors:
                # The log keeps whatever could not be put back, so Undo stays
                # live and a second press finishes the job.
                message += (f" · {len(result.errors)} could not be restored "
                            f"— press Undo again once they are free")
            self.var_status.set(message)
        self._refresh_undo_button()

    def _apply_name_changes(self, moves: list[tuple[Path, Path]]) -> None:
        """Keep our in-memory list pointing at the files' new names.

        Keyed on the full path: two folders in one batch can easily both
        contain an IMG_0001.jpg, and matching on the bare name would point
        both rows at the same file.
        """
        # These resolve() calls happen once per rename, not per keystroke, so
        # they stay: the moves come back from disk as plain paths.
        # Fold the moves into the index before anything re-plans against it.
        self.dir_index.apply_moves(moves)
        moved = {old.resolve(): new for old, new in moves}
        for photo in self.files:
            resolved = photo.resolved
            new_path = moved.get(resolved)
            if new_path is None:
                continue
            was_checked = resolved in self.checked
            self.checked.discard(resolved)
            photo.relocate(new_path)
            if was_checked:
                self.checked.add(photo.resolved)
        self.files = renamer.sort_files(self.files)
        self._populate_tree()
        self.refresh_preview()

    def _refresh_undo_button(self) -> None:
        has_log = renamer.last_log() is not None
        self.button_undo.state(["!disabled"] if has_log else ["disabled"])


def main() -> None:
    set_dpi_awareness()
    root = TkinterDnD.Tk() if TkinterDnD is not None else tk.Tk()
    if sys.platform == "win32":
        # Match Tk's idea of a pixel to the monitor, so nothing looks tiny.
        root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
    set_window_icon(root)
    FotoRenamer(root)
    dark_title_bar(root)
    root.mainloop()


if __name__ == "__main__":
    main()
