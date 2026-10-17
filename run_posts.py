"""Pre-written carousel posts from a queue (channels with a posts/ folder, e.g. parenting).

  python run_posts.py --channel parenting --render p001      # slides + caption + printable into output/ for review
  python run_posts.py --channel parenting --status           # which posts are ready / missing images
  python run_posts.py --channel parenting --publish          # post the next ready post (GitHub Actions, twice a day)
  python run_posts.py --channel parenting --publish --dry-run
  python run_posts.py --channel parenting --comments         # reply to comments containing a post's keyword

Each post is posts/<id>.yaml; slide N's image is images/<id>/<id>_s<N>.jpg (or .png/.webp).
Posts go out in id order, skipping any whose images aren't all there yet.
Publishing also writes the post's printable to docs/printables/ (GitHub Pages, linked from the bio).
"""
import argparse
import base64
import html
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, select_autoescape

from engine import host, instagram, notify, render

ROOT = Path(__file__).parent
DOCS = ROOT / "docs" / "printables"
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
REPLIES = [
    "Here you go 🌿 Tap the link in our bio → Printables → {kw}",
    "Yay! 🌿 Your printable is waiting: link in our bio → {kw}",
    "Sending it your way 💛 Find {kw} under Printables (link in bio)",
    "Done! 🌿 It's in our bio link → Printables → {kw}. Happy trying!",
]
MAX_REPLIES_PER_RUN = 40


# ---------- posts and state ----------

def load_post(channel_dir, post_id):
    return yaml.safe_load((channel_dir / "posts" / f"{post_id}.yaml").read_text(encoding="utf-8"))


def all_post_ids(channel_dir):
    return sorted(p.stem for p in (channel_dir / "posts").glob("p[0-9]*.yaml"))


def find_image(channel_dir, post_id, n):
    for ext in IMAGE_EXTENSIONS:
        path = channel_dir / "images" / post_id / f"{post_id}_s{n}{ext}"
        if path.exists():
            return path
    return None


def missing_images(channel_dir, post):
    return [n for n in range(1, len(post["slides"]) + 1) if not find_image(channel_dir, post["id"], n)]


def load_state(channel_dir, name, default):
    path = channel_dir / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_state(channel_dir, name, data):
    (channel_dir / name).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def ready_queue(channel_dir, hist, local_date):
    """Unposted posts with all their images, in id order. A post with `not_before: YYYY-MM-DD` (festival
    greetings) waits until that day, then goes first."""
    posted = {p["id"] for p in hist["posts"]}
    ready, dated = [], []
    for pid in all_post_ids(channel_dir):
        post = load_post(channel_dir, pid)
        if pid in posted or missing_images(channel_dir, post):
            continue
        not_before = str(post.get("not_before", ""))
        if not_before > local_date:
            continue
        (dated if not_before else ready).append(pid)
    return dated + ready


# ---------- rendering ----------

def data_uri(path):
    if not path:
        return None
    data = path.read_bytes()
    mime = "image/png" if data[:4] == b"\x89PNG" else "image/webp" if data[8:12] == b"WEBP" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def highlight(text):
    """'Pour their own *water*' -> HTML with the starred words highlighted."""
    safe = re.sub(r"\S+-\S+", r'<span class="nw">\g<0></span>', html.escape(text))  # keep "2-year-old" on one line
    return re.sub(r"\*(.+?)\*", r'<span class="hl">\1</span>', safe)


def build_slides(post, channel_dir, cfg):
    slides, tip = [], 0
    for n, s in enumerate(post["slides"], 1):
        kind = s.get("kind", "tip")
        tip += kind == "tip"
        image = find_image(channel_dir, post["id"], n)
        if not image:
            print(f"  ! no image for slide {n}; using a plain background")
        slides.append({"template": "slide.html", "kind": kind, "topic": post.get("topic", ""),
                       "number": tip if kind == "tip" else None,
                       "title_html": highlight(s["title"]), "body": s.get("body"),
                       "keyword": post.get("keyword"), "cta_mode": cfg.get("cta_mode", "bio"),
                       "image": data_uri(image)})
    return slides


