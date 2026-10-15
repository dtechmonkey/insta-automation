"""Build and publish one post for one channel.

  python run.py --channel ai-news --mode daily --dry-run
  python run.py --channel ai-news --mode weekly
"""
import argparse
import base64
import html
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

from engine import collect, curate, history, host, images, instagram, render

ROOT = Path(__file__).parent
CAPTION_LIMIT = 2200


def build_caption(result, cfg):
    sources = sorted({s["source"] for s in result["stories"]})
    parts = [
        result["caption"],
        "Sources: " + ", ".join(sources),
        "Summaries written with AI assistance from the sources above. All images are AI-generated illustrations, not real photos.",
        " ".join(cfg["hashtags"].split()[:30]),
    ]
    caption = "\n\n".join(p for p in parts if p)
    if len(caption) > CAPTION_LIMIT:  # trim the body, keep credits + hashtags
        tail = "\n\n".join(parts[1:])
        caption = result["caption"][: CAPTION_LIMIT - len(tail) - 3].rstrip() + "…\n\n" + tail
    return caption


def highlight(text):
    """'GLASSES *KILL* PHONES' -> HTML with the starred words in the accent gradient."""
    safe = html.escape(text)
    return re.sub(r"\*(.+?)\*", r'<span class="hl">\1</span>', safe)


def image_prompts(result):
    """One background prompt per slide: cover, each story, outro (reuses the cover image)."""
    return [result["cover_image_prompt"]] + [s["image_prompt"] for s in result["stories"]]


BG_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def find_bg(out_dir, i):
    """The background image for slide i (any common extension), or the default .jpg path if none exists."""
    for ext in BG_EXTENSIONS:
        path = out_dir / "bg" / f"{i:02d}{ext}"
        if path.exists():
            return path
    return out_dir / "bg" / f"{i:02d}.jpg"


def make_images(result, out_dir, cfg):
    bg_dir = out_dir / "bg"
    bg_dir.mkdir(parents=True, exist_ok=True)
    img_cfg = cfg.get("images", {})
    prompts = image_prompts(result)
    for i, prompt in enumerate(prompts):
        path = find_bg(out_dir, i)
        if path.exists():
            continue
        print(f"  image {i + 1}/{len(prompts)}")
        models = img_cfg.get("cover_models" if i == 0 else "story_models", [])
        try:
            images.generate(prompt, path, models)
        except images.QuotaExhausted as e:
            print(f"  ! {e}; remaining slides use a plain background")
            break


def _data_uri(path):
    if not path.exists():
        return None
    data = path.read_bytes()
    # Providers return JPEG, PNG or WebP; label the data URI with the real type.
    mime = "image/png" if data[:4] == b"\x89PNG" else "image/webp" if data[8:12] == b"WEBP" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def build_slides(result, mode, out_dir, cfg):
    stories = result["stories"]
    total = len(stories) + 2
    bg = [_data_uri(find_bg(out_dir, i)) for i in range(len(stories) + 1)]
    weekly = mode == "weekly"
    slides = [{"template": "hero.html", "cover": True, "image": bg[0],
               "kicker": "Weekly recap" if weekly else None,
               "headline_html": highlight("This week in *tech*" if weekly else result["cover_headline"]),
               "facts": result.get("cover_subline"), "cta": "Swipe for more"}]
    for i, story in enumerate(stories, 1):
        slides.append({"template": "hero.html", "image": bg[i], "pager": f"{i + 1}/{total}",
                       "headline_html": highlight(story["headline"]), "facts": story["facts"],
                       "source": story["source"],
                       "badge": "Story of the week" if weekly and story["url"] == result.get("biggest_url") else None})
    slides.append({"template": "hero.html", "image": bg[0], "pager": f"{total}/{total}",
                   "headline_html": highlight("We make you *smarter* about tech *every day*"),
                   "facts": f"Follow {cfg['handle']} for {'weekly recaps' if weekly else 'daily'} tech & AI news",
                   "cta": "Follow now"})
    return slides


