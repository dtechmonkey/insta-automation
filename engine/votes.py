"""Vote counts and comment analysis for vote-loop story reels (the stories channel, Your Move).

  python -m engine.votes --channel stories             # update one GitHub issue per recent part
  python -m engine.votes --channel stories --dry-run   # print the report instead

Each bank entry lists its choices as `vote: [BAITHO, BHAAGO]` and may add `vote_aliases` (Hindi spellings,
English words). Choices are also numbered in that order, matching the end card's "Comment: 1 ya 2", so list
them in the card's order. A comment counts as a vote when it names exactly one choice. Each person gets one vote:
their latest comment that names a choice. The other comments go to Gemini for a short summary of themes.
Reports carry counts and paraphrased themes only, never usernames or quoted comments: the repo is public.
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

from engine import instagram, notify

ROOT = Path(__file__).resolve().parent.parent
REPORT_DAYS = 7          # keep reporting a part for this long after it posts
ANALYSE_MAX = 300        # comments sent to the AI per part (the newest)


# ---------- counting ----------

def _squash(word):
    """'Bhaaaago' -> 'bhago', 'baithooo' -> 'baitho': repeated letters collapse, so stretched votes match."""
    return re.sub(r"(.)\1+", r"\1", word.lower())


def _words(text):
    text = unicodedata.normalize("NFKC", text or "").lower()
    return re.findall(r"[a-z0-9]+|[ऀ-ॿ]+", text)


# The end cards say "Comment: 1 ya 2": choice N in `vote` is also number N. Digits count in a short comment
# ("2", "2 bhai", "option 1"); number words only as the whole comment, since "jaldi do" means "give it".
DIGITS = {1: {"1", "१"}, 2: {"2", "२"}, 3: {"3", "३"}, 4: {"4", "४"}}
NUMBER_WORDS = {1: {"one", "ek", "pehla", "एक"}, 2: {"two", "do", "dusra", "doosra", "दो"},
                3: {"three", "teen", "तीन"}, 4: {"four", "char", "chaar", "चार"}}
NOT_A_VOTE = {"part", "episode", "ep", "season", "baje", "bje", "pm", "am"}


def matcher(choices, aliases=None):
    """A function: comment text -> the one choice it names, 'BOTH' if several, else None."""
    forms = {}
    for choice in choices:
        keys = {_squash(choice)} | {_squash(a) for a in (aliases or {}).get(choice, [])}
        forms[choice] = keys

    def which(text):
        hits = set()
        words = _words(text)
        numbers_ok = (len(words) <= 3 or ("vote" in words and len(words) <= 6)) and not NOT_A_VOTE & set(words)
        for w in words:
            sw = _squash(w)
            for n, choice in enumerate(choices, 1):
                if numbers_ok and (w in DIGITS.get(n, ()) or (len(words) == 1 and w in NUMBER_WORDS.get(n, ()))):
                    hits.add(choice)
            for choice, keys in forms.items():
                # whole word, or a clear stem of a Latin-script choice ("bhag", "baith")
                if sw in keys or (len(sw) >= 4 and sw.isascii() and any(k.startswith(sw) for k in keys if k.isascii())):
                    hits.add(choice)
        if len(hits) > 1:
            return "BOTH"
        return next(iter(hits), None)
    return which


def tally(comments, choices, aliases=None, me=""):
    """Votes per choice (one per person, latest wins), plus the comments that are not plain votes."""
    which = matcher(choices, aliases)
    latest, both, other, raw = {}, 0, [], Counter()
    me = me.lstrip("@").lower()
    for c in sorted(comments, key=lambda c: c.get("timestamp", "")):
        user = (c.get("username") or c.get("id")).lower()
        if user == me:
            continue
        pick = which(c.get("text", ""))
        if pick == "BOTH":
            both += 1
        elif pick:
            raw[pick] += 1
            latest[user] = pick
        words = len(_words(c.get("text", "")))
        if not pick or words > 3:  # a vote with something to say is worth reading too
            other.append(c)
    votes = Counter(latest.values())
    return {"votes": {ch: votes.get(ch, 0) for ch in choices}, "voters": len(latest),
            "vote_comments": sum(raw.values()), "both": both,
            "comments": sum(1 for c in comments if (c.get("username") or "").lower() != me), "other": other}


# ---------- analysis ----------

PROMPT = """You read Instagram comments on Part {part} of "{series}", a Hinglish vote-loop horror story reel.
Viewers vote for what happens next with one word: {choices}. The vote counts are already done; your job is
everything else people said.

Reel caption:
{caption}

Comments (one per line, newest last, usernames removed):
{comments}

Return only JSON in this shape:
{{"summary": "2-3 sentences: overall mood and what people care about",
  "sentiment": {{"positive": 0, "neutral": 0, "negative": 0}},
  "themes": [{{"theme": "short name", "comments": 0, "gist": "what they say, paraphrased"}}],
  "theories": ["fan theories about the mystery, paraphrased"],
  "questions": ["questions viewers ask, paraphrased"],
  "part_ideas": ["what viewers want to see next, paraphrased"],
  "problems": ["complaints: audio, pacing, too short, confusing, etc."]}}
