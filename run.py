"""Build and publish one post for one channel.

  python run.py --channel ai-news --mode daily --stage draft     # 14:00: pick stories, open heads-up issue
  python run.py --channel ai-news --mode daily                   # 17:50: build (from today's draft) + post at 18:30
  python run.py --channel ai-news --mode daily --dry-run         # build everything, don't post
  python run.py --channel ai-news --mode weekly
"""
import argparse
import base64
import html
import json
import re
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

from engine import broll, collect, curate, history, host, images, instagram, notify, reel, render, video

ROOT = Path(__file__).parent
CAPTION_LIMIT = 2200


def keyword_line(result, mode, local_date):
    """Searchable first caption line, e.g. 'AI News Today (17 Oct): OpenAI, NVIDIA, Google & more'."""
    names = []
    for s in result["stories"]:
        c = (s.get("company") or "").strip()
        if c and c.lower() not in (n.lower() for n in names):
            names.append(c)
    day = datetime.strptime(local_date, "%Y-%m-%d").strftime("%d %b").lstrip("0")
    title = "This Week in Tech" if mode == "weekly" else "AI News Today"
    who = ", ".join(names[:3]) + (" & more" if len(names) > 3 or len(result["stories"]) > len(names[:3]) else "")
    return f"{title} ({day}): {who}" if names else f"{title} ({day})"


def hashtags(cfg):
    return " ".join(cfg["hashtags"].split()[: cfg.get("hashtags_max", 5)])


def build_caption(result, cfg, mode="daily", local_date=None):
    local_date = local_date or datetime.now().strftime("%Y-%m-%d")
    sources = sorted({s["source"] for s in result["stories"]})
    credits = sorted({c for c in result.get("video_credits", [])})
    disclosure = "Summaries written with AI assistance from the sources above. AI-generated illustrations are not real photos"
    disclosure += ("; video clips are stock or official footage used for illustration." if credits else ".")
    parts = [
        keyword_line(result, mode, local_date),
        result["caption"],
        "Sources: " + ", ".join(sources),
        ("Video: " + "; ".join(credits)) if credits else "",
        disclosure,
        hashtags(cfg),
    ]
    parts = [p for p in parts if p]
    caption = "\n\n".join(parts)
    if len(caption) > CAPTION_LIMIT:  # trim the body, keep the first line, credits + hashtags
        tail = "\n\n".join(parts[2:])
        body = result["caption"][: CAPTION_LIMIT - len(tail) - len(parts[0]) - 6].rstrip() + "…"
        caption = "\n\n".join([parts[0], body, tail])
    return caption


def build_reel_caption(result, cfg, mode, local_date):
    hook = (result.get("caption") or "").split("\n")[0].strip()
    return "\n\n".join(p for p in [
        keyword_line(result, mode, local_date),
        hook,
        f"All {len(result['stories'])} stories with the details are in our latest carousel 👉 {cfg['handle']}",
        hashtags(cfg),
    ] if p)


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


def slide_backgrounds(out_dir, n_slides):
    """Background per slide for video clips: a moving background (bg/NN_motion.mp4: press clip,
    stock footage or AI motion) if present, else the still. Slide 1 = cover (bg 00),
    slides 2..n-1 = stories (bg 01..), last slide = outro (reuses the cover)."""
    def bg(i):
        motion = out_dir / "bg" / f"{i:02d}_motion.mp4"
        return motion if motion.exists() else find_bg(out_dir, i)
    return [bg(0)] + [bg(i) for i in range(1, n_slides - 1)] + [bg(0)]


def slide_media(out_dir):
    """The files to publish, in order: NN.mp4 where a clip was made, otherwise NN.jpg."""
    stills = sorted(out_dir.glob("[0-9][0-9].jpg"))
    return [s.with_suffix(".mp4") if s.with_suffix(".mp4").exists() else s for s in stills]


