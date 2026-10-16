"""Build pre-written carousel posts (channels with a posts/ folder, e.g. parenting).

  python run_posts.py --channel parenting --post p001     # render slides + caption into output/<channel>/<post>/

Each post is posts/<id>.yaml; slide N's image is images/<id>/<id>_s<N>.png (or .jpg/.webp).
Publishing on a schedule comes later; this renders for review.
"""
import argparse
import base64
import html
import re
from pathlib import Path

import yaml

from engine import render

ROOT = Path(__file__).parent
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")


def highlight(text):
    """'Pour their own *water*' -> HTML with the starred words highlighted."""
    safe = re.sub(r"\S+-\S+", r'<span class="nw">\g<0></span>', html.escape(text))  # keep "2-year-old" on one line
    return re.sub(r"\*(.+?)\*", r'<span class="hl">\1</span>', safe)


def slide_image(images_dir, post_id, n):
    for ext in IMAGE_EXTENSIONS:
        path = images_dir / post_id / f"{post_id}_s{n}{ext}"
        if path.exists():
            data = path.read_bytes()
            mime = "image/png" if data[:4] == b"\x89PNG" else "image/webp" if data[8:12] == b"WEBP" else "image/jpeg"
            return f"data:{mime};base64," + base64.b64encode(data).decode()
    print(f"  ! no image for slide {n}; using a plain background")
    return None


def build_slides(post, images_dir):
    slides, tip = [], 0
    for n, s in enumerate(post["slides"], 1):
        kind = s.get("kind", "tip")
        tip += kind == "tip"
        slides.append({"template": "slide.html", "kind": kind, "topic": post.get("topic", ""),
                       "number": tip if kind == "tip" else None,
                       "title_html": highlight(s["title"]), "body": s.get("body"),
                       "image": slide_image(images_dir, post["id"], n)})
    return slides


def build_caption(post, cfg):
    tags = " ".join(post.get("hashtags", "").split()[: cfg.get("hashtags_max", 12)])
    return "\n\n".join(p for p in [post["caption"].strip(), tags] if p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--post", required=True, help="post id, e.g. p001")
    args = ap.parse_args()

    channel_dir = ROOT / "channels" / args.channel
    cfg = yaml.safe_load((channel_dir / "config.yaml").read_text(encoding="utf-8"))
    post = yaml.safe_load((channel_dir / "posts" / f"{args.post}.yaml").read_text(encoding="utf-8"))
    out_dir = ROOT / "output" / args.channel / args.post

    slides = build_slides(post, channel_dir / "images")
    print(f"Rendering {len(slides)} slides…")
    base_context = {"brand": cfg["brand"], "name": cfg["name"], "handle": cfg["handle"]}
    render.render_slides(slides, channel_dir / "templates", out_dir, base_context)
    (out_dir / "caption.txt").write_text(build_caption(post, cfg), encoding="utf-8")
    print(f"Done: {out_dir}")


if __name__ == "__main__":
    main()
