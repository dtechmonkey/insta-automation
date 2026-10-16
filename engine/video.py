"""Turn slides into short MP4 clips for mixed photo/video carousels.

Method 1 (animated slide): slow zoom on the still background, then the text fades and slides in.
Method 2 (AI motion): if bg/NN_motion.mp4 exists (e.g. an image-to-video clip), it replaces the
still background and the same fade/text layers go on top.
"""
import subprocess
from pathlib import Path

import imageio_ffmpeg
from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright

W, H, FPS = 1080, 1350, 30


def render_layers(slide, templates_dir, base_context, out_dir, name):
    """Render the slide's dark fade and its text as two transparent PNGs (same layout as the still)."""
    env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=select_autoescape(["html"]))
    paths = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H})
        for layer in ("fade", "text"):
            html = env.get_template(slide["template"]).render(**base_context, **slide, layer=layer)
            page.set_content(html, wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            page.evaluate("window.fitText && window.fitText()")
            path = Path(out_dir) / f"{name}_{layer}.png"
            page.screenshot(path=str(path), type="png", omit_background=True)
            paths[layer] = path
        browser.close()
    return paths["fade"], paths["text"]


def make_clip(background, fade_png, text_png, out_mp4, seconds=6.0, animate_text=True):
    """Compose background (image or video) + fade + text into an Instagram-ready MP4.
    animate_text=False keeps the text fully visible from the first frame: used for the cover, because
    Instagram uses the first frame of a carousel's first video as the grid thumbnail."""
    background = Path(background)
    frames = int(seconds * FPS)
    if background.suffix.lower() in (".mp4", ".mov", ".webm"):
        bg_input = ["-stream_loop", "-1", "-i", str(background)]
        base = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"fps={FPS},trim=duration={seconds},setpts=PTS-STARTPTS[base]")
    else:
        bg_input = ["-i", str(background)]
        # Upscale first so the slow zoom stays smooth; zoom toward the upper part where subjects sit.
        base = (f"[0:v]scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
                f"zoompan=z='1+0.10*on/{frames}':x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*0.35':"
                f"d={frames}:s={W}x{H}:fps={FPS}[base]")
    if animate_text:  # text fades in and slides up into place
        text = ["[2:v]format=rgba,fade=t=in:st=0.4:d=0.8:alpha=1[txt]",
                "[faded][txt]overlay=x=0:y='if(lt(t,0.4),36,max(0,36-40*(t-0.4)))':eval=frame,format=yuv420p[v]"]
    else:  # static text over the moving background
        text = ["[faded][2:v]overlay=0:0,format=yuv420p[v]"]
    graph = ";".join([base, "[base][1:v]overlay=0:0[faded]", *text])
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
           *bg_input,
           "-loop", "1", "-framerate", str(FPS), "-i", str(fade_png),
           "-loop", "1", "-framerate", str(FPS), "-i", str(text_png),
           "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
           "-filter_complex", graph, "-map", "[v]", "-map", "3:a",
           "-t", str(seconds), "-r", str(FPS),
           "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-profile:v", "high", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-movflags", "+faststart", str(out_mp4)]
    subprocess.run(cmd, check=True)
    return Path(out_mp4)


def make_clips(slides, slide_numbers, bg_paths, templates_dir, base_context, out_dir, seconds=6.0):
    """slide_numbers are 1-based; bg_paths[i] is the background (image, or *_motion.mp4) for slide i+1."""
    work = Path(out_dir) / "video_layers"
    work.mkdir(exist_ok=True)
    made = []
    for n in slide_numbers:
        bg = bg_paths[n - 1]
        if bg is None or not Path(bg).exists():
            print(f"  slide {n}: no background image, skipping video")
            continue
        fade, text = render_layers(slides[n - 1], templates_dir, base_context, work, f"{n:02d}")
        # Slide 1 (cover) keeps its text static so the grid thumbnail shows the full cover.
        out = make_clip(bg, fade, text, Path(out_dir) / f"{n:02d}.mp4", seconds, animate_text=n != 1)
        print(f"  slide {n}: {out.name} ({'video background' if Path(bg).suffix == '.mp4' else 'animated still'})")
        made.append(out)
    return made
