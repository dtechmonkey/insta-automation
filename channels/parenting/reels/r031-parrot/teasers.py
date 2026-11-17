"""Story teasers for r031 (each about 14 s, 1080x1920). Run from the reels folder:
python r031-parrot/teasers.py 2        -> teaser2.mp4 (cut from reel.mp4)
python r031-parrot/teasers.py 1        -> teaser1.mp4 (mother's question clip + flashes), needs clips2/Q.mp4"""
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parents[1]))
import build_reel as br  # noqa: E402

D = Path(__file__).parent
FF = imageio_ffmpeg.get_ffmpeg_exe()
T = D / "teaser"
T.mkdir(exist_ok=True)
END = 3.5
XF = 0.25

PILL = """<style>{base}
.p{{position:absolute;left:90px;right:90px;top:505px;padding:28px 40px;border-radius:40px;text-align:center;
  background:rgba(94,77,64,.92);box-shadow:0 10px 36px rgba(60,45,35,.3);
  font-family:"Fraunces",serif;font-weight:700;font-size:62px;line-height:1.15;color:#FBF6EE}}
.p small{{display:block;font-family:"Nunito",sans-serif;font-weight:800;font-size:40px;color:#F2D7C4;margin-top:12px;letter-spacing:1px}}
</style><div class="p">{text}</div>"""


def render(html, text, out, top=1270):
    with sync_playwright() as p:
        pg = p.chromium.launch().new_page(viewport={"width": 1080, "height": 1920})
        pg.set_content(f"<html><head>{br.FONTS}</head><body>{html.format(base=br.BASE_CSS, text=text, top=top)}</body></html>",
                       wait_until="networkidle")
        pg.evaluate("document.fonts.ready")
        pg.screenshot(path=str(out), omit_background=True)


def build(parts, out, subs=()):
    """parts: [(path, start, dur, vf_extra)]; then the end card. subs: [(png, start, end)] on the joined timeline"""
    render(PILL, "The full reel is live<small>👉 ON OUR PROFILE</small>", T / "pill.png")
    args, v, a = [], [], []
    for i, (path, s, dur, vf) in enumerate(parts):
        args += ["-ss", str(s), "-t", str(dur), "-i", str(path)]
        v.append(f"[{i}:v]{vf}scale=1080:1920,setsar=1,fps=30,format=yuv420p,settb=AVTB[v{i}]")
        a.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
    n = len(parts)
    args += ["-loop", "1", "-t", str(END), "-i", str(D / "cover" / "cover_1080x1920.jpg"),
             "-loop", "1", "-t", str(END), "-i", str(T / "pill.png"),
             "-ss", "20", "-t", str(END + 1), "-i", str(D.parent / "_music" / "warm.mp3")]
    v.append(f"[{n}:v]scale=1080:1920,setsar=1,fps=30,format=rgba[cv];[{n + 1}:v]format=rgba,fade=t=in:st=0.4:d=0.4:alpha=1[pl];"
             f"[cv][pl]overlay=0:0,format=yuv420p,settb=AVTB[v{n}]")
    a.append(f"[{n + 2}:a]aresample=48000,aformat=channel_layouts=stereo,volume=-14dB,afade=t=in:d=0.3,"
             f"afade=t=out:st={END - 0.7}:d=0.7[a{n}]")
    durs = [p[2] for p in parts] + [END]
    chain, cur, acur, off = [], "v0", "a0", 0.0
    for i in range(1, n + 1):
        off += durs[i - 1] - XF
        chain.append(f"[{cur}][v{i}]xfade=transition=fade:duration={XF}:offset={off:.3f}[x{i}]")
        chain.append(f"[{acur}][a{i}]acrossfade=d={XF}[y{i}]")
        cur, acur = f"x{i}", f"y{i}"
    k = n + 3
    for j, (png, s, e) in enumerate(subs):
        args += ["-loop", "1", "-t", f"{e - s:.2f}", "-i", str(png)]
        chain.append(f"[{k + j}:v]format=rgba,fade=t=in:d=0.15:alpha=1,setpts=PTS+{s}/TB[s{j}];[{cur}][s{j}]overlay=0:0:eof_action=pass[o{j}]")
        cur = f"o{j}"
    total = sum(durs) - XF * n
    graph = ";".join(v + a + chain) + f";[{acur}]loudnorm=I=-15:TP=-1.5,atrim=duration={total:.2f}[aout]"
    subprocess.run([FF, "-y", "-loglevel", "error", *args, "-filter_complex", graph, "-map", f"[{cur}]", "-map", "[aout]",
                    "-t", f"{total:.2f}", "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)], check=True)
    print(out.name, round(total, 2), "s")


reel = D / "reel.mp4"
which = sys.argv[1]
if which == "2":
    build([(reel, 3.6, 3.8, ""),      # "Phir se... har baar mere saath hi kyun?" (subtitled)
           (reel, 9.8, 2.7, ""),      # "Laa, main khila deti hoon" + the first snip
           (reel, 45.6, 2.3, ""),     # the cat leaping, frozen mid-air
           (reel, 50.3, 2.4, "")],    # "Let me try!"
          D / "teaser2.mp4")
else:
    q = D / "clips2" / "Q.mp4"
    qs, qd = float(sys.argv[2]), float(sys.argv[3])   # where the hug + question sit in Q.mp4
    render(br.SUB_HTML, "“Hum unke pankh kaatein…<br>ya unhe udna sikhayein?”<br><small>Do we clip their wings… or teach them to fly?</small>",
           T / "qsub.png", top=1480)
    sub_s, sub_e = float(sys.argv[4]), float(sys.argv[5])
    build([(q, qs, qd, "delogo=x=856:y=1696:w=88:h=88,"),
           (reel, 11.0, 1.6, ""),     # a snip
           (reel, 46.0, 1.9, ""),     # the cat mid-air
           (reel, 50.3, 2.4, "")],    # "Let me try!"
          D / "teaser1.mp4", subs=[(T / "qsub.png", sub_s, sub_e)])
