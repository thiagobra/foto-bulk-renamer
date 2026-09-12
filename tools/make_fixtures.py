"""Build a realistic photo tree to drive the GUI against."""
import shutil, sys
from pathlib import Path
from datetime import datetime
from PIL import Image

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("fixtures")
if ROOT.exists():
    shutil.rmtree(ROOT)

def jpg(path: Path, colour, when: str | None):
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (320, 240), colour)
    if when:
        exif = img.getexif()
        ifd = exif.get_ifd(0x8769)      # Exif sub-IFD
        ifd[36867] = when               # DateTimeOriginal
        exif[306] = when                # DateTime
        img.save(path, "JPEG", exif=exif)
    else:
        img.save(path, "JPEG")

# Card A: three shots with EXIF, out of order on disk
jpg(ROOT / "cardA" / "IMG_0003.JPG", (200, 60, 60),  "2026:06:12 14:22:33")
jpg(ROOT / "cardA" / "IMG_0001.JPG", (60, 200, 60),  "2026:06:12 14:20:11")
jpg(ROOT / "cardA" / "IMG_0010.JPG", (60, 60, 200),  "2026:06:12 14:21:05")
# Card B: SAME file names as card A — the multi-folder trap
jpg(ROOT / "cardB" / "IMG_0001.JPG", (240, 200, 40), "2026:06:12 09:00:00")
jpg(ROOT / "cardB" / "IMG_0003.JPG", (40, 200, 240), "2026:06:12 09:05:00")
# No-EXIF png + a video + an unsupported file
Image.new("RGB", (200, 150), (120, 120, 120)).save(ROOT / "cardA" / "Screenshot 2026.png")
(ROOT / "cardA" / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
(ROOT / "cardA" / "notes.txt").write_text("not a photo")
# Accented + spaces, to exercise cleanup
jpg(ROOT / "cardA" / "Férias  na Praia.jpg", (10, 160, 90), "2026:06:12 16:00:00")
print(ROOT.resolve())
