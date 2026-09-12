"""Build a realistic photo tree to drive the GUI against."""
import shutil, sys
from datetime import datetime
from pathlib import Path
from PIL import Image

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("fixtures")
if ROOT.exists():
    shutil.rmtree(ROOT)

def jpg(path: Path, colour, when: str | None, *, created: str | None = None,
        modified: str | None = None):
    """A JPEG carrying capture dates.

    `when` is the ordinary case: DateTimeOriginal and DateTime both set, which
    is what a file straight off a camera card looks like. `created` and
    `modified` write CreateDate (36868) and DateTime (306) on their own, for
    the photo-through-an-editor case where the two disagree.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (320, 240), colour)
    if when or created or modified:
        exif = img.getexif()
        ifd = exif.get_ifd(0x8769)      # Exif sub-IFD
        if when:
            ifd[36867] = when           # DateTimeOriginal
            exif[306] = when            # DateTime
        if created:
            ifd[36868] = created        # CreateDate
        if modified:
            exif[306] = modified        # DateTime, rewritten by an editor
        img.save(path, "JPEG", exif=exif)
    else:
        img.save(path, "JPEG")

def box(kind: bytes, payload: bytes) -> bytes:
    return (len(payload) + 8).to_bytes(4, "big") + kind + payload

def mp4(path: Path, when: datetime):
    """A real (tiny) MP4 whose moov/mvhd holds a known capture time."""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = int((when - datetime(1904, 1, 1)).total_seconds())
    mvhd = (b"\x00\x00\x00\x00" + raw.to_bytes(4, "big") * 2
            + (1000).to_bytes(4, "big") + (5000).to_bytes(4, "big")
            + b"\x00" * 80)
    path.write_bytes(box(b"ftyp", b"mp42\x00\x00\x00\x00mp42isom")
                     + box(b"moov", box(b"mvhd", mvhd)))

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
# Card C: a trip across two days, for the Place panel — and the two files
# whose dates the old metadata layer got wrong.
#   clip.mp4      a real container, so its date comes from moov/mvhd
#   edited.jpg    CreateDate 15 Sep, ModifyDate 1 Nov, as a resize tool leaves it
jpg(ROOT / "cardC" / "IMG_2001.JPG", (200, 120, 40), "2026:09:15 10:00:00")
jpg(ROOT / "cardC" / "IMG_2002.JPG", (40, 120, 200), "2026:09:17 11:30:00")
jpg(ROOT / "cardC" / "edited.jpg", (120, 40, 200), None,
    created="2026:09:15 12:00:00", modified="2026:11:01 08:00:00")
mp4(ROOT / "cardC" / "clip.mp4", datetime(2026, 9, 17, 14, 22, 33))
print(ROOT.resolve())
