# Screenshots

The app as it actually runs, captured from the committed code.

| Image | What it shows |
|---|---|
| [`01-new-name-mode.png`](01-new-name-mode.png) | The default view. The ★ *Date · Event · Number* preset with a live preview of 8 files drawn from **two different folders** — note the two separate `IMG_0001.JPG` rows, which get their own sequence numbers rather than colliding. The `.png` and `.mp4` have no EXIF, so they correctly show the file date instead of the shot date. Beside the pattern box, the same pattern drawn as draggable blocks — `date _ event _ n` — with the unused tokens greyed out after the `+`, and the example line above it coloured to match. |
| [`02-insert-text-mode.png`](02-insert-text-mode.png) | *Insert text* mode putting "praia" at the beginning. `IMG_` keeps its capitals — clean-up only tidies the text you type, never the rest of the original name. |
| [`03-find-and-replace-mode.png`](03-find-and-replace-mode.png) | *Find & replace*, `IMG` → `foto`. The button counts only the files that actually change: the `.png` and `.mp4` contain no "IMG", so they are left alone. |
| [`04-custom-pattern.png`](04-custom-pattern.png) | The **Custom…** preset: the pattern box is unlocked for typing. Pattern `{date8}_{event}_{n}_{cam}` appends the camera's last 4 digits — note the blocks and the colours in the example line following the typed pattern, and `cam` no longer offered as a spare. |
| [`05-convention-warning.png`](05-convention-warning.png) | The convention check turning amber — `⚠ spaces in name` — after the lowercase and hyphen switches were turned off. It warns, it never blocks. |
| [`06-after-rename-undo-available.png`](06-after-rename-undo-available.png) | Straight after renaming: the list shows the new names, **Undo last rename** is enabled, **RENAME** is greyed out because nothing is left to change, and the status reads `Renamed 8 files · undo is available`. |
| [`07-long-name-capped.png`](07-long-name-capped.png) | The Windows path-length guard. A very long Event name turns every affected row amber and shows `⚠ 8 name(s) shortened to stay under Windows' 260-character path limit`. The names are cut before anything touches the disk, not attempted and failed. |
| [`08-place-panel.png`](08-place-panel.png) | The **Place** panel open, with a trip declared: 15 Sep in New York City, 16–20 Sep in Boston, and one afternoon at Fenway Park. The narrower stay wins inside it, so the `{place}` at the end of the pattern resolves per photo — the line beside *Position* is the first ticked file as it will come out. Closed, the panel is the one-line strip in `01-new-name-mode.png`. |
| [`09-dragging-a-token.png`](09-dragging-a-token.png) | A drag in progress, caught with the mouse button still down: the `date` block is held (lighter), and the blue caret between `n` and the `+` shows where it would land. Dropping it there rewrites the pattern to `{event}_{n}_{date}` and every name in the list follows. Each token's colour is repeated in the example line above, which is how you can tell which block produced which characters. |

## A note on how these were captured

They were taken on Linux under a virtual display (Xvfb) as part of verifying
the UI, which is why there is no window title bar in the images. On Windows 11
the app draws a dark title bar to match the theme (`DwmSetWindowAttribute`, in
`app.py`) — that part has not been photographed, because it has not yet been
run on Windows.

Regenerate them all with:

```
xvfb-run -a python tools/gui_drive_full.py shots screenshots
```

The photos in the shots are generated test images, not real ones — hence the
small file sizes and the flat colours.