def make_images(result, out_dir, cfg):
    bg_dir = out_dir / "bg"
    bg_dir.mkdir(parents=True, exist_ok=True)
    img_cfg = cfg.get("images", {})
    prompts = image_prompts(result)
    for i, prompt in enumerate(prompts):
        path = find_bg(out_dir, i)
        if path.exists():
            continue  # already have one (hand-made, or a frame from a press/stock clip)
        print(f"  image {i + 1}/{len(prompts)}")
        models = img_cfg.get("cover_models" if i == 0 else "story_models", [])
        try:
            images.generate(prompt, path, models)
        except images.QuotaExhausted as e:
            print(f"  ! {e}; remaining slides use a plain background")
            break


def gather_video(result, out_dir, cfg, draft_dir=None):
    """Moving backgrounds for story slides: your press clips first (draft_dir/press-N.mp4), then free
    stock footage (NASA for space stories, Pexels, Pixabay). NASA and press credits go into result["video_credits"]."""
    vcfg = cfg.get("video", {})
    bg_dir = out_dir / "bg"
    bg_dir.mkdir(parents=True, exist_ok=True)
    credits, used = [], set()
    for i, story in enumerate(result["stories"], 1):
        motion, still = bg_dir / f"{i:02d}_motion.mp4", bg_dir / f"{i:02d}.jpg"
        press = draft_dir / f"press-{i}.mp4" if draft_dir else None
        if press and press.exists():
            shutil.copy(press, motion)
            broll._still_frame(motion, still)
            credits.append(f"{story.get('company') or story['source']} (official)")
            print(f"  story {i}: your press clip")
            continue
        if motion.exists() or not vcfg.get("broll") or not story.get("broll_query"):
            continue
        found = broll.fetch(story["broll_query"], story.get("space"), vcfg.get("broll_providers", []),
                            motion, still, vcfg.get("seconds", 6), used)
        if found:
            if found["provider"] == "NASA":  # stock libraries (Pixabay, Pexels) need no credit
                credits.append("NASA")
            print(f"  story {i}: {found['provider']} clip for '{story['broll_query']}'")
        else:
            print(f"  story {i}: no stock clip for '{story['broll_query']}', animated AI image instead")
    result["video_credits"] = credits


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


def render_all(result, mode, out_dir, cfg, channel_dir, base_context, video_numbers=None):
    """Still slides for every slide, plus MP4 clips for the cover and story slides when video is on."""
    slides = build_slides(result, mode, out_dir, cfg)
    print(f"Rendering {len(slides)} slides…")
    render.render_slides(slides, channel_dir / "templates", out_dir, base_context)
    vcfg = cfg.get("video", {})
    if video_numbers is None and vcfg.get("enabled"):
        video_numbers = list(range(1, len(slides)))  # cover + stories; the outro stays a photo
    if video_numbers:
        print(f"Making {len(video_numbers)} video slides…")
        video.make_clips(slides, video_numbers, slide_backgrounds(out_dir, len(slides)),
                         channel_dir / "templates", base_context, out_dir, vcfg.get("seconds", 6))
    return slide_media(out_dir)


