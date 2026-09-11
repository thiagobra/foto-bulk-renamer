# Screenshots

The app as it actually runs, captured from the committed code.

| Image | What it shows |
|---|---|
| [`01-new-name-mode.png`](01-new-name-mode.png) | The default view. The ★ *Date · Event · Number* preset, a live preview of 12 photos, the thumbnail pane, and the primary button reading **RENAME 12 FILES**. |
| [`02-insert-text-mode.png`](02-insert-text-mode.png) | *Insert text* mode putting "Beach Day" after the 1st separator: `IMG_beach-day_20260612_141101.jpg`. Note `IMG_` keeps its capitals — clean-up only tidies the text you type. The unticked row is dimmed and shows `—`. |
| [`03-find-and-replace-mode.png`](03-find-and-replace-mode.png) | *Find & replace*, `IMG` → `holiday`. The button reads 11 files, not 12, because the `.mp4` in the batch contains no "IMG" and is therefore unchanged. |
| [`04-custom-pattern.png`](04-custom-pattern.png) | The **Custom…** preset: the pattern box is unlocked and the token chips light up. Pattern `{date}_{event}_{n}_{cam}` appends the camera's last 4 digits. |
| [`05-convention-warning.png`](05-convention-warning.png) | The convention check turning amber — `⚠ spaces in name — hyphens sort and share better` — after the lowercase and hyphen switches were turned off. It warns, it never blocks. |
| [`06-after-rename-undo-available.png`](06-after-rename-undo-available.png) | Straight after renaming: the list shows the new names, **Undo last rename** is enabled, **RENAME** is greyed out because nothing is left to change, and the status reads `Renamed 13 files · undo is available`. |

## A note on how these were captured

They were taken on Linux under a virtual display (Xvfb) as part of verifying the UI, which is why
there is no window title bar in the images. On Windows 11 the app draws a dark title bar to match
the theme (`DwmSetWindowAttribute`, in `app.py`).

The photos in the shots are generated test images, not real ones — hence the small file sizes and
the identical mountains.
