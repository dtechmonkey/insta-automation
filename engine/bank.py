"""A bank of ready-to-review posts whose images come from the Gemini app (generated in Chrome, in a loop).

  python -m engine.bank plan            # research + write text for all posts, fill the image queue
  python -m engine.bank render <post>   # render one post once its images are in place
  python -m engine.bank status          # queue progress

Layout: output/bank/queue.json (image jobs) and output/bank/<post>/ (post.json, img/NN.jpg, slides, caption).
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

from engine import collect, curate, faces, history, render, repos

ROOT = Path(__file__).resolve().parents[1]
BANK = ROOT / "output" / "bank"
CHANNEL = ROOT / "channels" / "ai-news"
STYLE = ("Photorealistic, cinematic, dramatic lighting, electric blue and warm orange, rich detail, "
         "vertical 3:4 composition, main subject in the upper two-thirds, no text, no letters, no logos, no watermark")

REPO_THEMES = [
    ("AI agents you can run for free", "ai agent", "AGENT"),
    ("Open-source AI video tools", "video generation", "VIDEO"),
    ("Free AI image generators", "image generation stable diffusion", "IMAGE"),
    ("AI coding assistants that are free", "ai coding assistant", "CODE"),
    ("Run ChatGPT-style AI on your own laptop", "local llm", "LOCAL"),
    ("Free alternatives to paid AI apps", "open source alternative ai", "FREE"),
    ("AI automation tools that save hours", "workflow automation ai", "AUTO"),
    ("Open-source AI voice and text-to-speech", "text to speech voice clone", "VOICE"),
    ("AI tools for designers", "ai design ui generation", "DESIGN"),
    ("AI tools for students and studying", "ai study notes pdf", "STUDY"),
    # extra themes, planned only when the queue runs dry ("more")
    ("Open-source AI music generators", "music generation ai", "MUSIC"),
    ("AI browser agents that use the web for you", "browser agent automation", "BROWSER"),
    ("Free tools to chat with your PDFs and documents", "chat with pdf rag", "PDF"),
    ("Open-source AI avatars and talking heads", "talking head avatar", "AVATAR"),
    ("AI tools that clean up and edit photos", "image editing inpainting upscaling", "PHOTO"),
    ("Self-hosted apps that replace paid subscriptions", "self-hosted alternative", "SELFHOST"),
    ("AI research assistants that read papers for you", "research assistant llm papers", "RESEARCH"),
    ("Open-source AI for 3D and game creation", "3d generation ai", "3D"),
    ("AI transcription and subtitle tools", "speech recognition whisper subtitles", "SUBS"),
    ("Privacy-first AI you can run offline", "offline private llm", "OFFLINE"),
    ("AI tools for making presentations and slides", "ai presentation slides", "SLIDES"),
    ("Open-source AI memory and second brain apps", "personal knowledge base ai notes", "NOTES"),
]
CORE_THEMES = 10  # the first 10 are planned up front

# (topic, Wikipedia title, person shown on the cover or "")
EVERGREEN_TOPICS = [
    ("James Webb Space Telescope", "James Webb Space Telescope", ""),
    ("Boston Dynamics Atlas robot", "Atlas (robot)", ""),
    ("Hyperloop", "Hyperloop", ""),
    ("Neuralink brain chip", "Neuralink", "Elon Musk"),
    ("Quantum computers", "Quantum computing", ""),
    ("Burj Khalifa engineering", "Burj Khalifa", ""),
    ("The Large Hadron Collider", "Large Hadron Collider", ""),
    ("Self-driving cars (Waymo)", "Waymo", ""),
    ("The International Space Station", "International Space Station", ""),
    ("Lab-grown meat", "Cultured meat", ""),
    ("Nuclear fusion (ITER)", "ITER", ""),
    ("The first iPhone", "IPhone (1st generation)", "Steve Jobs"),
    ("Deep Blue vs Kasparov", "Deep Blue versus Garry Kasparov", "Garry Kasparov"),
    ("AlphaGo", "AlphaGo", ""),
    ("Starlink satellites", "Starlink", ""),
    ("The Mars rover Perseverance", "Perseverance (rover)", ""),
    ("Ingenuity, the Mars helicopter", "Ingenuity (helicopter)", ""),
    ("Bullet trains (Shinkansen)", "Shinkansen", ""),
    ("Maglev trains", "Shanghai maglev train", ""),
    ("The Hubble Space Telescope", "Hubble Space Telescope", ""),
    ("3D-printed houses", "Construction 3D printing", ""),
    ("Exoskeletons", "Powered exoskeleton", ""),
    ("The Chandrayaan-3 Moon landing", "Chandrayaan-3", ""),
    ("Blue Origin New Shepard", "New Shepard", "Jeff Bezos"),
    ("Tesla Optimus robot", "Optimus (robot)", ""),
    ("The Three Gorges Dam", "Three Gorges Dam", ""),
    ("The Panama Canal", "Panama Canal", ""),
    ("Supersonic Concorde", "Concorde", ""),
    ("GPS", "Global Positioning System", ""),
    ("The Voyager probes", "Voyager 1", ""),
]
TARGET = 10  # keep this many finished posts waiting

REPO_PROMPT = """You write Instagram carousels for "{channel}" about free open-source tools. Theme: {theme}.
Real GitHub repos with README excerpts are below.

