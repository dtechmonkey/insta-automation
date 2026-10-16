"""Build the daily Reel (9:16 video) and Story image from the carousel's slides.

Reel: cover (3 s) + ~4 s of each story clip + the closing slide (2.5 s). Each 4:5 slide sits in the
middle of a 9:16 frame; a blurred, darkened copy of the same video fills the top and bottom.
Music: a royalty-free track from the channel's music folder (rotates daily); silent if none.
"""
import base64
import subprocess
from datetime import date
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import sync_playwright

W, H, SLIDE_H, FPS = 1080, 1920, 1350, 30
MUSIC_EXTENSIONS = (".mp3", ".m4a", ".aac", ".wav", ".ogg")


def _segments(out_dir, cover_seconds, story_seconds, outro_seconds):
    stills = sorted(Path(out_dir).glob("[0-9][0-9].jpg"))
    segments = []
    for i, still in enumerate(stills):
        clip = still.with_suffix(".mp4")
        last = i == len(stills) - 1
        if i == 0:
            segments.append((clip if clip.exists() else still, 0.0, cover_seconds))
        elif last:
            segments.append((still, 0.0, outro_seconds))
        else:  # skip the first 0.6 s so the text is already sliding in
            segments.append((clip, 0.6, story_seconds) if clip.exists() else (still, 0.0, story_seconds))
    return segments


def pick_music(music_dir):
    tracks = sorted(p for p in Path(music_dir).glob("*") if p.suffix.lower() in MUSIC_EXTENSIONS) \
        if music_dir and Path(music_dir).exists() else []
    return tracks[date.today().toordinal() % len(tracks)] if tracks else None


def build_reel(out_dir, music=None, cover_seconds=3.0, story_seconds=4.0, outro_seconds=2.5):
    out_dir = Path(out_dir)
    segments = _segments(out_dir, cover_seconds, story_seconds, outro_seconds)
    total = sum(d for _, _, d in segments)
    inputs, chains = [], []
    for i, (path, start, dur) in enumerate(segments):
        if path.suffix == ".mp4":
            inputs += ["-ss", str(start), "-t", str(dur), "-i", str(path)]
        else:
            inputs += ["-loop", "1", "-framerate", str(FPS), "-t", str(dur), "-i", str(path)]
        chains.append(f"[{i}:v]scale={W}:{SLIDE_H}:out_range=tv,setsar=1,fps={FPS},format=yuv420p[v{i}]")
    n = len(segments)
    graph = chains + [
        "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[cat]",
        "[cat]split[a][b]",
        f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=28:2,eq=brightness=-0.22[bg]",
        f"[bg][b]overlay=0:{(H - SLIDE_H) // 2},format=yuv420p[v]",
    ]
    if music:
        inputs += ["-i", str(music)]
        graph.append(f"[{n}:a]atrim=0:{total},asetpts=PTS-STARTPTS,afade=t=in:d=0.5,"
                     f"afade=t=out:st={total - 1.5}:d=1.5,volume=0.8[aud]")
        audio_map = "[aud]"
    else:
        inputs += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        audio_map = f"{n}:a"
    out = out_dir / "reel.mp4"
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", *inputs,
           "-filter_complex", ";".join(graph), "-map", "[v]", "-map", audio_map,
           "-t", str(total), "-r", str(FPS), "-c:v", "libx264", "-preset", "medium", "-crf", "21",
           "-profile:v", "high", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
           "-color_range", "tv", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True, timeout=600)
    return out


def build_story(out_dir, handle, n_stories):
    """A 9:16 Story image: today's cover on a blurred background with a 'NEW POST' banner."""
    out_dir = Path(out_dir)
    cover = base64.b64encode((out_dir / "01.jpg").read_bytes()).decode()
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Anton&family=Oswald:wght@600&display=swap" rel="stylesheet">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ width: {W}px; height: {H}px; background: #000; overflow: hidden; position: relative; color: #fff; }}
.bg {{ position: absolute; inset: -60px; background: url(data:image/jpeg;base64,{cover}) center/cover;
      filter: blur(40px) brightness(.45); }}
.card {{ position: absolute; left: 80px; top: 330px; width: 920px; border-radius: 28px;
        box-shadow: 0 30px 80px rgba(0,0,0,.6); }}
.banner {{ position: absolute; top: 150px; width: 100%; text-align: center; font-family: Anton, sans-serif;
          font-size: 92px; letter-spacing: .02em; }}
.banner span {{ background: #1E7BFF; padding: 4px 34px; border-radius: 14px; }}
.foot {{ position: absolute; bottom: 230px; width: 100%; text-align: center; font-family: Oswald, sans-serif;
        font-weight: 600; font-size: 46px; text-transform: uppercase; line-height: 1.25; }}
.foot b {{ color: #5FC8FF; text-transform: none; }}
</style></head><body>
<div class="bg"></div>
<div class="banner"><span>NEW POST</span></div>
<img class="card" src="data:image/jpeg;base64,{cover}">
<div class="foot">Today's {n_stories} stories are live<br>Visit <b>{handle}</b> to read them all</div>
</body></html>"""
    out = out_dir / "story.jpg"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H})
        page.set_content(html, wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        page.screenshot(path=str(out), type="jpeg", quality=90)
        browser.close()
    return out
