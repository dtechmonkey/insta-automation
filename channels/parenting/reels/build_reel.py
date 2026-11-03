"""Assemble a reel from its folder: python build_reel.py r021-ziddi

Reads <folder>/reel.json. Clips come from Flow (<folder>/clips/clipN.mp4), audio from AI Studio, and all
text is rendered with Playwright in the brand fonts, as in r002-only-child/build.py.
Output: <folder>/reel.mp4, 1080x1920, 30 fps, H.264 + AAC.

reel.json keys:
  shots: [[clip name, seconds skipped, length on the timeline], ...]   the end card follows the last shot
  voice: optional path to the voiceover, relative to the folder
  voice_at: seconds into the reel where the voiceover starts (default 0)
  dialogue: optional [[path, start seconds], ...] for separate lines
  music: optional path; music_db sets its level (default -20 under voice, -9 with no voice)
  headlines, subtitles: [[start, end, html], ...]
  cta: {"keyword": "CALM", "line": "for the calm-down toolkit"}
  end_seconds: length of the end card (default 3)
"""
import json
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
W, H = 1080, 1920
FPS = 30
XF = 0.3  # dissolve length between shots
FADE = 0.2  # text fade in/out
CLIP_LEN = 8.0  # Omni clips run 8 s; a longer shot freezes on its final frame

FONTS = ('<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700'
         '&family=Nunito:wght@600;700;800&family=Noto+Sans+Devanagari:wght@600;700&family=Noto+Serif+Devanagari:wght@700&display=swap" rel="stylesheet">')
BASE_CSS = f"""html,body{{margin:0;width:{W}px;height:{H}px;background:transparent;overflow:hidden}}
em{{font-style:normal;color:#BC8B72}}"""

HEADLINE_HTML = """<style>{base}
.h{{position:absolute;left:70px;right:70px;top:230px;padding:34px 40px;border-radius:36px;text-align:center;
  background:rgba(247,242,232,.94);box-shadow:0 10px 36px rgba(112,92,75,.2);
  font-family:"Fraunces","Noto Serif Devanagari",serif;font-weight:700;font-size:66px;line-height:1.16;color:#5E4D40;
  text-transform:uppercase;letter-spacing:1px}}
</style><div class="h">{text}</div>"""

SUB_HTML = """<style>{base}
.s{{position:absolute;left:0;right:0;top:{top}px;text-align:center}}
.s span{{display:inline-block;max-width:900px;padding:18px 34px;border-radius:26px;background:rgba(61,52,44,.68);
  font-family:"Nunito","Noto Sans Devanagari",sans-serif;font-weight:700;font-size:46px;line-height:1.3;color:#FBF6EE}}
</style><div class="s"><span>{text}</span></div>"""

END_HTML = """<style>{base}
body{{background:#F7F2E8}}
.e{{position:absolute;inset:0 0 340px 0;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center}}
.c{{padding:44px 60px;border:5px solid #BC8B72;border-radius:44px;background:rgba(255,252,246,.8);margin-bottom:90px;max-width:900px;
  font-family:"Nunito","Noto Sans Devanagari",sans-serif;font-weight:800;font-size:54px;line-height:1.3;color:#705C4B}}
.c b{{display:block;font-family:"Fraunces",serif;font-size:118px;line-height:1.15;color:#BC8B72;letter-spacing:3px}}
.m{{font-family:"Fraunces",serif;font-weight:600;font-size:110px;letter-spacing:6px;color:#705C4B}}
.r{{width:160px;border-top:3px solid #BC8B72;margin:30px 0}}
.l{{font-family:"Fraunces",serif;font-weight:500;font-size:48px;color:#92977A;font-style:italic}}
.u{{font-family:"Nunito",sans-serif;font-weight:700;font-size:36px;color:#8A7766;margin-top:50px;letter-spacing:1px}}
</style><div class="e">{cta}<div class="m">LET ME TRY</div><div class="r"></div>
<div class="l">Little moments. Lifelong learning.</div><div class="u">@letmetry.montessori</div></div>"""


