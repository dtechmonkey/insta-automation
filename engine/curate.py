"""Pick the top stories and write slide text + caption with the free Gemini API.

Quality layers:
  A. check_story(): deterministic rules in code (dangling words, banned words, lengths).
  B. an AI "editor" pass that checks every story against its source and fixes problems;
     anything still failing A gets one targeted rewrite, then is dropped.
  C. strong models are retried for a while before the lightweight fallback is used.

If too few stories survive, or every model fails, raises CurationFailed and the post is
skipped: we never publish text copied from the sources.
"""
import json
import os
import re
import time

import requests

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
CATEGORIES = ["Model Release", "Product", "Research", "Funding", "Policy", "Industry", "Open Source", "Hardware"]

# Layer A word lists (compared case-insensitively against headline words).
DANGLING = {"IN", "INTO", "FOR", "OF", "TO", "AT", "ON", "WITH", "AND", "OR", "BUT", "THE", "A", "AN",
            "BY", "FROM", "AS", "ITS", "THEIR", "HIS", "HER", "OVER", "AFTER", "BEFORE", "VS"}
BANNED = {"TODAY", "TONIGHT", "NOW", "JUST", "YESTERDAY", "SPY", "SPIES", "CRIMINAL", "CRIMINALS",
          "FRAUD", "FRAUDSTER", "THIEF", "CROOK", "SCAMMER", "LIAR", "TRAITOR"}

STORY_SCHEMA = """{
  "stories": [
    {"id": <id of the input item>,
     "headline": "<2-6 punchy words, like a bold magazine cover line, e.g. 'GLASSES *KILL* PHONES'. Wrap the 1-2 most striking words in *asterisks*. A complete phrase that is dramatic but true.>",
     "facts": "<1-2 short, punchy sentences, 15-25 words total (about 4 lines on the slide), with the most important number, name or date from the item. Your own words.>",
     "caption_line": "<one sentence for the caption list, starting with a fitting emoji; no number>",
     "broll_query": "<2-4 plain English words to search a STOCK FOOTAGE library for a real video clip that fits the story visually, e.g. 'data center servers', 'robot arm factory', 'courtroom gavel', 'rocket launch'. No brand or person names.>",
     "space": <true if the story is about space, rockets, satellites or astronomy, else false>,
     "company": "<the single main company the story is about, e.g. 'Apple', 'NVIDIA', or empty string>",
     "person": "<full name (as on Wikipedia) of the one well-known public figure central to this story, e.g. 'Jensen Huang'; empty string if none or if the person is a private individual or accused of a crime>",
     "category": "<one of: %s>",
     "image_prompt": "<60-90 words describing a rich, layered, photorealistic collage-style composition like a viral tech-news carousel slide. Include: (1) a large hero subject in the foreground: the product, machine, chip, robot, rocket or vehicle at the heart of the story, OR, if a public figure is central, that person as a confident portrait (e.g. arms crossed, gazing off-camera) who is NOT shown doing anything from the story; (2) 2-3 secondary story elements in the midground (e.g. a launch pad, a data center, a city skyline, a factory floor, a phone, stacks of cash); (3) holographic overlays: a glowing rising chart, floating UI panels, data streams, network lines, map pins. Name the setting, lighting (dramatic sunset, neon, studio rim light), and the electric-blue and warm-orange color palette. Never ask for readable text, captions or lettered logos.>"}
  ],
  "cover_headline": "<max 6 words for the cover slide with *asterisks* on the key words, e.g. '*7 AI STORIES* YOU CAN'T MISS'>",
  "cover_subline": "<max 12 words teasing the biggest stories>",
  "cover_person": "<full Wikipedia name of the best-known public figure across today's top stories, or empty string>",
  "cover_image_prompt": "<60-90 words: an epic photorealistic collage combining the hero elements of the top 3 stories into one dramatic scene (e.g. a robot, a glowing chip and a rocket around a central figure or device), dense and layered with holographic charts and glowing data, electric-blue and warm-orange palette, no readable text>",
  "caption_hook": "<one catchy opening line for the Instagram caption; don't mention how many stories>",
  "caption_cta": "<one closing line asking people to follow {handle} and save the post>"
}""" % ", ".join(CATEGORIES)

HEADLINE_RULES = """- Use only facts stated in the items. Never invent numbers, names, dates or quotes. No speculation.
- Headlines must be complete, grammatical phrases that make sense on their own; never end on a dangling word like "IN", "FOR" or "OF".
- Don't use time words ("TODAY", "NOW", "JUST", "TONIGHT") in headlines.
- Legal matters involving a person: never call them a criminal, spy, fraud etc. Use exactly what the source says ("CHARGED", "ACCUSED", "ARRESTED") and keep "allegedly" in the facts.
- Don't mention a person's nationality, ethnicity or religion unless it is central to the story.
- Write everything in your own words; do not copy sentences from the items."""