The COVER must stop the scroll: a curiosity hook like "{n} GITHUB REPOS THAT FEEL *ILLEGAL* TO KNOW",
"STOP PAYING FOR {topic}: {n} *FREE* REPOS", "{n} AI TOOLS DEVELOPERS DON'T WANT YOU TO KNOW". Max 8 words, 1-2 *asterisk* words.
Describe each repo ONLY from its README: plain English, what it does for the user, no invented claims.

Return only JSON:
{{"cover_headline": "...", "cover_sub": "<max 10 words>", "caption_hook": "<one line>",
  "cover_image_prompt": "<40-70 words: a dramatic scene that sells the theme, e.g. a glowing laptop in a dark room with holographic code and robots; no text>",
  "repos": [{{"full_name": "<as given>", "title": "<2-4 words, *asterisk* on key word>", "what": "<12-20 words>"}}]}}

Repos:
{repos}"""

NEWS_PROMPT = """You write a single-story Instagram carousel for "{channel}" (tech news, futuretech style).
Use ONLY facts in the article below. No speculation. Legal matters: say "accused"/"charged" exactly as the source.
Don't mention nationality/ethnicity/religion unless central. Plain, punchy English.

Return only JSON:
{{"cover_headline": "<hook, max 8 words, 1-2 *asterisk* words, curiosity or stakes, e.g. 'NVIDIA JUST *CHANGED* THE AI RACE'>",
  "cover_sub": "<max 12 words>",
  "slides": [{{"headline": "<2-6 words, *asterisk* key word>", "text": "<15-25 words of facts>"}}, {{"headline": "...", "text": "..."}}],
  "caption_hook": "<one line>", "caption_body": "<2-3 sentences summary>",
  "image_prompts": ["<cover scene, 40-70 words>", "<scene for slide 2>", "<scene for slide 3>"]}}

Article ({source}): {title}
{text}"""


EVERGREEN_PROMPT = """You write an evergreen Instagram carousel for "{channel}" (style: @technology / @futuretech).
Topic: {topic}. Use ONLY facts from the Wikipedia text below; every number must appear in it. No speculation.

The COVER must stop the scroll: a curiosity or "wow" hook, max 8 words, 1-2 *asterisk* words,
e.g. "THIS ROCKET IS *TALLER* THAN A 40-STOREY BUILDING". Then 4 slides, each one surprising fact.

Return only JSON:
{{"cover_headline": "...", "cover_sub": "<max 12 words>",
  "slides": [{{"headline": "<2-6 words, *asterisk* key word>", "text": "<15-25 words of facts>"}}, ... 4 items],
  "caption_hook": "<one line>", "caption_body": "<2-3 sentences>",
  "image_prompts": ["<cover scene, 40-70 words>", "<slide 1 scene>", "<slide 2 scene>", "<slide 3 scene>", "<slide 4 scene>"]}}