Rules: sentiment numbers are comment counts. At most 6 themes, 5 items per list, empty lists allowed.
Paraphrase in simple English; never copy usernames or whole comments. Ignore spam and emoji-only comments."""


def analyse(entry, other, ai_cfg, series):
    from engine.curate import _ask_gemini
    texts = [" ".join((c.get("text") or "").split()) for c in other][-ANALYSE_MAX:]
    texts = [t for t in texts if t]
    if not texts:
        return None
    numbered = " / ".join(f"{n} = {c}" for n, c in enumerate(entry["vote"], 1))
    prompt = PROMPT.format(part=entry["id"], series=series, choices=f"{numbered} (by word or number)",
                           caption=entry.get("caption", "").strip(), comments="\n".join(texts))
    return _ask_gemini(prompt, ai_cfg, retry_minutes=3)


# ---------- report ----------

def _bar(n, total, width=20):
    filled = round(width * n / total) if total else 0
    return "█" * filled + "░" * (width - filled)


def render(entry, posted, t, ai, now, permalink=None, analysed_at=None):
    total = sum(t["votes"].values())
    lines = [f"**{entry['id']}**, posted {posted:%d %b %H:%M} IST" + (f" · [open the reel]({permalink})" if permalink else ""),
             f"Updated {now:%d %b %H:%M} IST. Counts update every hour.", "", "## Votes", "",
             "| Choice | Votes | Share | |", "|---|---:|---:|---|"]
    for choice, n in sorted(t["votes"].items(), key=lambda kv: -kv[1]):
        share = f"{100 * n / total:.0f}%" if total else "–"
        lines.append(f"| **{choice}** | {n} | {share} | `{_bar(n, total)}` |")
    ranked = sorted(t["votes"].items(), key=lambda kv: -kv[1])
    if total and ranked[0][1] == ranked[1][1]:
        lead = "**Tied.**"
    elif total:
        lead = f"**{ranked[0][0]} leads** by {ranked[0][1] - ranked[1][1]}."
    else:
        lead = "No votes yet."
    lines += ["", lead,
              f"{t['voters']} people voted (one vote each; a changed vote counts once). "
              f"{t['comments']} comments in all, {t['both']} named both choices and were not counted.", ""]
    if ai:
        s = ai.get("sentiment", {})
        lines += ["## What people are saying", "", ai.get("summary", ""), "",
                  f"Mood: {s.get('positive', 0)} positive · {s.get('neutral', 0)} neutral · {s.get('negative', 0)} negative", ""]
        if ai.get("themes"):
            lines += ["| Theme | Comments | Gist |", "|---|---:|---|"]
            lines += [f"| {x.get('theme', '')} | {x.get('comments', '')} | {x.get('gist', '')} |" for x in ai["themes"]]
            lines.append("")
        for key, head in (("theories", "Fan theories"), ("questions", "Questions"),
                          ("part_ideas", "What they want next"), ("problems", "Complaints")):
            if ai.get(key):
                lines += [f"**{head}**", *[f"- {x}" for x in ai[key]], ""]
        if analysed_at:
            lines.append(f"_Summary from {analysed_at}; it is redone when new non-vote comments arrive._")
    elif t["other"]:
        lines.append("_Comment summary unavailable this run; it retries next hour._")
    return "\n".join(lines)


def _saved_analysis(body):
    m = re.search(r"<!-- analysis (\{.*?\}) -->", body or "", re.S)
    return json.loads(m.group(1)) if m else None


def run(channel, dry_run=False):
    channel_dir = ROOT / "channels" / channel
    cfg = yaml.safe_load((channel_dir / "config.yaml").read_text(encoding="utf-8"))
    tz = ZoneInfo(cfg.get("timezone", "UTC"))
    version = cfg["instagram"]["api_version"]
    bank = {e["id"]: e for e in yaml.safe_load((channel_dir / "reels" / "bank.yaml").read_text(encoding="utf-8"))}
    hist = json.loads((channel_dir / "history.json").read_text(encoding="utf-8")) if (channel_dir / "history.json").exists() else {}
    now = datetime.now(tz)
    for posted_entry in hist.get("reels", []):
        entry = bank.get(posted_entry["id"])
        posted = datetime.fromisoformat(posted_entry["posted_at"]).astimezone(tz)
        if not entry or not entry.get("vote") or now - posted > timedelta(days=REPORT_DAYS):
            continue
        comments = instagram.all_comments(posted_entry["media_id"], version)
        t = tally(comments, entry["vote"], entry.get("vote_aliases"), cfg.get("handle", ""))
        title = f"{cfg['name']}: votes for {entry['id']}"
        saved = None if dry_run else _saved_analysis(notify.issue_body(title))
        ai, analysed_at = (saved or {}).get("ai"), (saved or {}).get("at")
        if t["other"] and (not saved or saved.get("n") != len(t["other"])):
            try:
                ai = analyse(entry, t["other"], cfg["ai"], cfg["name"])
                analysed_at = f"{now:%d %b %H:%M} IST"
            except Exception as e:  # counts still go out; the summary retries next run
                print(f"  ! comment summary failed: {e}")
        try:
            permalink = instagram._call("GET", posted_entry["media_id"], version, fields="permalink").get("permalink")
        except instagram.InstagramError:
            permalink = None
        body = render(entry, posted, t, ai, now, permalink, analysed_at)
        if ai:
            body += f"\n\n<!-- analysis {json.dumps({'n': len(t['other']), 'at': analysed_at, 'ai': ai}, ensure_ascii=False)} -->"
        print(f"{entry['id']}: {t['votes']} from {t['voters']} voters, {t['comments']} comments")
        if dry_run:
            print(body.split("<!-- analysis")[0])
        else:
            print(f"  report: {notify.upsert_issue(title, body)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--dry-run", action="store_true", help="print the report, update nothing")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(ROOT / ".env")
    for name in ("IG_ACCESS_TOKEN", "IG_USER_ID"):
        if os.environ.get(f"{name}_{args.channel.upper()}"):
            os.environ[name] = os.environ[f"{name}_{args.channel.upper()}"]
    run(args.channel, args.dry_run)


if __name__ == "__main__":
    main()