RULES = "Rules:\n" + HEADLINE_RULES + """
- Skip minor, promotional, duplicate or non-AI items. Prefer variety across categories.
- Order stories from most to least important.
- The channel's Instagram handle is exactly {handle}; never write any other handle.
- Tone: {tone}.
Return only JSON in exactly this shape:
""" + STORY_SCHEMA


class CurationFailed(RuntimeError):
    """The AI writing step failed; the post is skipped (no copied-text fallback)."""


# ---------- Gemini calls (layer C: prefer strong models) ----------

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


def _ask_gemini(prompt, ai_cfg, retry_minutes=None):
    """Try the strong models in rounds until the retry window closes, then the fallback models."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    strong = ai_cfg["models"]
    fallback = ai_cfg.get("fallback_models", [])
    window = ai_cfg.get("strong_retry_minutes", 10) if retry_minutes is None else retry_minutes
    deadline = time.time() + window * 60
    while True:
        for model in strong:
            try:
                return _ask_model(prompt, model, key)
            except RuntimeError as e:
                print(f"  ! {e}")
        if time.time() + 90 > deadline:
            break
        print("  strong models busy; trying them again in 90s")
        time.sleep(90)
    for model in fallback:
        try:
            print(f"  using fallback model {model}")
            return _ask_model(prompt, model, key)
        except RuntimeError as e:
            print(f"  ! {e}")
    raise RuntimeError("all models failed")


# ---------- Layer A: deterministic checks ----------

def _words(headline):
    return re.findall(r"[A-Za-z0-9$%.'&+-]+", headline.replace("*", " "))


def check_headline(headline, max_words=7):
    problems = []
    words = _words(headline)
    if not 2 <= len(words) <= max_words:
        problems.append(f"headline has {len(words)} words (need 2-{max_words})")
    if headline.count("*") % 2:
        problems.append("unbalanced * highlight markers")
    upper = [w.upper().strip(".'") for w in words]
    if upper and upper[-1] in DANGLING:
        problems.append(f"headline ends on the dangling word '{words[-1]}'")
    banned = sorted({w for w in upper if w in BANNED})
    if banned:
        problems.append(f"headline uses banned word(s): {', '.join(banned)}")
    return problems


def check_story(story):
    problems = check_headline(story.get("headline", ""))
    n = len(story.get("facts", "").split())
    if not 10 <= n <= 28:
        problems.append(f"facts have {n} words (need 10-28)")
    if not story.get("caption_line", "").strip():
        problems.append("missing caption_line")
    return problems


# ---------- Layer B: AI editor pass ----------

EDITOR_PROMPT = """You are a strict fact-checking editor for an Instagram tech-news channel.
For each story below, compare the headline and facts against its SOURCE (title + summary).
Fix anything that is: inaccurate or not supported by the source; exaggerated or clickbait beyond what the
source says; unfair to a named person (see rules); an incomplete or ungrammatical headline; or listed
under "known_problems". Keep the punchy style, the *asterisk* highlights and roughly the same length.
If a story is already fine, return it unchanged.

Rules:
{rules}

Return only JSON: {{"stories": [{{"id": <id>, "headline": "...", "facts": "...", "caption_line": "...", "changed": true|false, "note": "<what you fixed, or empty>"}}]}}

