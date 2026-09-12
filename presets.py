"""Naming presets, as plain data.

Each preset is one dict-like object, so adding your own is a two-line edit at
the bottom of PRESETS. The patterns follow the naming practice that current
photography and records-management guidance agrees on:

  * ISO 8601 date first (2026-06-12) so alphabetical order IS chronological
  * a short subject in the middle
  * a zero-padded sequence at the end (007 sorts correctly, 7 does not)
  * only a-z 0-9 - _ .  — no spaces, no accents
  * "_" separates blocks, "-" separates words inside a block
  * keeping the camera's last 4 digits lets a file be traced back to the card

Sources are listed in README.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from renamer import CleanupOptions, Mode

# The sample used to render the "you'll get …" example next to each preset.
EXAMPLE_EVENT = "lakeside wedding"
EXAMPLE_ORIG = "IMG_20260612_142233"


@dataclass(frozen=True)
class Preset:
    key: str
    label: str            # what the dropdown shows
    pattern: str          # tokens from renamer.TOKEN_HELP
    example: str          # rendered result, shown under the dropdown
    event_label: str = "Event"
    event_hint: str = "e.g. lakeside wedding"
    needs_event: bool = True
    uses_number: bool = True
    cleanup: CleanupOptions = CleanupOptions()
    mode: Mode = Mode.NEW_NAME
    note: str = ""


# Underscore-separated blocks, hyphenated words: the recommended default.
_STANDARD = CleanupOptions(lowercase=True, spaces_to_hyphens=True,
                           strip_accents=True, collapse_separators=True)

PRESETS: list[Preset] = [
    Preset(
        key="date_event_n",
        label="★  Date · Event · Number",
        pattern="{date}_{event}_{n}",
        example="2026-06-12_lakeside-wedding_014.jpg",
        cleanup=_STANDARD,
        note="The recommended default: sorts chronologically, says what it is.",
    ),
    Preset(
        key="date_event_n_cam",
        label="Date · Event · Number · Camera ID",
        pattern="{date}_{event}_{n}_{cam}",
        example="2026-06-12_lakeside-wedding_014_2233.jpg",
        cleanup=_STANDARD,
        note="Keeps the camera's last 4 digits so a file traces back to the card.",
    ),
    Preset(
        key="date8_n",
        label="Date · Number  (no subject needed)",
        pattern="{date8}_{n}",
        example="20260612_014.jpg",
        needs_event=False,
        cleanup=_STANDARD,
        note="Shortest name that still sorts by date.",
    ),
    Preset(
        key="date_time",
        label="Date · Time  (unique without a counter)",
        pattern="{date}_{time}",
        example="2026-06-12_14-22-33.jpg",
        needs_event=False,
        uses_number=False,
        cleanup=_STANDARD,
        note="Two photos in the same second would clash — then it adds (1).",
    ),
    Preset(
        key="event_n",
        label="Event · Number  (folder already dated)",
        pattern="{event}_{n}",
        example="lakeside-wedding_014.jpg",
        cleanup=_STANDARD,
    ),
    Preset(
        key="web_slug",
        label="Web slug  (lowercase, hyphens only)",
        pattern="{event}-{n}",
        example="lakeside-wedding-014.jpg",
        cleanup=_STANDARD,
        note="Upload-friendly: no underscores, no capitals, no spaces.",
    ),
    Preset(
        key="prefix_keep",
        label="Keep original, add prefix",
        pattern="{event}_{orig}",
        example="lakeside-wedding_IMG_20260612_142233.jpg",
        uses_number=False,
        cleanup=CleanupOptions(lowercase=False, spaces_to_hyphens=True,
                               strip_accents=True, collapse_separators=True),
        note="Non-destructive: the camera name stays fully intact.",
    ),
    Preset(
        key="suffix_keep",
        label="Keep original, add suffix",
        pattern="{orig}_{event}",
        example="IMG_20260612_142233_edit.jpg",
        event_label="Suffix",
        event_hint="edit / web / print",
        uses_number=False,
        cleanup=CleanupOptions(lowercase=False, spaces_to_hyphens=True,
                               strip_accents=True, collapse_separators=True),
        note="For derivatives — better than version numbers like _v2.",
    ),
    Preset(
        key="custom",
        label="Custom…",
        pattern="{date}_{event}_{n}",
        example="your pattern, your rules",
        cleanup=_STANDARD,
        note="Unlocks the pattern box. Click a token chip to insert it.",
    ),
]

PRESETS_BY_KEY = {p.key: p for p in PRESETS}
PRESET_LABELS = [p.label for p in PRESETS]
PRESET_BY_LABEL = {p.label: p for p in PRESETS}
DEFAULT_PRESET = PRESETS[0]
