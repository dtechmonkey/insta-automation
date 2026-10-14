"""Pick the top stories and write slide text + caption with the free Gemini API.

If every configured model fails, raises CurationFailed and the post is skipped:
we never publish text copied from the sources.
"""
import json
import os
import time

import requests

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
CATEGORIES = ["Model Release", "Product", "Research", "Funding", "Policy", "Industry", "Open Source", "Hardware"]

STORY_SCHEMA = """{
  "stories": [
    {"id": <id of the input item>,
     "headline": "<2-5 punchy words, like a bold magazine cover line, e.g. 'GLASSES *KILL* PHONES'. Wrap the 1-2 most striking words in *asterisks*. Dramatic but true.>",
     "facts": "<2-3 short, punchy sentences, 20-30 words total, packed with the concrete numbers, names and dates from the item. Your own words.>",
     "category": "<one of: %s>",
     "image_prompt": "<60-90 words describing a rich, layered, photorealistic collage-style composition like a viral tech-news carousel slide. Include: (1) a large hero subject in the foreground: the product, machine, chip, robot, rocket or vehicle at the heart of the story, OR, if a public figure is central, that person as a confident portrait (e.g. arms crossed, gazing off-camera) who is NOT shown doing anything from the story; (2) 2-3 secondary story elements in the midground (e.g. a launch pad, a data center, a city skyline, a factory floor, a phone, stacks of cash); (3) holographic overlays: a glowing rising chart, floating UI panels, data streams, network lines, map pins. Name the setting, lighting (dramatic sunset, neon, studio rim light), and the electric-blue and warm-orange color palette. Never ask for readable text, captions or lettered logos.>"}
  ],
  "cover_headline": "<max 6 words for the cover slide with *asterisks* on the key words, e.g. '*7 AI STORIES* YOU CAN'T MISS'>",
  "cover_subline": "<max 12 words teasing the biggest stories>",
  "cover_image_prompt": "<60-90 words: an epic photorealistic collage combining the hero elements of the top 3 stories into one dramatic scene (e.g. a robot, a glowing chip and a rocket around a central figure or device), dense and layered with holographic charts and glowing data, electric-blue and warm-orange palette, no readable text>",
  "caption": "<Instagram caption: a hook line, a blank line, then one line per story starting with an emoji and its number, then a call to follow/save. Max 1500 characters. No hashtags.>"
}""" % ", ".join(CATEGORIES)

RULES = """Rules:
- Use only facts stated in the items. Never invent numbers, names, dates or quotes. No speculation.
- Headlines must be complete, grammatical phrases that make sense on their own (never end on a dangling word like "IN" or "FOR").
- Don't add time words ("TODAY", "NOW", "JUST") unless the item says so.
- Legal matters involving a person: never call them a criminal, spy, fraud etc. in a headline. Use what the source says ("CHARGED", "ACCUSED", "ARRESTED") and keep "allegedly" in the facts.
- Write everything in your own words; do not copy sentences from the items.
- Skip minor, promotional, duplicate or non-AI items. Prefer variety across categories.
- Order stories from most to least important.
- The channel's Instagram handle is exactly {handle}; never write any other handle.
- Tone: {tone}.
Return only JSON in exactly this shape:
""" + STORY_SCHEMA


def _ask_model(prompt, model, key, attempts=2):
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4},
    }
    for attempt in range(attempts):
        try:
            resp = requests.post(
                GEMINI_URL.format(model=model),
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json=body,
                timeout=240,
            )
        except requests.RequestException as e:
            problem = type(e).__name__
        else:
            if resp.ok:
                parts = resp.json()["candidates"][0]["content"]["parts"]
                return json.loads("".join(p.get("text", "") for p in parts))
            if resp.status_code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"{model} error {resp.status_code}: {resp.text[:200]}")
            problem = resp.status_code
        if attempt < attempts - 1:
            wait = 30 * (attempt + 1)
            print(f"  {model} busy ({problem}), retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"{model} unavailable after {attempts} tries")


def _ask_gemini(prompt, models):
    """Try each configured model in turn (a string or a list in config)."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    for model in [models] if isinstance(models, str) else models:
        try:
            return _ask_model(prompt, model, key)
        except RuntimeError as e:
            print(f"  ! {e}")
    raise RuntimeError("all models failed")


def _merge(result, items, n):
    """Attach source/url from our own items (never trust the model for those)."""
    by_id = {item["id"]: item for item in items}
    stories, used = [], set()
    for s in result.get("stories", []):
        item = by_id.get(s.get("id"))
        if item is None or item["id"] in used or not s.get("headline"):
            continue
        used.add(item["id"])
        stories.append({
            "headline": s["headline"].strip(),
            "facts": s.get("facts", "").strip(),
            "image_prompt": s.get("image_prompt", "").strip() or item["title"],
            "category": s.get("category") if s.get("category") in CATEGORIES else "Industry",
            "source": item["source"],
            "url": item["url"],
            "published": item.get("published"),
        })
        if len(stories) == n:
            break
    return stories


class CurationFailed(RuntimeError):
    """The AI writing step failed; the post is skipped (no copied-text fallback)."""


def _items_for_prompt(items):
    return json.dumps([
        {"id": i["id"], "title": i["title"], "summary": i["summary"], "source": i["source"],
         "also_reported_by": i.get("also_reported_by", [])}
        for i in items
    ], ensure_ascii=False)


def curate_daily(items, cfg):
    n = cfg["daily"]["stories"]
    prompt = (
        f'You are the editor of "{cfg["name"]}", an Instagram channel posting daily AI news.\n'
        f"Below are AI news items from the last 24 hours. Items reported by more outlets are usually bigger news.\n"
        f"Pick the {n} most important, distinct stories.\n\n"
        + _rules(cfg)
        + "\n\nItems:\n" + _items_for_prompt(items)
    )
    return _run(prompt, items, n, cfg)


def _rules(cfg):
    return RULES.replace("{tone}", cfg["ai"]["tone"]).replace("{handle}", cfg["handle"])


def curate_weekly(items, cfg):
    n = cfg["weekly"]["stories"]
    prompt = (
        f'You are the editor of "{cfg["name"]}", an Instagram channel about tech news.\n'
        f"Below are the stories we covered this week. Pick the {n} most significant for a "
        f'"This Week in Tech" recap and rewrite them freshly. Also add "biggest_id": the id of the '
        f"single biggest story of the week, and make the caption a weekly recap.\n\n"
        + _rules(cfg).replace('"cover_headline"', '"biggest_id": <id>,\n  "cover_headline"')
        + "\n\nItems:\n" + _items_for_prompt(items)
    )
    return _run(prompt, items, n, cfg)


def _run(prompt, items, n, cfg):
    try:
        raw = _ask_gemini(prompt, cfg["ai"]["model"])
        stories = _merge(raw, items, n)
        if not stories:
            raise RuntimeError("Gemini returned no usable stories")
        biggest = next((i for i in items if i["id"] == raw.get("biggest_id")), None)
        return {
            "stories": stories,
            "cover_headline": raw.get("cover_headline", "").strip() or f"*{len(stories)} stories* you can't miss",
            "cover_subline": raw.get("cover_subline", "").strip(),
            "cover_image_prompt": raw.get("cover_image_prompt", "").strip() or stories[0]["image_prompt"],
            "caption": raw.get("caption", "").strip(),
            "biggest_url": biggest["url"] if biggest else None,
        }
    except Exception as e:
        raise CurationFailed(f"AI writing failed ({e})") from e
