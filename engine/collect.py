"""Fetch recent news items from RSS feeds and Hacker News, then de-duplicate."""
import calendar
import html
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

import feedparser
import requests

USER_AGENT = "Mozilla/5.0 (compatible; insta-news-bot/1.0)"
MAX_ITEMS = 60  # cap what we send to the AI


def _clean(text, limit=600):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text[:limit]


def _norm(title):
    return re.sub(r"[^a-z0-9 ]", "", title.lower())


def _similar(a, b, threshold):
    return SequenceMatcher(None, a, b).ratio() >= threshold


def _entry_time(entry):
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc)
    return None


def fetch_rss(feed, since):
    try:
        resp = requests.get(feed["url"], headers={"User-Agent": USER_AGENT}, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  ! {feed['name']}: {e}")
        return []
    items = []
    for entry in feedparser.parse(resp.content).entries:
        published = _entry_time(entry)
        if not published or published < since or not entry.get("link"):
            continue
        items.append({
            "title": _clean(entry.get("title"), 200),
            "summary": _clean(entry.get("summary") or entry.get("description")),
            "url": entry["link"],
            "source": feed["name"],
            "published": published.isoformat(),
        })
    return items


def fetch_hackernews(cfg, since):
    params = {
        "tags": "story",
        "query": cfg.get("query", "AI"),
        "numericFilters": f"created_at_i>{int(since.timestamp())},points>{cfg.get('min_points', 150)}",
        "hitsPerPage": 30,
    }
    try:
        resp = requests.get("https://hn.algolia.com/api/v1/search", params=params, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  ! Hacker News: {e}")
        return []
    items = []
    for hit in resp.json().get("hits", []):
        items.append({
            "title": _clean(hit.get("title"), 200),
            "summary": f"Trending on Hacker News with {hit.get('points')} points and {hit.get('num_comments')} comments.",
            "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}",
            "source": "Hacker News",
            "published": hit.get("created_at"),
        })
    return items


def dedupe(items, seen_urls, seen_titles):
    """Drop already-posted stories; merge the same story reported by several outlets."""
    seen_titles = [_norm(t) for t in seen_titles]
    out = []
    for item in sorted(items, key=lambda i: i["published"] or "", reverse=True):
        if not item["title"] or item["url"] in seen_urls:
            continue
        norm = _norm(item["title"])
        if any(_similar(norm, old, 0.8) for old in seen_titles):
            continue
        dup = next((o for o in out if _similar(norm, _norm(o["title"]), 0.75)), None)
        if dup:
            dup["also_reported_by"].append(item["source"])
            continue
        item["also_reported_by"] = []
        out.append(item)
    # Stories covered by more outlets first, then newest.
    out.sort(key=lambda i: (len(i["also_reported_by"]), i["published"] or ""), reverse=True)
    return out[:MAX_ITEMS]


def collect(sources, since, seen_urls, seen_titles):
    items = []
    for feed in sources.get("rss", []):
        got = fetch_rss(feed, since)
        print(f"  {feed['name']}: {len(got)}")
        items += got
    if sources.get("hackernews"):
        got = fetch_hackernews(sources["hackernews"], since)
        print(f"  Hacker News: {len(got)}")
        items += got
    unique = dedupe(items, seen_urls, seen_titles)
    print(f"  -> {len(items)} items, {len(unique)} unique & new")
    for i, item in enumerate(unique):
        item["id"] = i
    return unique
