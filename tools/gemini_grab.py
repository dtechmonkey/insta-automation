"""Pick up the image Gemini just downloaded and turn it into a 1080x1350 slide background.

  python tools/gemini_grab.py <dest.jpg> [--since <unix-seconds>] [--wait 40]

Waits for the newest Gemini_Generated_Image_* file in Downloads that is newer than --since, converts it
to a 4:5 JPEG (center crop) at <dest.jpg>, and moves the original into Downloads/_used/.
"""
import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import imageio_ffmpeg

# Chrome currently saves downloads into the parenting inbox (set by that project); check both.
DOWNLOAD_DIRS = [Path(__file__).resolve().parents[1] / "channels" / "parenting" / "images" / "inbox", Path.home() / "Downloads"]


def newest(since):
    files = [p for d in DOWNLOAD_DIRS if d.exists() for p in d.glob("Gemini_Generated_Image_*")
             if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") and p.stat().st_mtime > since]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dest")
    ap.add_argument("--since", type=float, default=time.time() - 120)
    ap.add_argument("--wait", type=int, default=40)
    a = ap.parse_args()
    deadline = time.time() + a.wait
    src = None
    while time.time() < deadline:
        src = newest(a.since)
        if src and src.stat().st_size > 50_000 and time.time() - src.stat().st_mtime > 1.5:
            break  # finished writing
        time.sleep(1)
    if not src:
        sys.exit("NO_FILE")
    dest = Path(a.dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(src),
                    "-vf", "scale=1080:1350:force_original_aspect_ratio=increase,crop=1080:1350",
                    "-q:v", "2", str(dest)], check=True)
    used = Path(__file__).resolve().parents[1] / "output" / "gemini-originals"  # out of the parenting inbox
    used.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), used / src.name)
    base = re.sub(r" \(\d+\)$", "", src.stem)  # "name (1)" -> "name": catch every copy of this image
    for dup in [*src.parent.glob(base + src.suffix), *src.parent.glob(base + " (*)" + src.suffix)]:
        if dup.exists():
            shutil.move(str(dup), used / dup.name)
    print(f"OK {dest}")


if __name__ == "__main__":
    main()