def make_reel_and_story(out_dir, result, cfg, channel_dir):
    """Daily Reel (9:16 video from the slides) and a 'NEW POST' Story image. Failures here never block the post."""
    try:
        if cfg.get("reel", {}).get("enabled"):
            rc = cfg["reel"]
            music = reel.pick_music(channel_dir / "music")
            print(f"Building Reel ({'music: ' + music.name if music else 'no music: add tracks to channels/<channel>/music/'})…")
            reel.build_reel(out_dir, music, rc.get("cover_seconds", 3), rc.get("story_seconds", 4))
        if cfg.get("story", {}).get("enabled"):
            print("Building Story image…")
            reel.build_story(out_dir, cfg["handle"], len(result["stories"]))
    except Exception as e:
        print(f"  ! Reel/Story build failed: {e}")


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
    out_dir = Path(paths[0]).parent
    extras = [f for f in (out_dir / "reel.mp4", out_dir / "story.jpg") if f.exists()]
    print(f"Uploading {len(paths)} slides ({sum(p.suffix == '.mp4' for p in paths)} video)"
          f"{' + ' + ', '.join(f.name for f in extras) if extras else ''}…")
    urls = host.upload(list(paths) + extras, f"{args.channel}/{stamp}")
    by_name = dict(zip([p.name for p in list(paths) + extras], urls))
    version = cfg["instagram"]["api_version"]
    print("Publishing carousel to Instagram…")
    media_id = instagram.publish_carousel(urls[: len(paths)], caption, version)
    print(f"Published! media id {media_id}")

    history.record(hist, args.mode, local_date, result["stories"], media_id)
    history.save(channel_dir, hist)

    problems = []
    if "reel.mp4" in by_name and cfg.get("reel", {}).get("enabled"):
        try:
            rc_path = out_dir / "reel_caption.txt"
            reel_caption = rc_path.read_text(encoding="utf-8") if rc_path.exists() else caption
            reel_id = instagram.publish_reel(by_name["reel.mp4"], reel_caption, version,
                                             cfg["reel"].get("share_to_feed", False))
            print(f"Reel published: {reel_id}")
        except Exception as e:
            problems.append(f"Reel: {e}")
    if "story.jpg" in by_name and cfg.get("story", {}).get("enabled"):
        try:
            print(f"Story published: {instagram.publish_story(by_name['story.jpg'], version)}")
        except Exception as e:
            problems.append(f"Story: {e}")
    try:
        host.cleanup(args.channel)
    except Exception as e:  # cleanup is best-effort
        print(f"  (media cleanup skipped: {e})")
    if problems:  # carousel is live and recorded; fail the run so GitHub emails you
        print("  ! " + "\n  ! ".join(problems))
        raise SystemExit(f"Carousel posted, but: {'; '.join(problems)}")
    return media_id


# ---------- draft stage (heads-up issue for optional press clips) ----------

def _newsroom(company, cfg):
    for name, url in (cfg.get("newsrooms") or {}).items():
        if company and name.lower() in company.lower():
            return name, url
    return None


def draft_issue_body(result, cfg, args, local_date):
    folder = f"channels/{args.channel}/drafts/{local_date}"
    lines = [f"@{cfg.get('github_user', '')} today's stories are picked. The post goes live at "
             f"**{cfg['daily'].get('publish_time', '18:30')} IST** (it's built from ~40 minutes before).", "",
             "**Optional:** to use an official clip as a story's video, upload a short MP4 (under 25 MB; "
             "the first ~6 seconds are used) named `press-N.mp4` (N = story number) into "
             f"[`{folder}`](../tree/main/{folder}) via **Add file → Upload files**, before ~17:45 IST. "
             "Only use clips the company offers for media/editorial use. Do nothing and the post goes "
             "ahead automatically with stock footage / AI images.", ""]
    for i, s in enumerate(result["stories"], 1):
        room = _newsroom(s.get("company"), cfg)
        media = f" · official media: [{room[0]} newsroom]({room[1]})" if room else ""
        lines.append(f"{i}. **{s['headline'].replace('*', '')}** · [{s['source']} article]({s['url']}){media}")
    return "\n".join(lines)


