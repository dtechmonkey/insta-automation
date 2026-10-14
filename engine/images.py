"""Generate slide background images with Cloudflare Workers AI (free daily allowance: 10,000 neurons).

Budget: flux-1-schnell costs ~100 neurons per image; lucid-origin is far pricier (~1,000+),
so it is used for the cover only (config `images.cover_models` vs `images.story_models`).
A slide with no image falls back to the template's plain background, so a failed image
never blocks a post.
"""
import base64
import os
import time
from pathlib import Path

import requests

API = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
STYLE = ("vivid cinematic photorealistic digital collage, viral tech-news poster style, densely layered composition "
         "with a large foreground hero subject, detailed midground elements and glowing holographic charts and data "
         "overlays, bright dramatic lighting with electric blue and warm orange highlights, saturated colors, "
         "sharp focus, ultra detailed, epic and high-energy, main subject in the upper two-thirds of the frame, "
         "the bottom quarter darker and simple, no readable text, no captions, no watermark")


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
    """Hugging Face Space (e.g. Qwen-Image) via gradio_client; free ZeroGPU quota. Returns path or None."""
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        return None
    from gradio_client import Client  # imported lazily: only needed for hf: models
    try:
        client = Client(space, token=token, verbose=False)
        result, _seed = client.predict(prompt=prompt, aspect_ratio="3:4", api_name="/generate_image")
    except Exception as e:  # busy Space, quota used up, Space down: fall through to the next model
        print(f"  ! hf:{space}: {str(e)[:150]}")
        return None
    path = result if isinstance(result, str) else (result or {}).get("path")
    if not path:
        return None
    Path(out_path).write_bytes(Path(path).read_bytes())
    return Path(out_path)


def generate(prompt, out_path, models, width=1080, height=1350):
    """Save one image to out_path; returns the path or None. Raises QuotaExhausted when Cloudflare's daily allowance is gone.

    Model names starting with "hf:" are Hugging Face Spaces; the rest are Cloudflare Workers AI models.
    """
    full_prompt = f"{prompt}. {STYLE}"
    for model in [m for m in models if m.startswith("hf:")]:
        if path := _hf_space(model[3:], full_prompt, out_path):
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