def render_overlays(folder, cfg, total, end_start):
    """Render every text layer to a transparent PNG; returns [(path, start, end)]."""
    jobs = [(HEADLINE_HTML, s, e, t, f"head{i}", 1270) for i, (s, e, t) in enumerate(cfg.get("headlines", []))]
    # a subtitle may carry a 4th value: its top position, to keep it off a face
    jobs += [(SUB_HTML, x[0], x[1], x[2], f"sub{i}", x[3] if len(x) > 3 else 1270) for i, x in enumerate(cfg.get("subtitles", []))]
    cta = cfg.get("cta")
    cta_html = f'<div class="c">Comment<b>{cta["keyword"]}</b>{cta["line"]}</div>' if cta else ""
    jobs.append((END_HTML.replace("{cta}", cta_html), end_start, total, "", "endcard", 1270))
    out_dir = folder / "overlays"
    out_dir.mkdir(exist_ok=True)
    layers = []
    with sync_playwright() as p:
        page = p.chromium.launch().new_page(viewport={"width": W, "height": H})
        for tpl, start, end, text, name, top in jobs:
            page.set_content(f"<html><head>{FONTS}</head><body>{tpl.format(base=BASE_CSS, text=text, top=top)}</body></html>",
                             wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            path = out_dir / f"{name}.png"
            page.screenshot(path=str(path), omit_background=True)
            layers.append((path, start, end))
    return layers


def build(name):
    folder = ROOT / name
    cfg = json.loads((folder / "reel.json").read_text(encoding="utf8"))
    shots = cfg["shots"]
    end_seconds = cfg.get("end_seconds", 3)
    end_start = sum(s[2] for s in shots)
    total = end_start + end_seconds
    layers = render_overlays(folder, cfg, total, end_start)
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    inputs, graph = [], []
    last = len(shots) - 1
    for i, (clip, skip, length) in enumerate(shots):
        seg = length + (XF if i < last else 0)
        inputs += ["-ss", str(skip), "-t", f"{min(seg, CLIP_LEN - skip):.3f}", "-i", str(folder / "clips" / f"{clip}.mp4")]
        hold = max(seg - (CLIP_LEN - skip), 0)
        graph.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},"
                     f"setsar=1,format=yuv420p,fps={FPS},tpad=stop_mode=clone:stop_duration={hold + 0.1:.3f},"
                     f"trim=duration={seg:.3f},setpts=PTS-STARTPTS,fps={FPS},settb=AVTB[s{i}]")
    # dissolve each shot into the next exactly at its cut point
    cur, t = "s0", 0.0
    for i in range(1, len(shots)):
        t += shots[i - 1][2]
        graph.append(f"[{cur}][s{i}]xfade=transition=fade:duration={XF}:offset={t:.3f}[x{i}]")
        cur = f"x{i}"
    n = len(shots)
    idx = n
    # video runs under the end card, which fades in over the last frame held
    graph.append(f"[{cur}]tpad=stop_mode=clone:stop_duration={end_seconds + 0.1:.3f},trim=duration={total:.3f},"
                 f"setpts=PTS-STARTPTS,fps={FPS},settb=AVTB[base]")
    cur = "base"
    audio = []  # (input index, start seconds, gain dB, resample chain)
    voice_lines = list(cfg.get("dialogue", []))
    if cfg.get("voice"):
        voice_lines.append([cfg["voice"], cfg.get("voice_at", 0)])
    for path, start in voice_lines:
        inputs += ["-i", str(folder / path)]
        audio.append((idx, start, 0))
        idx += 1
    if cfg.get("music"):
        inputs += ["-stream_loop", "-1", "-i", str(folder / cfg["music"])]
        audio.append((idx, 0, cfg.get("music_db", -20 if voice_lines else -9)))
        idx += 1
    for j, (path, start, end) in enumerate(layers):
        dur = end - start
        inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", str(path)]
        fades = f"fade=t=in:st=0:d={FADE}:alpha=1,fade=t=out:st={max(dur - FADE, 0):.3f}:d={FADE}:alpha=1"
        if path.stem == "endcard":
            fades = "fade=t=in:st=0:d=0.4:alpha=1"
        graph.append(f"[{idx}:v]format=rgba,{fades},setpts=PTS+{start:.3f}/TB[l{j}]")
        graph.append(f"[{cur}][l{j}]overlay=0:0:eof_action=pass[o{j}]")
        cur = f"o{j}"
        idx += 1
    graph.append(f"[{cur}]format=yuv420p[vout]")
    labels = []
    for k, (input_idx, start, gain) in enumerate(audio):
        delay = int(start * 1000)
        graph.append(f"[{input_idx}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={delay}|{delay},"
                     f"volume={gain}dB,atrim=duration={total:.3f}[a{k}]")
        labels.append(f"[a{k}]")
    if labels:
        mix = f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0" if len(labels) > 1 else f"{labels[0]}anull"
        graph.append(f"{mix},afade=t=out:st={total - 0.5:.3f}:d=0.5,loudnorm=I=-15:TP=-1.5:LRA=11,aresample=48000,"
                     f"apad,atrim=duration={total:.3f}[aout]")
        audio_args = ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    else:
        audio_args = []
    cmd = [ff, "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(graph),
           "-map", "[vout]", *audio_args, "-t", f"{total:.3f}", "-r", str(FPS),
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", str(folder / "reel.mp4")]
    subprocess.run(cmd, check=True)
    print(f"{name}/reel.mp4 written ({total:.2f} s)")


if __name__ == "__main__":
    build(sys.argv[1])