def run_draft(result, cfg, args, channel_dir, local_date):
    draft_dir = channel_dir / "drafts" / local_date
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / "post.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    (draft_dir / ".keep").write_text("", encoding="utf-8")
    for old in (channel_dir / "drafts").iterdir():  # keep a week of drafts
        if old.is_dir() and old.name < (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"):
            shutil.rmtree(old)
    print(f"Draft saved to {draft_dir}")
    for i, s in enumerate(result["stories"], 1):
        print(f"  {i}. {s['headline']}  ({s['source']}){'  [official media page]' if _newsroom(s.get('company'), cfg) else ''}")
    if not args.dry_run:
        number = notify.open_issue(f"Tech Monkey {local_date}: today's stories (optional press clips)",
                                   draft_issue_body(result, cfg, args, local_date))
        (draft_dir / "issue.json").write_text(json.dumps({"number": number}), encoding="utf-8")


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--mode", choices=["daily", "weekly"], default="daily")
    ap.add_argument("--stage", choices=["post", "draft"], default="post",
                    help="draft: pick stories + open the heads-up issue; post: build and publish")
    ap.add_argument("--dry-run", action="store_true", help="make images + caption, don't post")
    ap.add_argument("--force", action="store_true", help="post even if already posted today")
    ap.add_argument("--rerender", metavar="OUTPUT_DIR", help="redraw a saved batch with the current templates (no fetching, no AI, no posting)")
    ap.add_argument("--video-slides", metavar="N,N", help="with --rerender: make MP4 clips for these slide numbers (e.g. 1,2,3,4)")
    ap.add_argument("--no-images", action="store_true", help="skip image generation (e.g. to add images by hand into OUTPUT_DIR/bg/)")
    ap.add_argument("--publish-dir", metavar="OUTPUT_DIR", help="publish an already-rendered, reviewed batch as-is")
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
        numbers = [int(n) for n in args.video_slides.split(",") if n.strip()] if args.video_slides else []
        render_all(result, args.mode, out_dir, cfg, channel_dir, base_context, numbers)
        print(f"Re-rendered {out_dir}")
        return

    if args.publish_dir:
        out_dir = Path(args.publish_dir)
        result = json.loads((out_dir / "post.json").read_text(encoding="utf-8"))
        caption = (out_dir / "caption.txt").read_text(encoding="utf-8")
        publish(slide_media(out_dir), caption, result, args, cfg, channel_dir, hist, local_date, out_dir.name)
        return

    if not args.dry_run and not args.force and history.already_posted(hist, args.mode, local_date):
        print(f"Already posted {args.mode} for {local_date}; nothing to do.")
        return

    print(f"[{args.channel}] {args.mode} {args.stage} for {local_date}")
    draft_dir = channel_dir / "drafts" / local_date if args.mode == "daily" else None
    if args.stage == "post" and draft_dir and (draft_dir / "post.json").exists():
        print(f"Using today's draft ({draft_dir})")
        result = json.loads((draft_dir / "post.json").read_text(encoding="utf-8"))
    else:
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

    if args.stage == "draft":
        run_draft(result, cfg, args, channel_dir, local_date)
        return

    stamp = now.strftime("%Y-%m-%d_%H%M") + f"_{args.mode}"
    out_dir = ROOT / "output" / args.channel / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "post.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    if cfg.get("video", {}).get("enabled"):
        print("Finding video clips…")
        gather_video(result, out_dir, cfg, draft_dir)
    if args.no_images:
        print("Skipping image generation (--no-images)")
    else:
        print("Generating background images…")
        make_images(result, out_dir, cfg)
    caption = build_caption(result, cfg, args.mode, local_date)
    (out_dir / "caption.txt").write_text(caption, encoding="utf-8")
    (out_dir / "reel_caption.txt").write_text(build_reel_caption(result, cfg, args.mode, local_date), encoding="utf-8")
    (out_dir / "post.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    paths = render_all(result, args.mode, out_dir, cfg, channel_dir, base_context)
    make_reel_and_story(out_dir, result, cfg, channel_dir)

    for i, s in enumerate(result["stories"], 1):
        print(f"  {i}. [{s['category']}] {s['headline']}  ({s['source']})")
    print(f"Slides + caption saved to {out_dir}")

    if args.dry_run:
        print("Dry run: not posting.")
        return

    wait_for_publish_time(cfg[args.mode].get("publish_time"), now.tzinfo)
    media_id = publish(paths, caption, result, args, cfg, channel_dir, hist, local_date, stamp)
    issue_file = draft_dir / "issue.json" if draft_dir else None
    if issue_file and issue_file.exists():
        notify.close_issue(json.loads(issue_file.read_text())["number"], f"Posted ✅ (media id {media_id}).")


if __name__ == "__main__":
    main()