Stories:
{stories}"""


def _editor_pass(stories, sources, ai_cfg, problems_by_id=None):
    payload = [{"id": i, "headline": s["headline"], "facts": s["facts"], "caption_line": s.get("caption_line", ""),
                "source_title": sources[i]["title"], "source_summary": sources[i]["summary"],
                "known_problems": (problems_by_id or {}).get(i, [])}
               for i, s in enumerate(stories) if problems_by_id is None or i in problems_by_id]
    prompt = EDITOR_PROMPT.format(rules=HEADLINE_RULES, stories=json.dumps(payload, ensure_ascii=False))
    raw = _ask_gemini(prompt, ai_cfg, retry_minutes=5)
    for fix in raw.get("stories", []):
        i = fix.get("id")
        if isinstance(i, int) and 0 <= i < len(stories):
            for field in ("headline", "facts", "caption_line"):
                if fix.get(field, "").strip():
                    stories[i][field] = fix[field].strip()
            if fix.get("changed") and fix.get("note"):
                print(f"  editor fixed story {i + 1}: {fix['note'][:120]}")


def _quality_gate(stories, sources, ai_cfg, min_stories):
    """Layers B then A: editor pass, code checks, one targeted rewrite, then drop what still fails."""
    _editor_pass(stories, sources, ai_cfg)
    problems = {i: p for i, s in enumerate(stories) if (p := check_story(s))}
    if problems:
        for i, p in problems.items():
            print(f"  story {i + 1} fails checks: {'; '.join(p)}")
        _editor_pass(stories, sources, ai_cfg, problems_by_id=problems)
    kept = []
    for i, (story, source) in enumerate(zip(stories, sources)):
        if p := check_story(story):
            print(f"  dropping story {i + 1} ({story['headline']}): {'; '.join(p)}")
        else:
            kept.append((story, source))
    if len(kept) < min_stories:
        raise CurationFailed(f"only {len(kept)} stories passed the quality checks (need {min_stories})")
    return kept


# ---------- Main curation ----------

def _merge(result, items, n):
    """Attach source/url from our own items (never trust the model for those)."""
    by_id = {item["id"]: item for item in items}
    stories, sources, used = [], [], set()
    for s in result.get("stories", []):
        item = by_id.get(s.get("id"))
        if item is None or item["id"] in used or not s.get("headline"):
            continue
        used.add(item["id"])
        stories.append({
            "headline": s["headline"].strip(),
            "facts": s.get("facts", "").strip(),
            "caption_line": s.get("caption_line", "").strip(),
            "image_prompt": s.get("image_prompt", "").strip() or item["title"],
            "broll_query": (s.get("broll_query") or "").strip(),
            "space": bool(s.get("space")),
            "company": (s.get("company") or "").strip(),
            "person": (s.get("person") or "").strip(),
            "category": s.get("category") if s.get("category") in CATEGORIES else "Industry",
            "source": item["source"],
            "source_title": item["title"],
            "url": item["url"],
            "published": item.get("published"),
        })
        sources.append(item)
        if len(stories) == n:
            break
    return stories, sources


def _items_for_prompt(items):
    return json.dumps([
        {"id": i["id"], "title": i["title"], "summary": i["summary"], "source": i["source"],
         "also_reported_by": i.get("also_reported_by", [])}
        for i in items
    ], ensure_ascii=False)


def _rules(cfg):
    return RULES.replace("{tone}", cfg["ai"]["tone"]).replace("{handle}", cfg["handle"])


def _recent_block(recent):
    if not recent:
        return ""
    return ("\nALREADY POSTED in the last few days (do NOT pick these stories again, even from a different "
            "outlet, unless there is a major new development; if so, the headline must say what is new):\n- "
            + "\n- ".join(recent) + "\n")


def curate_daily(items, cfg, recent=None):
    n = cfg["daily"]["stories"]
    prompt = (
        f'You are the editor of "{cfg["name"]}", an Instagram channel posting daily AI news.\n'
        f"Below are AI news items from the last 24 hours. Items reported by more outlets are usually bigger news.\n"
        f"Pick the {n} most important, distinct stories.\n"
        + _recent_block(recent) + "\n"
        + _rules(cfg)
        + "\n\nItems:\n" + _items_for_prompt(items)
    )
    return _run(prompt, items, n, cfg, cfg["daily"]["min_stories"])


def curate_weekly(items, cfg):
    n = cfg["weekly"]["stories"]
    prompt = (
        f'You are the editor of "{cfg["name"]}", an Instagram channel about tech news.\n'
        f"Below are the stories we covered this week. Pick the {n} most significant for a "
        f'"This Week in Tech" recap and rewrite them freshly. Also add "biggest_id": the id of the '
        f"single biggest story of the week.\n\n"
        + _rules(cfg).replace('"cover_headline"', '"biggest_id": <id>,\n  "cover_headline"')
        + "\n\nItems:\n" + _items_for_prompt(items)
    )
    return _run(prompt, items, n, cfg, 3)


def _cover_headline(raw_headline, n_original, n_final):
    """Keep the story count on the cover in sync after stories were dropped."""
    if not raw_headline:
        return f"*{n_final} AI stories* you can't miss"
    if n_final != n_original:
        raw_headline = re.sub(rf"\b{n_original}\b", str(n_final), raw_headline, count=1)
    return raw_headline


def _run(prompt, items, n, cfg, min_stories):
    try:
        raw = _ask_gemini(prompt.replace("{handle}", cfg["handle"]), cfg["ai"])
        stories, sources = _merge(raw, items, n)
        if not stories:
            raise RuntimeError("Gemini returned no usable stories")
        n_original = len(stories)
        kept = _quality_gate(stories, sources, cfg["ai"], min_stories)
        stories = [s for s, _ in kept]
        biggest = next((i for i in items if i["id"] == raw.get("biggest_id")), None)
        cover = _cover_headline(raw.get("cover_headline", "").strip(), n_original, len(stories))
        if check_headline(cover, max_words=8):
            cover = f"*{len(stories)} AI stories* you can't miss"
        lines = "\n".join(f"{i}. {s['caption_line']}" for i, s in enumerate(stories, 1))
        caption = "\n\n".join(p for p in (raw.get("caption_hook", "").strip(), lines,
                                          raw.get("caption_cta", "").strip()) if p)
        return {
            "stories": stories,
            "cover_headline": cover,
            "cover_subline": raw.get("cover_subline", "").strip(),
            "cover_person": (raw.get("cover_person") or "").strip(),
            "cover_image_prompt": raw.get("cover_image_prompt", "").strip() or stories[0]["image_prompt"],
            "caption": caption,
            "biggest_url": biggest["url"] if biggest else None,
        }
    except CurationFailed:
        raise
    except Exception as e:
        raise CurationFailed(f"AI writing failed ({e})") from e
