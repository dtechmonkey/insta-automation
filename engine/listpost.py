"""Build an "N GitHub repos you should know" carousel (syntaix-style list post) from real repo data."""
import base64
import json
import re
from pathlib import Path

import requests

from engine import curate, render, repos

PROMPT = """You write Instagram carousel text for "{channel}", about free open-source tools.
Theme: {theme}. Below are real GitHub repos with their README excerpts.

Rules:
- Describe each repo ONLY from its README/description. No invented features, numbers or claims.
- Plain English for non-developers: say what it does for the user, not how it's built.
- No hype words that the README doesn't support ("best", "revolutionary").

Return only JSON:
{{"cover_headline": "<max 7 words, *asterisks* on 1-2 key words, e.g. '6 *FREE AI AGENTS* WORTH TRYING'>",
  "cover_sub": "<max 10 words>",
  "caption_hook": "<one catchy first caption line>",
  "repos": [{{"full_name": "<exactly as given>", "title": "<2-4 words naming what it is, *asterisks* on the key word>",
              "what": "<one sentence, 12-20 words, what it lets you do>"}}]}}

Repos:
{repos}"""


def _data_uri(url, fallback=None):
    """Download an image as a data URI; retry politely on rate limits, then try the fallback URL."""
    import time
    for attempt, target in enumerate([url, url, url, fallback]):
        if not target:
            break
        try:
            r = requests.get(target, timeout=30)
            if r.status_code == 429:
                time.sleep(8 * (attempt + 1))
                continue
            r.raise_for_status()
            mime = r.headers.get("content-type", "image/png").split(";")[0]
            return f"data:{mime};base64," + base64.b64encode(r.content).decode()
        except requests.RequestException:
            time.sleep(3)
    return ""


def _hl(text):
    import html
    return re.sub(r"\*(.+?)\*", r'<span class="hl">\1</span>', html.escape(text))


def build(theme, full_names, keyword, cfg, channel_dir, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    info = []
    for name in full_names:
        r = requests.get(f"{repos.API}/repos/{name}", headers=repos._headers(), timeout=30)
        r.raise_for_status()
        j = r.json()
        info.append({"full_name": j["full_name"], "url": j["html_url"], "description": j.get("description") or "",
                     "stars": j["stargazers_count"], "language": j.get("language") or "",
                     "license": (j.get("license") or {}).get("spdx_id") or "",
                     "card": f"https://opengraph.githubassets.com/1/{j['full_name']}",
                     "readme": repos.readme(j["full_name"], 1800)})
    payload = [{"full_name": i["full_name"], "description": i["description"], "readme": i["readme"]} for i in info]
    text = curate._ask_gemini(PROMPT.format(channel=cfg["name"], theme=theme,
                                            repos=json.dumps(payload, ensure_ascii=False)), cfg["ai"])
    by_name = {r["full_name"]: r for r in text.get("repos", [])}

    cards = [_data_uri(i["card"], f"https://github.com/{i['full_name'].split('/')[0]}.png?size=600") for i in info]
    total = len(info) + 2
    base = {"name": cfg["name"], "handle": cfg["handle"], "keyword": keyword, "count": len(info)}
    # The model sometimes miscounts: force the number on the cover to the real repo count.
    headline = re.sub(r"\b\d+\b", str(len(info)), text.get("cover_headline", theme), count=1)
    slides = [{"template": "repo.html", "cover": True, "cards": [c for c in cards if c] * 2,
               "headline_html": _hl(headline), "sub": text.get("cover_sub", "")}]
    for n, (i, card) in enumerate(zip(info, cards), 1):
        t = by_name.get(i["full_name"], {})
        slides.append({"template": "repo.html", "index": n, "pager": f"{n + 1}/{total}", "repo": i, "card": card,
                       "title_html": _hl(t.get("title") or i["full_name"].split("/")[1]),
                       "what": t.get("what") or i["description"], "stars": repos.stars_label(i["stars"])})
    slides.append({"template": "repo.html", "outro": True})
    render.render_slides(slides, channel_dir / "templates", out_dir, base)

    links = "\n".join(f"{n}. {i['full_name'].split('/')[1]}: {i['url']}" for n, i in enumerate(info, 1))
    caption = (f"{text.get('caption_hook', theme)}\n\n"
               + "\n".join(f"{n}. {(by_name.get(i['full_name'], {}).get('title') or i['description'][:40]).replace('*', '')} "
                           f"({i['full_name'].split('/')[1]})" for n, i in enumerate(info, 1))
               + f"\n\n💬 Comment \"{keyword}\" and we'll DM you all {len(info)} links.\n"
               f"🔖 Save this for later · Follow {cfg['handle']} for more free tools\n\n"
               "#github #opensource #aitools #ai #techmonkey")
    (out_dir / "caption.txt").write_text(caption, encoding="utf-8")
    (out_dir / "links.txt").write_text(links, encoding="utf-8")  # what the comment-to-DM reply will send
    return out_dir