def wait_for_publish_time(hhmm, tz, max_wait_minutes=60):
    """If the publish time is up to an hour away, sleep until it; otherwise (manual runs) post now."""
    if not hhmm:
        return
    now = datetime.now(tz)
    hour, minute = map(int, hhmm.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    wait = (target - now).total_seconds()
    if 0 < wait <= max_wait_minutes * 60:
        print(f"Ready early; waiting {wait / 60:.0f} min to publish at {hhmm}…")
        time.sleep(wait)


def publish(paths, caption, result, args, cfg, channel_dir, hist, local_date, stamp):
    print("Uploading images…")
    urls = host.upload(paths, f"{args.channel}/{stamp}")
    print("Publishing to Instagram…")
    media_id = instagram.publish_carousel(urls, caption, cfg["instagram"]["api_version"])
    print(f"Published! media id {media_id}")

    history.record(hist, args.mode, local_date, result["stories"], media_id)
    history.save(channel_dir, hist)
    try:
        host.cleanup(args.channel)
    except Exception as e:  # cleanup is best-effort
        print(f"  (media cleanup skipped: {e})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--mode", choices=["daily", "weekly"], default="daily")
    ap.add_argument("--dry-run", action="store_true", help="make images + caption, don't post")
    ap.add_argument("--force", action="store_true", help="post even if already posted today")
    ap.add_argument("--rerender", metavar="OUTPUT_DIR", help="redraw a saved batch with the current templates (no fetching, no AI, no posting)")
    ap.add_argument("--no-images", action="store_true", help="skip image generation (e.g. to add images by hand into OUTPUT_DIR/bg/)")
    ap.add_argument("--publish-dir", metavar="OUTPUT_DIR", help="publish an already-rendered, reviewed batch as-is (its NN.jpg slides + caption.txt)")
    args = ap.parse_args()

    load_dotenv(ROOT / ".env")
    channel_dir = ROOT / "channels" / args.channel
    cfg = yaml.safe_load((channel_dir / "config.yaml").read_text(encoding="utf-8"))
    now = datetime.now(ZoneInfo(cfg.get("timezone", "UTC")))
    local_date = now.strftime("%Y-%m-%d")
    hist = history.load(channel_dir)
    base_context = {"brand": cfg["brand"], "name": cfg["name"], "handle": cfg["handle"]}

    if args.rerender:  # reuses saved text + images; only generates images that are missing
        out_dir = Path(args.rerender)
        result = json.loads((out_dir / "post.json").read_text(encoding="utf-8"))
        if not args.no_images:
            make_images(result, out_dir, cfg)
        slides = build_slides(result, args.mode, out_dir, cfg)
        render.render_slides(slides, channel_dir / "templates", out_dir, base_context)
        print(f"Re-rendered {len(slides)} slides in {out_dir}")
        return

    if args.publish_dir:
        out_dir = Path(args.publish_dir)
        result = json.loads((out_dir / "post.json").read_text(encoding="utf-8"))
        caption = (out_dir / "caption.txt").read_text(encoding="utf-8")
        paths = sorted(out_dir.glob("[0-9][0-9].jpg"))
        print(f"Publishing saved batch {out_dir.name}: {len(paths)} slides")
        publish(paths, caption, result, args, cfg, channel_dir, hist, local_date, out_dir.name)
        return

    if not args.dry_run and not args.force and history.already_posted(hist, args.mode, local_date):
        print(f"Already posted {args.mode} for {local_date}; nothing to do.")
        return

    print(f"[{args.channel}] {args.mode} post for {local_date}")
    if args.mode == "daily":
        since = datetime.now(timezone.utc) - timedelta(hours=cfg["daily"]["lookback_hours"])
        seen_urls, seen_titles = history.seen(hist)
        print("Collecting news…")
        items = collect.collect(cfg["sources"], since, seen_urls, seen_titles)
        if len(items) < cfg["daily"]["min_stories"]:
            sys.exit(f"Only {len(items)} new stories found; skipping today.")
        print("Curating with AI…")
        recent = history.recently_posted(hist)
        curate_fn = lambda items, cfg: curate.curate_daily(items, cfg, recent)
    else:
        items = history.week_pool(hist)
        if len(items) < 3:
            sys.exit(f"Only {len(items)} stories in this week's history; skipping weekly recap.")
        print(f"Curating weekly recap from {len(items)} stories…")
        curate_fn = curate.curate_weekly
    try:
        result = curate_fn(items, cfg)
    except curate.CurationFailed as e:
        # Non-zero exit fails the GitHub run, which emails you: that's the "inform" step.
        sys.exit(f"SKIPPED {args.mode} post for {local_date}: {e}. Nothing was posted.")

    caption = build_caption(result, cfg)
    stamp = now.strftime("%Y-%m-%d_%H%M") + f"_{args.mode}"
    out_dir = ROOT / "output" / args.channel / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "caption.txt").write_text(caption, encoding="utf-8")
    (out_dir / "post.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    if args.no_images:
        print("Skipping image generation (--no-images)")
    else:
        print("Generating background images…")
        make_images(result, out_dir, cfg)
    slides = build_slides(result, args.mode, out_dir, cfg)
    print(f"Rendering {len(slides)} slides…")
    paths = render.render_slides(slides, channel_dir / "templates", out_dir, base_context)

    for i, s in enumerate(result["stories"], 1):
        print(f"  {i}. [{s['category']}] {s['headline']}  ({s['source']})")
    print(f"Images + caption saved to {out_dir}")

    if args.dry_run:
        print("Dry run: not posting.")
        return

    wait_for_publish_time(cfg[args.mode].get("publish_time"), now.tzinfo)
    publish(paths, caption, result, args, cfg, channel_dir, hist, local_date, stamp)


if __name__ == "__main__":
    main()