Wikipedia: {title}
{text}"""


def _wiki(title, limit=6000):
    r = requests.get("https://en.wikipedia.org/w/api.php", timeout=30, headers=faces.HEADERS, params={
        "action": "query", "prop": "extracts", "explaintext": 1, "titles": title, "redirects": 1, "format": "json"})
    r.raise_for_status()
    page = next(iter(r.json()["query"]["pages"].values()))
    return page.get("title", title), (page.get("extract") or "")[:limit]


def _slug(topic):
    return re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:30]


def plan_evergreen_post(topic, wiki_title, cfg, person=""):
    post_id = "ever-" + _slug(topic)
    if (BANK / post_id / "post.json").exists():
        return
    title, text = _wiki(wiki_title)
    if len(text) < 1000:
        print(f"  skip {topic}: not enough Wikipedia text"); return
    out = curate._ask_gemini(EVERGREEN_PROMPT.format(channel=cfg["name"], topic=topic, title=title, text=text),
                             cfg["ai"], retry_minutes=3)
    d = BANK / post_id
    d.mkdir(parents=True, exist_ok=True)
    story = {"source": "Wikipedia", "url": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}", "person": person}
    (d / "post.json").write_text(json.dumps({"type": "evergreen", "story": story, **out}, indent=1, ensure_ascii=False),
                                 encoding="utf-8")
    _add_jobs(post_id, (out.get("image_prompts") or [topic])[:5])
    print(f"  planned {post_id}: {out.get('cover_headline')}")


def _cfg():
    return yaml.safe_load((CHANNEL / "config.yaml").read_text(encoding="utf-8"))


def _queue():
    q = BANK / "queue.json"
    return json.loads(q.read_text(encoding="utf-8")) if q.exists() else []


def _save_queue(jobs):
    BANK.mkdir(parents=True, exist_ok=True)
    (BANK / "queue.json").write_text(json.dumps(jobs, indent=1, ensure_ascii=False), encoding="utf-8")


def _add_jobs(post_id, prompts):
    jobs = [j for j in _queue() if j["post"] != post_id]
    for n, p in enumerate(prompts):
        jobs.append({"post": post_id, "file": f"img/{n:02d}.jpg", "prompt": f"{p}. {STYLE}", "status": "pending"})
    _save_queue(jobs)


def plan_repo_post(theme, query, keyword, cfg):
    if (BANK / ("repos-" + keyword.lower()) / "post.json").exists():
        return  # already planned
    found = repos.search(f"{query} in:name,description,topics", n=6)
    if len(found) < 4:
        print(f"  skip {theme}: only {len(found)} repos"); return
    for r in found:
        r["readme"] = repos.readme(r["full_name"], 1500)
    payload = [{"full_name": r["full_name"], "description": r["description"], "readme": r["readme"]} for r in found]
    text = curate._ask_gemini(REPO_PROMPT.format(channel=cfg["name"], theme=theme, n=len(found),
                                                 topic=theme.upper(), repos=json.dumps(payload, ensure_ascii=False)),
                              cfg["ai"], retry_minutes=3)
    post_id = "repos-" + keyword.lower()
    d = BANK / post_id
    d.mkdir(parents=True, exist_ok=True)
    for r in found:
        r.pop("readme", None)
    repo_text = text.pop("repos", [])
    (d / "post.json").write_text(json.dumps({"type": "repos", "theme": theme, "keyword": keyword, "repos": found,
                                             "repo_text": repo_text, **text},
                                            indent=1, ensure_ascii=False), encoding="utf-8")
    _add_jobs(post_id, [text.get("cover_image_prompt") or theme])
    print(f"  planned {post_id}: {text.get('cover_headline')}")


def _article(url):
    try:
        import trafilatura
        html = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"}).text
        return (trafilatura.extract(html) or "")[:5000]
    except Exception:
        return ""


def plan_news_posts(cfg, n=7):
    hist = history.load(CHANNEL)
    seen_urls, seen_titles = history.seen(hist)
    items = collect.collect(cfg["sources"], datetime.now(timezone.utc) - timedelta(hours=36), seen_urls, seen_titles)
    picked = curate.curate_daily(items, cfg, history.recently_posted(hist))["stories"][:n]
    for i, s in enumerate(picked, 1):
        body = _article(s["url"]) or next((x["summary"] for x in items if x["url"] == s["url"]), "")
        try:
            text = curate._ask_gemini(NEWS_PROMPT.format(channel=cfg["name"], source=s["source"], title=s["headline"],
                                                         text=body), cfg["ai"], retry_minutes=3)
        except Exception as e:
            print(f"  skip story {i}: {e}"); continue
        post_id = f"news-{datetime.now():%m%d-%H%M}-{i}"
        d = BANK / post_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "post.json").write_text(json.dumps({"type": "news", "story": s, **text}, indent=1, ensure_ascii=False),
                                     encoding="utf-8")
        _add_jobs(post_id, (text.get("image_prompts") or [s["image_prompt"]])[:3])
        print(f"  planned {post_id}: {text.get('cover_headline')}")


def _uri(path):
    import base64
    p = Path(path)
    return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode() if p.exists() else None


def _hl(t):
    import html
    return re.sub(r"\*(.+?)\*", r'<span class="hl">\1</span>', html.escape(t or ""))


def render_post(post_id):
    cfg = _cfg()
    d = BANK / post_id
    post = json.loads((d / "post.json").read_text(encoding="utf-8"))
    base = {"brand": cfg["brand"], "name": cfg["name"], "handle": cfg["handle"]}
    if post["type"] == "repos":
        from engine import listpost
        cards = [listpost._data_uri(r["card"], f"https://github.com/{r['owner']}.png?size=600") for r in post["repos"]]
        total = len(post["repos"]) + 2
        headline = re.sub(r"\b\d+\b", str(len(post["repos"])), post.get("cover_headline", ""), count=1)
        slides = [{"template": "repo.html", "cover": True, "image": _uri(d / "img/00.jpg"), "cards": [c for c in cards if c] * 2,
                   "headline_html": _hl(headline), "sub": post.get("cover_sub", "")}]
        text_by = {r.get("full_name"): r for r in post.get("repo_text", [])}
        for n, (r, card) in enumerate(zip(post["repos"], cards), 1):
            t = text_by.get(r["full_name"], {})
            slides.append({"template": "repo.html", "index": n, "pager": f"{n + 1}/{total}", "repo": r, "card": card,
                           "title_html": _hl(t.get("title") or r["name"]), "what": t.get("what") or r["description"],
                           "stars": repos.stars_label(r["stars"])})
        slides.append({"template": "repo.html", "outro": True})
        render.render_slides(slides, CHANNEL / "templates", d, {**base, "keyword": post["keyword"], "count": len(post["repos"])})
        caption = (f"{post.get('caption_hook', '')}\n\n" + "\n".join(
            f"{n}. {(text_by.get(r['full_name'], {}).get('title') or r['name']).replace('*', '')}" for n, r in enumerate(post["repos"], 1))
            + f"\n\n💬 Comment \"{post['keyword']}\" and we'll DM you all {len(post['repos'])} links.\n"
            f"🔖 Save this · Follow {cfg['handle']} for more free tools\n\n#github #opensource #aitools #ai #techmonkey")
        (d / "links.txt").write_text("\n".join(f"{r['name']}: {r['url']}" for r in post["repos"]), encoding="utf-8")
    else:
        s = post["story"]
        credit = None
        if s.get("person"):
            credit = faces.portrait(s["person"], d / "person.png")
        person = _uri(d / "person.png") if credit else None
        n_pts = 4 if post["type"] == "evergreen" else 2
        imgs = [_uri(d / f"img/{n:02d}.jpg") for n in range(n_pts + 1)]
        pts = post.get("slides", [])[:n_pts]
        total = len(pts) + 2
        slides = [{"template": "hero.html", "cover": True, "image": imgs[0], "person": person,
                   "headline_html": _hl(post.get("cover_headline")), "facts": post.get("cover_sub"), "cta": "Swipe for more"}]
        for k, p in enumerate(pts, 1):
            slides.append({"template": "hero.html", "image": imgs[k] or imgs[0], "pager": f"{k + 1}/{total}",
                           "headline_html": _hl(p.get("headline")), "facts": p.get("text"), "source": s["source"]})
        slides.append({"template": "hero.html", "image": imgs[0], "pager": f"{total}/{total}",
                       "headline_html": _hl("Follow for *daily* tech news"),
                       "facts": f"Follow {cfg['handle']} so you never miss a story", "cta": "Follow now"})
        render.render_slides(slides, CHANNEL / "templates", d, base)
        caption = "\n\n".join(x for x in [
            post.get("caption_hook", ""), post.get("caption_body", ""), f"Source: {s['source']}",
            f"Photo: {credit['credit']}" if credit else "",
            "Summary written with AI assistance. AI-generated illustrations are not real photos.",
            f"Follow {cfg['handle']} for daily tech news", "#technews #ainews #artificialintelligence #technewsindia #techmonkey"] if x)
    (d / "caption.txt").write_text(caption, encoding="utf-8")
    (d / "READY").write_text("", encoding="utf-8")
    print(f"rendered {post_id}")


def status():
    jobs = _queue()
    done = sum(j["status"] == "done" for j in jobs)
    nxt = next((j for j in jobs if j["status"] == "pending"), None)
    ready = sorted(p.name for p in BANK.glob("*/READY"))
    print(f"images {done}/{len(jobs)} done; posts ready: {len([p for p in BANK.glob('*') if (p / 'READY').exists()])}")
    if nxt:
        print(f"NEXT {nxt['post']} {nxt['file']}\nPROMPT {nxt['prompt']}")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "plan":
        cfg = _cfg()
        plan_news_posts(cfg)
        for theme in REPO_THEMES[:CORE_THEMES]:
            try:
                plan_repo_post(*theme, cfg)
            except Exception as e:
                print(f"  skip {theme[0]}: {e}")
        status()
    elif cmd == "more":  # queue ran dry but Gemini still has quota: plan 4 more repo posts + fresh news
        cfg = _cfg()
        todo = [t for t in REPO_THEMES if not (BANK / ("repos-" + t[2].lower()) / "post.json").exists()][:4]
        for theme in todo:
            try:
                plan_repo_post(*theme, cfg)
            except Exception as e:
                print(f"  skip {theme[0]}: {e}")
        if not todo:
            plan_news_posts(cfg, n=5)
        status()
    elif cmd == "plan2":  # today's 2 posts: one GitHub repo list + one evergreen
        cfg = _cfg()
        plan_repo_post(*REPO_THEMES[0], cfg)
        plan_evergreen_post("SpaceX Starship", "SpaceX Starship", cfg)
        status()
    elif cmd == "topup":  # bank running low and no images pending: plan 1 repo list + 1 evergreen post
        waiting = {d.name for d in (ROOT / "bank").glob("*") if (d / "READY").exists()} |                   {d.name for d in BANK.glob("*") if (d / "READY").exists()}
        posted = {p.get("bank_id") for p in json.loads((ROOT / "channels/ai-news/history.json").read_text(encoding="utf-8"))["posts"]}
        waiting -= posted
        pending = [j for j in _queue() if j["status"] == "pending"]
        if pending or len(waiting) >= TARGET:
            print(f"no top-up: {len(waiting)} waiting, {len(pending)} images pending")
        else:
            cfg = _cfg()
            for t in [t for t in REPO_THEMES if not (BANK / ("repos-" + t[2].lower()) / "post.json").exists()][:1]:
                try:
                    plan_repo_post(*t, cfg)
                except Exception as e:
                    print(f"  skip {t[0]}: {e}")
            for topic, title, person in [t for t in EVERGREEN_TOPICS
                                         if not (BANK / ("ever-" + _slug(t[0])) / "post.json").exists()][:1]:
                try:
                    plan_evergreen_post(topic, title, cfg, person)
                except Exception as e:
                    print(f"  skip {topic}: {e}")
        status()
    elif cmd == "render":
        render_post(sys.argv[2])
    elif cmd == "done":  # mark a job finished: python -m engine.bank done <post> <file>
        jobs = _queue()
        for j in jobs:
            if j["post"] == sys.argv[2] and j["file"] == sys.argv[3]:
                j["status"] = "done"
        _save_queue(jobs)
        post_jobs = [j for j in jobs if j["post"] == sys.argv[2]]
        if all(j["status"] == "done" for j in post_jobs):
            render_post(sys.argv[2])
        status()
    else:
        status()
