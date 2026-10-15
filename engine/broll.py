"""Find a real, freely licensed video clip ("b-roll") for a story.

Providers, tried in order (config video.broll_providers):
  nasa    - NASA Image and Video Library (no key; space stories only). Credit: "Courtesy NASA".
  pexels  - Pexels videos (free key PEXELS_API_KEY; free to use, no attribution required).
  pixabay - Pixabay videos (free key PIXABAY_API_KEY; free to use, no attribution required).

Only a short segment is downloaded (ffmpeg reads the remote file with range requests), re-encoded
to a small local MP4 that video.make_clip() then uses as a moving slide background.
"""
import os
import subprocess
from pathlib import Path

import imageio_ffmpeg
import requests

TIMEOUT = 30
MIN_SECONDS = 5
MAX_CANDIDATES = 3
MAX_DOWNLOAD_MB = 60


def _nasa(query):
    r = requests.get("https://images-api.nasa.gov/search",
                     params={"q": query, "media_type": "video"}, timeout=TIMEOUT)
    r.raise_for_status()
    found = []
    for item in r.json().get("collection", {}).get("items", [])[:8]:
        if len(found) >= MAX_CANDIDATES:
            break
        data = (item.get("data") or [{}])[0]
        try:
            assets = requests.get(item["href"], timeout=TIMEOUT).json()
        except (requests.RequestException, ValueError, KeyError):
            continue
        mp4s = [a for a in assets if a.lower().endswith(".mp4")]
        for suffix in ("~medium.mp4", "~mobile.mp4", "~orig.mp4"):
            url = next((a for a in mp4s if a.endswith(suffix)), None)
            if url:
                # NASA videos open with a title card ("slate"): start well past it.
                url = url.replace("http://", "https://")
                length = _duration(url)
                if length is None or length < 15:
                    continue
                found.append({"url": url, "start": round(min(max(12, length * 0.4), length - 8), 1),
                              "provider": "NASA", "credit": "Courtesy NASA", "title": data.get("title", ""),
                              "remote": True})  # big files: read only the segment we need
                break
    return found


def _pexels(query):
    key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not key:
        return []
    r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": key},
                     params={"query": query, "per_page": 15, "size": "medium"}, timeout=TIMEOUT)
    r.raise_for_status()
    found = []
    for v in r.json().get("videos", []):
        if v.get("duration", 0) < MIN_SECONDS:
            continue
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4"
                 and min(f.get("width") or 0, f.get("height") or 0) >= 720]
        if not files:
            continue
        best = min(files, key=lambda f: abs(min(f["width"], f["height"]) - 1080))
        found.append({"url": best["link"], "start": 0, "provider": "Pexels",
                      "credit": f"{v.get('user', {}).get('name', 'Pexels')} / Pexels", "title": v.get("url", "")})
    return found[:MAX_CANDIDATES]


def _pixabay(query):
    key = os.environ.get("PIXABAY_API_KEY", "").strip()
    if not key:
        return []
    r = requests.get("https://pixabay.com/api/videos/",
                     params={"key": key, "q": query, "per_page": 20, "safesearch": "true"}, timeout=TIMEOUT)
    r.raise_for_status()
    found = []
    for hit in r.json().get("hits", []):
        if hit.get("duration", 0) < MIN_SECONDS:
            continue
        for size in ("large", "medium"):
            f = hit.get("videos", {}).get(size) or {}
            if f.get("url") and min(f.get("width") or 0, f.get("height") or 0) >= 720:
                found.append({"url": f["url"], "start": 0, "provider": "Pixabay",
                              "credit": f"{hit.get('user', 'Pixabay')} / Pixabay", "title": hit.get("pageURL", "")})
                break
    return found[:MAX_CANDIDATES]


PROVIDERS = {"nasa": _nasa, "pexels": _pexels, "pixabay": _pixabay}


def _duration(url):
    """Length in seconds of a remote video (reads only the file header), or None."""
    import re
    out = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-i", url], capture_output=True, text=True,
                         timeout=60).stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", out)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None


def _download_whole(url, path):
    with requests.get(url, stream=True, timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0"}) as r:
        r.raise_for_status()
        size = 0
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                size += len(chunk)
                if size > MAX_DOWNLOAD_MB << 20:
                    raise OSError("clip too large")
                f.write(chunk)
    return path


def _download_segment(candidate, seconds, out_mp4):
    """Save a short segment of the clip as a small local MP4 (no audio)."""
    source, tmp = candidate["url"], None
    if not candidate.get("remote"):
        tmp = Path(out_mp4).with_suffix(".download.mp4")
        source = str(_download_whole(candidate["url"], tmp))
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
           "-ss", str(candidate["start"]), "-t", str(seconds + 1), "-i", source,
           "-an", "-vf", "scale=1350:1350:force_original_aspect_ratio=increase",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(out_mp4)]
    try:
        subprocess.run(cmd, check=True, timeout=180)
    finally:
        if tmp and tmp.exists():
            tmp.unlink()
    return Path(out_mp4).exists() and Path(out_mp4).stat().st_size > 50_000


def _still_frame(mp4, out_jpg):
    """A frame from the clip, used as the slide's still background (previews, fallbacks)."""
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-ss", "1", "-i", str(mp4),
           "-frames:v", "1", "-q:v", "2", str(out_jpg)]
    subprocess.run(cmd, check=True, timeout=60)


def fetch(query, space, providers, motion_path, still_path, seconds=6, used=None):
    """Find and save a clip for one story. Returns a credit dict or None.
    `used` is a set of source URLs already taken in this post (avoids repeating the same clip)."""
    used = used if used is not None else set()
    for name in providers:
        if name == "nasa" and not space:
            continue
        try:
            candidates = PROVIDERS[name](query)
        except (requests.RequestException, ValueError) as e:
            print(f"    ! {name}: {type(e).__name__}")
            continue
        for found in candidates:
            if found["url"] in used:
                continue
            try:
                if _download_segment(found, seconds, motion_path):
                    _still_frame(motion_path, still_path)
                    used.add(found["url"])
                    return found
            except (subprocess.SubprocessError, OSError, requests.RequestException) as e:
                print(f"    ! {name} download failed ({type(e).__name__}), trying next clip")
    return None
