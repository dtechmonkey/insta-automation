"""r031 v3: continuous top story (A snips, B praise+leave, C cat, D escape, E return) over a cut bottom story,
framed panels on a cream page, crossfades inside each panel, a 2.5 s rewind. Writes clips/base.mp4,
clips/base_sfx.wav and reel.json. Run from the reels folder: python r031-parrot/compose3.py"""
import json
import subprocess
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

D = Path(__file__).parent
C = D / "clips2"
FF = imageio_ffmpeg.get_ffmpeg_exe()
W, H, FPS = 1080, 1920, 30
PW, PH, R = 1000, 780, 34
P1, P2 = (40, 130), (40, 940)
XF = 0.3
CREAM = (247, 242, 232)
GRADE = "colorbalance=rs=0.03:bs=-0.04:rm=0.02:bm=-0.03,eq=saturation=0.96"
FREEZE = {"TC": 14.0}   # the cat is mid-air here: hold this frame
DELOGO = "delogo=x=856:y=1696:w=88:h=88,"   # sparkle on portrait clips

# ---------- page ----------
rng = np.random.default_rng(1)
BG = Image.fromarray(np.clip(np.array(CREAM, np.float32) + rng.normal(0, 3.0, (H, W, 1)), 0, 255).astype(np.uint8))
d = ImageDraw.Draw(BG)
font = ImageFont.truetype("C:/Windows/Fonts/georgiai.ttf", 30)
label = "Let Me Try  ·  @letmetry.montessori"
d.text(((W - d.textlength(label, font=font)) / 2, 62), label, font=font, fill=(146, 151, 122))


def rounded(w, h, r):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], r, fill=255)
    return m


MASK = rounded(PW, PH, R)
for (x, y) in (P1, P2):
    sh = Image.new("L", (W, H), 0)
    ImageDraw.Draw(sh).rounded_rectangle([x + 4, y + 10, x + PW + 4, y + PH + 10], R, fill=70)
    BG = Image.composite(Image.new("RGB", (W, H), (112, 92, 75)), BG, sh.filter(ImageFilter.GaussianBlur(14)))
    ImageDraw.Draw(BG).rounded_rectangle([x - 5, y - 5, x + PW + 4, y + PH + 4], R + 5, fill=(188, 139, 114))


def is_portrait(clip):
    return clip.startswith("H_")


