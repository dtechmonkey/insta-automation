"""Per-channel record of what was posted: drives de-duplication and the weekly recap."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def load(channel_dir):
    path = Path(channel_dir) / "history.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"posts": []}


def save(channel_dir, history):
    path = Path(channel_dir) / "history.json"
    path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")


def _recent(history, days, mode=None):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return [p for p in history["posts"]
            if datetime.fromisoformat(p["posted_at"]) >= cutoff and (mode is None or p["mode"] == mode)]


def seen(history, days=14):
    stories = [s for p in _recent(history, days, "daily") for s in p["stories"]]
    titles = [s["headline"] for s in stories] + [s["source_title"] for s in stories if s.get("source_title")]
    return {s["url"] for s in stories}, titles


def recently_posted(history, days=5):
    """Short descriptions of recently posted stories, so the AI can avoid repeating them."""
    stories = [s for p in _recent(history, days) for s in p["stories"]]
    return [f"{s['headline'].replace('*', '')}: {s.get('facts', '')[:140]}" for s in stories]


def already_posted(history, mode, local_date):
    return any(p["mode"] == mode and p["date"] == local_date for p in history["posts"])


def week_pool(history, days=7):
    """Stories from this week's daily posts, shaped like collected items for the curator."""
    pool = []
    for p in _recent(history, days, "daily"):
        for s in p["stories"]:
            pool.append({"id": len(pool), "title": s["headline"], "summary": s.get("facts", ""),
                         "source": s["source"], "url": s["url"], "published": s.get("published"),
                         "also_reported_by": []})
    return pool


def record(history, mode, local_date, stories, media_id):
    history["posts"].append({
        "mode": mode,
        "date": local_date,
        "posted_at": datetime.now(timezone.utc).isoformat(),
        "media_id": media_id,
        "stories": stories,
    })
    history["posts"] = _recent(history, 60)  # keep the file small
