"""Generate slide background images (models tried in order from config images.cover_models / story_models).

  "hf:<space>"  Hugging Face Spaces (Qwen-Image, FLUX.1-dev) on the free ZeroGPU quota (~7 images/day).
  "@cf/<model>" Cloudflare Workers AI (free 10,000 neurons/day; flux-1-schnell ~100 neurons per image).
A slide with no image falls back to the template's plain background, so a failed image never blocks a post.
"""
import base64
import os
import re
import subprocess
import time
from pathlib import Path

import requests

API = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
STYLE = ("vivid cinematic photorealistic digital collage, viral tech-news poster style, densely layered composition "
         "with a large foreground hero subject, detailed midground elements and glowing holographic charts and data "
         "overlays, bright dramatic lighting with electric blue and warm orange highlights, saturated colors, "
         "sharp focus, ultra detailed, epic and high-energy, main subject in the upper two-thirds of the frame, "
         "the bottom quarter darker and simple, full-bleed composition filling the entire frame edge to edge, "
         "no frame, no border, no margins, no readable text, no captions, no watermark")

# How to call each supported Hugging Face Space (each has its own API).
HF_SPACES = {
    "mcp-tools/Qwen-Image": {"api": "/generate_image", "args": {"aspect_ratio": "3:4"}},
    "black-forest-labs/FLUX.1-dev": {"api": "/infer", "args": {"width": 864, "height": 1080}},
}


class QuotaExhausted(RuntimeError):
    pass


def _decode(resp):
    if resp.headers.get("content-type", "").startswith("image/"):
        return resp.content
    image = (resp.json().get("result") or {}).get("image")
    return base64.b64decode(image) if image else None


def _body(model, prompt, width, height):
    if "schnell" in model:  # takes no size; returns 1024x1024, cropped to 4:5 by the template
        return {"prompt": prompt, "steps": 8}
    return {"prompt": prompt, "width": width, "height": height}


def _hf_space(space, prompt, out_path):
    """Hugging Face Space via gradio_client on the free ZeroGPU quota. Returns path or None."""
    token = os.environ.get("HF_TOKEN", "").strip()
    spec = HF_SPACES.get(space)
    if not token or not spec:
        return None
    from gradio_client import Client  # imported lazily: only needed for hf: models
    try:
        client = Client(space, token=token, verbose=False)
        out = client.predict(prompt=prompt, api_name=spec["api"], **spec["args"])
    except Exception as e:  # busy Space, quota used up, Space down: fall through to the next model
        print(f"  ! hf:{space}: {str(e)[:150]}")
        return None
    result = out[0] if isinstance(out, (list, tuple)) else out
    path = result if isinstance(result, str) else (result or {}).get("path")
    if not path:
        return None
    Path(out_path).write_bytes(Path(path).read_bytes())
    return Path(out_path)


def trim_borders(path, min_keep=0.60, min_trim=0.06):
    """Crop solid dark bars that some models draw around the picture (a 'framed poster').
    Uses ffmpeg cropdetect; only trims when the bars are solid and most of the picture remains."""
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    out = subprocess.run([ff, "-i", str(path), "-vf", "cropdetect=limit=0.08:round=2:reset=0:skip=0", "-f", "null", "-"],
                         capture_output=True, text=True, timeout=60).stderr
    crops = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", out)
    size = re.search(r"Stream.*?, (\d{2,5})x(\d{2,5})", out)
    if not crops or not size:
        return False
    w, h, x, y = map(int, crops[-1])
    W, H = int(size[1]), int(size[2])
    if w < W * min_keep or h < H * min_keep or w * h > W * H * (1 - min_trim):
        return False  # nothing worth trimming, or it would cut too much (likely a genuinely dark scene)
    tmp = Path(path).with_suffix(".trim.jpg")
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(path), "-vf", f"crop={w}:{h}:{x}:{y}",
                    "-q:v", "2", str(tmp)], check=True, timeout=60)
    tmp.replace(path)  # saved as JPEG under the original name; the renderer detects the real type
    print(f"  trimmed border: {W}x{H} -> {w}x{h}")
    return True


def generate(prompt, out_path, models, width=1080, height=1350):
    """Save one image to out_path; returns the path or None. Raises QuotaExhausted when Cloudflare's daily allowance is gone.

    Model names starting with "hf:" are Hugging Face Spaces; the rest are Cloudflare Workers AI models.
    """
    full_prompt = f"{prompt}. {STYLE}"
    for model in [m for m in models if m.startswith("hf:")]:
        if path := _hf_space(model[3:], full_prompt, out_path):
            trim_borders(path)
            return path
    models = [m for m in models if not m.startswith("hf:")]
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not (account and token):
        print("  (no Cloudflare keys: slides use a plain background)")
        return None
    for model in models:
        for attempt in range(2):
            try:
                resp = requests.post(API.format(account=account, model=model),
                                     headers={"Authorization": f"Bearer {token}"},
                                     json=_body(model, full_prompt, width, height), timeout=120)
            except requests.RequestException as e:
                print(f"  ! {model}: {type(e).__name__}")
                time.sleep(5)
                continue
            if resp.ok and (image := _decode(resp)):
                Path(out_path).write_bytes(image)
                return Path(out_path)
            if "daily free allocation" in resp.text:
                raise QuotaExhausted("Cloudflare daily free image allowance used up (resets 00:00 UTC)")
            print(f"  ! {model}: HTTP {resp.status_code} {resp.text[:150]}")
            if resp.status_code not in (429, 500, 502, 503, 504):
                break  # bad request / no access: try the next model
            time.sleep(10)
    return None