def reader(clip, s, n, f=1.0, size=(PW, PH), py=None, grade=True):
    """n frames from clip starting at s, slowed by f, last frame held"""
    w, h = size
    if is_portrait(clip):
        ch = round(1080 * h / w)
        crop = f"{DELOGO}crop=1080:{ch}:0:{py if py is not None else max(0, (1920 - ch) // 2)}"
    else:
        cw = round(1080 * w / h)
        crop = f"crop={cw}:1080:{(1920 - cw) // 2}:0"
    vf = (f"{crop},scale={w}:{h}:flags=lanczos,setsar=1,setpts={f:.4f}*(PTS-STARTPTS),fps={FPS},"
          f"tpad=stop_mode=clone:stop_duration=30" + (f",{GRADE}" if grade else ""))
    cut = ["-t", f"{FREEZE[clip] - s:.3f}"] if clip in FREEZE else []
    p = subprocess.Popen([FF, "-loglevel", "error", "-ss", str(s), *cut, "-i", str(C / f"{clip}.mp4"), "-vf", vf,
                          "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    last = None
    for _ in range(n):
        buf = p.stdout.read(w * h * 3)
        if len(buf) == w * h * 3:
            last = Image.frombytes("RGB", (w, h), buf)
        yield last
    p.stdout.close()
    p.wait()


def track(pieces, total):
    """pieces: (clip, src, t0, t1, f, py); a piece runs XF past t1 (except the last) and the next fades in over it"""
    open_ = {}
    n = round(total * FPS)
    for k in range(n):
        t = k / FPS
        act = [i for i, p in enumerate(pieces) if p[2] <= t < p[3] + (XF if i < len(pieces) - 1 else 1)]
        frames = []
        for i in act:
            if i not in open_:
                clip, src, t0, t1, f, py = pieces[i]
                open_[i] = reader(clip, src, round((t1 - t0 + XF + 1) * FPS), f=f, py=py)
            frames.append((i, next(open_[i])))
        if len(frames) == 1:
            yield frames[0][1]
        else:
            (i0, a), (i1, b) = frames[0], frames[-1]
            alpha = min(max((t - pieces[i1][2]) / XF, 0), 1)
            yield Image.blend(a, b, alpha)


out = subprocess.Popen([FF, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
                        "-i", "-", "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p",
                        str(D / "clips" / "base.mp4")], stdin=subprocess.PIPE)
t = 0.0
audio = []    # (clip, src, timeline at, dur, gain dB, f)
marks, keep = {}, []


def emit(img, sample=False):
    global t
    out.stdin.write(img.tobytes())
    if sample:
        keep.append(img.resize((W // 2, H // 2)))
    t += 1 / FPS


def full(clip, s, e, name):
    marks[name] = round(t, 3)
    audio.append((clip, s, t, e - s, 0, 1.0))
    last = None
    for fr in reader(clip, s, round((e - s) * FPS), size=(W, H), py=0, grade=False):
        emit(fr)
        last = fr
    return last


def seg(name, top, bot, total, top_db, bot_db, sample=True):
    """top/bot: piece lists for the two panels; gains: list per piece"""
    marks[name] = round(t, 3)
    for pieces, dbs in ((top, top_db), (bot, bot_db)):
        for p, db in zip(pieces, dbs):
            if db is not None:
                end = p[3] + (XF if p is not pieces[-1] else 0)
                audio.append((p[0], p[1], t + p[2], (end - p[2]) / p[4], db, p[4]))
    k = 0
    for a, b in zip(track(top, total), track(bot, total)):
        page = BG.copy()
        page.paste(a, P1, MASK)
        page.paste(b, P2, MASK)
        emit(page, sample and k % 10 == 0)
        k += 1
    return page


# ---- hook ----
full("H_action", 1.5, 5.0, "hook_action")
last = full("H_closeup", 1.5, 6.6, "hook_line")

# ---- the page appears ----
marks["push"] = round(t, 3)
first_top = next(reader("TA", 0.0, 1))
n = round(0.9 * FPS)
for k in range(n):
    p = 1 - (1 - (k + 1) / n) ** 3
    w, h = W + (PW - W) * p, H + (PH - H) * p
    ch = last.width * h / w
    cy = min(max((last.height - ch) * 0.4, 0), last.height - ch)
    box = last.crop((0, int(cy), last.width, int(cy + ch))).resize((int(w), int(h)))
    page = BG.copy()
    page.paste(box, (int(p * P2[0]), int(p * P2[1])), rounded(int(w), int(h), int(R * p) + 1))
    page.paste(first_top, (P1[0], int(-PH + (P1[1] + PH) * p)), MASK)
    emit(page)
audio.append(("H_closeup", 6.6, marks["push"], 0.9, -8, 1.0))

# ---- 1 three snips over one continuous top shot; each "...hoon" lands on a snip (TA snips at 2.26, 7.28, 12.0) ----
seg("snips",
    [("TA", 0.0, 0, 15.0, 1.0, None)],
    [("B1", 2.04, 0, 4.9, 1.0, None), ("B2", 1.32, 4.9, 8.3, 1.0, None), ("B3", 1.98, 8.3, 15.0, 1.0, None)],
    15.0, [-5], [0, 0, 0])
# ---- 2 praise, then he leaves for good / aunty, then the present ----
seg("praise",
    [("TB", 0.0, 0, 8.3, 1.0, None)],   # TB grows a second perch after 8.5 s, so it ends as he heads out
    [("B4", 2.6, 0, 5.2, 1.0, None), ("H2", 0.0, 5.2, 8.3, 1.0, None)],
    8.3, [-16], [0, -8])
# ---- 3 the cat: he can't fly, falls into the far corner, the cat leaps (frozen mid-air) / he cries ----
seg("cat",
    [("TC", 0.0, 0, 15.0, 1.0, None)],
    [("H2", 3.1, 0, 10.0, 1.5, None), ("H_closeup", 4.2, 10.0, 15.0, 1.3, 250)],
    15.0, [-6], [-10, -14])
# TC is 15.04 s long: freeze its mid-air frame for the last second
# (done by the reader's held last frame; the clip itself ends mid-leap)

# ---- rewind ----
marks["rewind"] = round(t, 3)
icon_font = ImageFont.truetype("C:/Windows/Fonts/seguisym.ttf", 70)
frames = keep[::-1]
n = round(2.5 * FPS)
for k in range(n):
    fr = frames[min(int(k * len(frames) / n), len(frames) - 1)].resize((W, H))
    a = np.asarray(fr).astype(np.float32)
    a = 0.55 * a + 0.45 * a.mean(axis=2, keepdims=True) + 8
    a[::4] *= 0.93
    fr = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    dr = ImageDraw.Draw(fr)
    if (k // 8) % 2 == 0:
        dr.rounded_rectangle([70, 160, 250, 260], 22, fill=(61, 52, 44))
        dr.text((95, 165), "\u23EA", font=icon_font, fill=(251, 246, 238))
    emit(fr)

# ---- 4 the other ending ----
seg("escape", [("TD", 0.0, 0, 8.0, 1.0, None)], [("E1", 0.0, 0, 8.0, 1.0, None)], 8.0, [-8], [0], sample=False)
seg("home", [("TE", 0.0, 0, 10.0, 1.0, None)], [("E2", 1.0, 0, 10.0, 1.43, None)], 10.0, [-12], [-12], sample=False)
out.stdin.close()
out.wait()
total = round(t, 2)

# ---------- audio ----------
args, chains = [], []
for i, (clip, s, at, dur, db, f) in enumerate(audio):
    args += ["-ss", str(s), "-t", f"{dur:.3f}", "-i", str(C / f"{clip}.mp4")]
    tempo, tch = 1 / f, []
    while tempo < 0.5:
        tch.append("atempo=0.5"); tempo /= 0.5
    tch.append(f"atempo={tempo:.4f}")
    ms = int(at * 1000)
    chains.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,{','.join(tch)},volume={db}dB,"
                  f"afade=t=in:d=0.08,adelay={ms}|{ms}[a{i}]")
k = len(audio)
args += ["-f", "lavfi", "-t", "2.5", "-i", "anoisesrc=c=pink:a=0.08"]
ms = int(marks["rewind"] * 1000)
chains.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,highpass=f=900,afade=t=in:d=0.3,afade=t=out:st=2.1:d=0.4,"
              f"adelay={ms}|{ms}[a{k}]")
chains.append(f"{''.join(f'[a{i}]' for i in range(k + 1))}amix=inputs={k + 1}:normalize=0,apad,atrim=duration={total}[a]")
subprocess.run([FF, "-y", "-loglevel", "error", *args, "-filter_complex", ";".join(chains), "-map", "[a]", "-c:a", "pcm_s16le",
                str(D / "clips" / "base_sfx.wav")], check=True)
marks["end"] = total

# ---------- reel.json: subtitles placed from the measured word timings ----------
hl, sn, pr, es = marks["hook_line"], marks["snips"], marks["praise"], marks["escape"]
r2 = lambda x: round(x, 2)
cfg = {
    "music": "../_music/warm.mp3", "music_db": -24,
    "dialogue": [["clips/base_sfx.wav", 0.0]],
    "shots": [["base", 0.0, total, "fit"]],
    "headlines": [[0.2, r2(marks["hook_line"] - 0.1), "28 years old.<br><em>Can’t make one roti.</em>"],
                  [r2(marks["home"] + 6.0), total, "Raise a kid who says<br><em>“Let me try.”</em>"]],
    "subtitles": [
        [r2(hl + 1.82 - 1.5), r2(hl + 2.96 - 1.5), "“Phir se…”<br><small>Again…</small>", 1480],
        [r2(hl + 2.96 - 1.5), r2(hl + 5.4 - 1.5), "“har baar mere saath hi kyun?”<br><small>why does this always happen to me?</small>", 1480],
        [r2(sn + 2.44 - 2.04), r2(sn + 4.6 - 2.04), "“Laa, main khila deti hoon.”<br><small>Here, let me feed you.</small>", 1560],
        [r2(sn + 4.9 + 1.9 - 1.32), r2(sn + 4.9 + 4.2 - 1.32), "“Laa, main baandh deta hoon.”<br><small>Here, let me tie them.</small>", 1560],
        [r2(sn + 8.3 + 2.48 - 1.98), r2(sn + 8.3 + 5.9 - 1.98), "“Tu rehne de, padhai kar.<br>Main bana deti hoon.”<br><small>Leave it, go study. I’ll make it.</small>", 1510],
        [r2(pr + 3.02 - 2.6), r2(pr + 7.15 - 2.6), "“Kitna sanskari bachcha hai…<br>aapki har baat maanta hai.”<br><small>Such a well-behaved child. Always listens to you.</small>", 1490],
        [r2(es + 0.15), r2(es + 1.4), "“Let me try!”", 1600],
    ],
    "cta": {"keyword": "ALONE", "line": "for 5 things a 2-year-old can do alone"},
    "end_seconds": 3,
}
(D / "reel.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf8")
(D / "segments.json").write_text(json.dumps(marks, indent=1))
print(json.dumps(marks))
