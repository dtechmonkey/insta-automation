"""Real, freely licensed portraits of public figures for slide collages.

1. Wikipedia: the person's main article image (pageimages API).
2. Wikimedia Commons: confirm the file lives on Commons under a free licence (CC BY / CC BY-SA / CC0 /
   public domain) and read the photographer + licence for the caption credit. Images that exist only on
   Wikipedia (non-free "fair use" uploads) are skipped.
3. rembg: remove the background, crop to the person, save a transparent PNG.
"""
import html
import io
import re
from pathlib import Path

import requests

HEADERS = {"User-Agent": "TechMonkeyBot/1.0 (https://github.com/dtechmonkey/insta-automation)"}
FREE_LICENCE = re.compile(r"^(cc[ -]?by|cc[ -]?zero|cc0|public domain|pd)", re.I)
_session = None


def _wiki_image(name):
    r = requests.get("https://en.wikipedia.org/w/api.php", headers=HEADERS, timeout=30, params={
        "action": "query", "titles": name, "prop": "pageimages", "piprop": "name", "redirects": 1, "format": "json"})
    r.raise_for_status()
    pages = r.json().get("query", {}).get("pages", {})
    return next((p.get("pageimage") for p in pages.values() if p.get("pageimage")), None)


def _commons_info(filename):
    r = requests.get("https://commons.wikimedia.org/w/api.php", headers=HEADERS, timeout=30, params={
        "action": "query", "titles": f"File:{filename}", "prop": "imageinfo",
        "iiprop": "url|extmetadata", "iiurlwidth": 1200, "format": "json"})
    r.raise_for_status()
    page = next(iter(r.json().get("query", {}).get("pages", {}).values()), {})
    if "missing" in page or not page.get("imageinfo"):
        return None  # not on Commons (e.g. a Wikipedia-only fair-use image)
    info = page["imageinfo"][0]
    meta = info.get("extmetadata", {})
    licence = html.unescape(re.sub(r"<[^>]+>", "", meta.get("LicenseShortName", {}).get("value", ""))).strip()
    artist = html.unescape(re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", ""))).strip()
    if not FREE_LICENCE.match(licence):
        return None
    return {"url": info.get("thumburl") or info["url"], "licence": licence,
            "artist": re.sub(r"\s+", " ", artist)[:80] or "Unknown", "page": info.get("descriptionurl", "")}


def _cutout(image_bytes, out_png):
    global _session
    from PIL import Image
    from rembg import new_session, remove
    if _session is None:
        _session = new_session("u2net_human_seg")  # ~170 MB model, downloaded once and cached
    person = remove(Image.open(io.BytesIO(image_bytes)).convert("RGB"), session=_session)
    box = person.getbbox()
    if not box or (box[2] - box[0]) * (box[3] - box[1]) < 0.05 * person.width * person.height:
        return None  # nothing person-like found
    person.crop(box).save(out_png)
    return Path(out_png)


def portrait(name, out_png):
    """Save a transparent cut-out of `name` to out_png. Returns a credit dict, or None if no free photo."""
    if not name or len(name) > 60:
        return None
    try:
        filename = _wiki_image(name)
        info = _commons_info(filename) if filename else None
        if not info:
            return None
        img = requests.get(info["url"], headers=HEADERS, timeout=60)
        img.raise_for_status()
        if not _cutout(img.content, out_png):
            return None
    except Exception as e:  # network, model or image problems never block a post
        print(f"    ! portrait for {name}: {type(e).__name__}: {str(e)[:100]}")
        return None
    return {"name": name, "credit": f"{name}: {info['artist']} / {info['licence']} via Wikimedia Commons",
            "page": info["page"]}