def build_caption(post, cfg):
    kw = post.get("keyword")
    if kw and post.get("printable") and cfg.get("cta_mode") == "comment":
        cta = f"💬 Comment {kw} and we'll reply with your free printable.\n📎 All our printables: link in bio"
    elif post.get("printable"):
        cta = "📎 Free printable for this post: link in bio"
    else:
        cta = ""
    tags = " ".join(post.get("hashtags", "").split()[: cfg.get("hashtags_max", 12)])
    return "\n\n".join(p for p in [post["caption"].strip(), cta, tags] if p)


def base_context(cfg):
    return {"brand": cfg["brand"], "name": cfg["name"], "handle": cfg["handle"]}


def render_post(post, channel_dir, cfg, out_dir):
    slides = build_slides(post, channel_dir, cfg)
    print(f"Rendering {len(slides)} slides…")
    paths = render.render_slides(slides, channel_dir / "templates", out_dir, base_context(cfg))
    (out_dir / "caption.txt").write_text(build_caption(post, cfg), encoding="utf-8")
    return paths


def render_printable(post, channel_dir, cfg, pdf_path):
    """The post's free printable as an A4 PDF (banner = the cover image)."""
    env = Environment(loader=FileSystemLoader(str(channel_dir / "templates")), autoescape=select_autoescape(["html"]))
    p = post["printable"]
    page_html = env.get_template("printable.html").render(
        **base_context(cfg), p=p, medical=p.get("medical", False),
        image=data_uri(find_image(channel_dir, post["id"], 1)))
    from playwright.sync_api import sync_playwright
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.set_content(page_html, wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        page.pdf(path=str(pdf_path), format="A4", print_background=True)
        browser.close()
    return pdf_path


def write_printables_index(channel_dir, cfg, hist):
    """docs/printables/index.html: every published post's printable, newest first."""
    rows = []
    for entry in reversed(hist["posts"]):
        kw = entry.get("keyword")
        if not kw or not (DOCS / f"{kw.lower()}.pdf").exists():
            continue
        p = load_post(channel_dir, entry["id"]).get("printable", {})
        rows.append(f'<a class="card" href="{kw.lower()}.pdf"><span class="kw">{html.escape(kw)}</span>'
                    f'<span class="t">{html.escape(p.get("title", ""))}</span>'
                    f'<span class="s">{html.escape(p.get("subtitle", ""))}</span></a>')
    b = cfg["brand"]
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(cfg['name'])} · Free printables</title>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:wght@600&family=Nunito:wght@600;800&display=swap" rel="stylesheet">
<style>
body {{ margin: 0; background: {b['cream']}; color: {b['ink']}; font-family: Nunito, sans-serif; }}
main {{ max-width: 640px; margin: 0 auto; padding: 32px 16px 48px; }}
h1 {{ font-family: Fraunces, serif; font-size: 34px; margin: 0 0 6px; }}
p.lead {{ color: {b['ink_soft']}; margin: 0 0 24px; }}
input {{ width: 100%; box-sizing: border-box; padding: 14px 16px; border-radius: 14px; border: 1.5px solid {b['sage']};
  font: inherit; background: #fff; margin-bottom: 20px; }}
.card {{ display: block; padding: 16px 18px; margin-bottom: 12px; border-radius: 16px; background: #fff;
  text-decoration: none; color: inherit; box-shadow: 0 4px 14px rgba(60,55,45,.08); }}
.kw {{ display: inline-block; padding: 2px 12px; border-radius: 999px; background: {b['terracotta']}; color: #fff;
  font-weight: 800; font-size: 13px; letter-spacing: .05em; }}
.t {{ display: block; margin-top: 8px; font-family: Fraunces, serif; font-size: 20px; }}
.s {{ display: block; color: {b['ink_soft']}; font-size: 14px; }}
footer {{ margin-top: 32px; color: {b['ink_soft']}; font-size: 13px; }}
</style></head>
<body><main>
<h1>Free printables 🌿</h1>
<p class="lead">Every activity and research guide from {html.escape(cfg['handle'])}. Find the keyword from the post, tap to open, print or save.</p>
<input id="q" placeholder="Search a keyword, e.g. {html.escape(hist['posts'][-1].get('keyword', 'ALONE') if hist['posts'] else 'ALONE')}" oninput="f()">
<div id="list">{''.join(rows) or '<p>The first printable arrives with our first post.</p>'}</div>
<footer>{html.escape(cfg['name'])} · Montessori ideas for ages 2–4 · Free to print and share</footer>
</main>
<script>
function f() {{ const q = document.getElementById('q').value.toLowerCase();
  document.querySelectorAll('.card').forEach(c => c.style.display = c.textContent.toLowerCase().includes(q) ? '' : 'none'); }}
</script></body></html>"""
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "index.html").write_text(page, encoding="utf-8")


# ---------- publishing ----------

def current_slot(cfg, now):
    """The publish time this run is for: the next one within 75 min (a job starting early), else None."""
    for hhmm in cfg.get("publish_times", []):
        h, m = map(int, hhmm.split(":"))
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if timedelta(minutes=-30) <= target - now <= timedelta(minutes=75):
            return hhmm, target
    return None, None


def warn_if_queue_low(channel_dir, cfg, hist, ready, local_date):
    per_day = max(1, len(cfg.get("publish_times", [])))
    days_left = len(ready) / per_day
    if days_left >= cfg.get("low_queue_days", 3) or hist.get("low_queue_warned") == local_date:
        return
    print(f"  ! only {len(ready)} ready posts left (~{days_left:.1f} days)")
    notify.open_issue(f"{cfg['name']}: only {len(ready)} ready posts left",
                      f"About {days_left:.1f} days of posts remain in the queue. Generate images for the next posts "
                      f"(see `python run_posts.py --channel {channel_dir.name} --status`). If the queue empties, "
                      "posting pauses until new posts are ready.")
    hist["low_queue_warned"] = local_date


def publish_next(channel_dir, cfg, args):
    tz = ZoneInfo(cfg.get("timezone", "UTC"))
    now = datetime.now(tz)
    local_date = now.strftime("%Y-%m-%d")
    if local_date < str(cfg.get("start_date", "")) and not args.dry_run:
        print(f"Posting starts on {cfg['start_date']}; nothing to do today.")
        return
    hist = load_state(channel_dir, "history.json", {"posts": []})
    slot, target = current_slot(cfg, now)
    slot = slot or "manual"
    if slot != "manual" and any(p["date"] == local_date and p["slot"] == slot for p in hist["posts"]):
        print(f"Already posted for {local_date} {slot}; nothing to do.")
        return
    ready = ready_queue(channel_dir, hist, local_date)
    if not ready:
        print("Queue is empty: no post has all its images. Skipping.")
        warn_if_queue_low(channel_dir, cfg, hist, ready, local_date)
        save_state(channel_dir, "history.json", hist)
        return
    post = load_post(channel_dir, ready[0])
    print(f"Next post: {post['id']} ({len(ready) - 1} more ready after it)")
    out_dir = ROOT / "output" / channel_dir.name / post["id"]
    paths = render_post(post, channel_dir, cfg, out_dir)
    caption = (out_dir / "caption.txt").read_text(encoding="utf-8")
    pdf = None
    if post.get("printable") and post.get("keyword"):
        pdf = render_printable(post, channel_dir, cfg, out_dir / f"{post['keyword'].lower()}.pdf")
    if args.dry_run:
        print(f"Dry run: rendered {out_dir}, not posting.")
        return

    if target and target > datetime.now(tz):
        wait = (target - datetime.now(tz)).total_seconds()
        print(f"Waiting {wait / 60:.0f} min to publish at {slot}…")
        time.sleep(wait)
    if pdf:  # printable goes live on the Pages site before the post asks people to comment for it
        DOCS.mkdir(parents=True, exist_ok=True)
        (DOCS / pdf.name).write_bytes(pdf.read_bytes())
    urls = host.upload(paths, f"{channel_dir.name}/{local_date}-{slot.replace(':', '')}-{post['id']}")
    print("Publishing carousel to Instagram…")
    media_id = instagram.publish_carousel(urls, caption, cfg["instagram"]["api_version"])
    print(f"Published {post['id']}! media id {media_id}")
    hist["posts"].append({"id": post["id"], "date": local_date, "slot": slot, "keyword": post.get("keyword"),
                          "posted_at": datetime.now(timezone.utc).isoformat(), "media_id": media_id})
    write_printables_index(channel_dir, cfg, hist)
    warn_if_queue_low(channel_dir, cfg, hist, ready[1:], local_date)
    save_state(channel_dir, "history.json", hist)
    try:
        host.cleanup(channel_dir.name)
    except Exception as e:  # best-effort
        print(f"  (media cleanup skipped: {e})")


# ---------- comment bot ----------

def asks_for(text, kw):
    """A short comment containing the keyword ('CALM', 'calm please 🙏'), not a long one that happens to use the word."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return kw.lower() in words and len(words) <= 4


def reply_to_comments(channel_dir, cfg, args, days=14):
    """Reply publicly to comments that contain the post's keyword, pointing to the printables link in bio."""
    hist = load_state(channel_dir, "history.json", {"posts": []})
    done = load_state(channel_dir, "replied.json", {"ids": []})
    done_ids = set(done["ids"])
    me = cfg["handle"].lstrip("@").lower()
    version = cfg["instagram"]["api_version"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    sent = 0
    for entry in hist["posts"]:
        kw = entry.get("keyword")
        if not kw or datetime.fromisoformat(entry["posted_at"]) < cutoff:
            continue
        for c in instagram.comments(entry["media_id"], version):
            if c["id"] in done_ids or (c.get("username") or "").lower() == me or not asks_for(c.get("text", ""), kw):
                continue
            message = random.Random(c["id"]).choice(REPLIES).format(kw=kw)
            if args.dry_run:
                print(f"  would reply to @{c.get('username')} on {entry['id']}: {message}")
            else:
                instagram.reply_to_comment(c["id"], message, version)
                print(f"  replied to @{c.get('username')} on {entry['id']}")
                time.sleep(2)
            done_ids.add(c["id"])
            done["ids"].append(c["id"])
            sent += 1
            if sent >= MAX_REPLIES_PER_RUN:  # stay well clear of spam limits; the rest wait for the next run
                break
        if sent >= MAX_REPLIES_PER_RUN:
            break
    if not args.dry_run:
        done["ids"] = done["ids"][-3000:]
        save_state(channel_dir, "replied.json", done)
    print(f"Comment replies this run: {sent}")


# ---------- main ----------

def print_status(channel_dir):
    hist = load_state(channel_dir, "history.json", {"posts": []})
    posted = {p["id"] for p in hist["posts"]}
    ready = 0
    for pid in all_post_ids(channel_dir):
        post = load_post(channel_dir, pid)
        missing = missing_images(channel_dir, post)
        state = "posted" if pid in posted else "READY" if not missing else f"needs slides {missing}"
        ready += state == "READY"
        print(f"  {pid}  {state:<28} {post['slides'][0]['title'].replace('*', '')}")
    print(f"{ready} ready, {len(posted)} posted")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--render", metavar="POST_ID", help="render one post for review (slides, caption, printable)")
    ap.add_argument("--publish", action="store_true", help="post the next ready post")
    ap.add_argument("--comments", action="store_true", help="reply to keyword comments")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--whoami", action="store_true", help="check this channel's Instagram token")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252

    load_dotenv(ROOT / ".env")
    for name in ("IG_ACCESS_TOKEN", "IG_USER_ID"):  # local .env: IG_ACCESS_TOKEN_PARENTING etc. win for this channel
        if os.environ.get(f"{name}_{args.channel.upper()}"):
            os.environ[name] = os.environ[f"{name}_{args.channel.upper()}"]
    channel_dir = ROOT / "channels" / args.channel
    cfg = yaml.safe_load((channel_dir / "config.yaml").read_text(encoding="utf-8"))

    if args.render:
        post = load_post(channel_dir, args.render)
        out_dir = ROOT / "output" / args.channel / args.render
        render_post(post, channel_dir, cfg, out_dir)
        if post.get("printable") and post.get("keyword"):
            render_printable(post, channel_dir, cfg, out_dir / f"{post['keyword'].lower()}.pdf")
        print(f"Done: {out_dir}")
    elif args.publish:
        publish_next(channel_dir, cfg, args)
    elif args.comments:
        reply_to_comments(channel_dir, cfg, args)
    elif args.whoami:
        me = instagram._call("GET", "me", cfg["instagram"]["api_version"], fields="user_id,username,account_type")
        print(f"username: {me.get('username')}\nIG_USER_ID: {me.get('user_id')}\naccount type: {me.get('account_type')}")
    else:
        print_status(channel_dir)


if __name__ == "__main__":
    main()
