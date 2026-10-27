"""Publish carousels, Reels and Stories with the official Instagram API (Instagram Login flavour).

CLI helpers:
  python -m engine.instagram whoami     # check the token; prints username + IG user id
  python -m engine.instagram refresh    # prints a refreshed long-lived token to stdout
"""
import os
import sys
import time

import requests

BASE = "https://graph.instagram.com"


class InstagramError(RuntimeError):
    pass


# Meta rate-limit error codes (app-level, user-level, business-use-case, too-many-calls).
RATE_LIMIT_CODES = {4, 17, 32, 613}
RATE_LIMIT_WAITS = (300, 600, 900)  # seconds; the limits are rolling-hour windows


def _call(method, path, version, **params):
    # Token goes in the body/params but never into error messages.
    params["access_token"] = os.environ["IG_ACCESS_TOKEN"].strip()
    url = f"{BASE}/{version}/{path}"
    for attempt in range(len(RATE_LIMIT_WAITS) + 1):
        if method == "GET":
            resp = requests.get(url, params=params, timeout=60)
        else:
            resp = requests.post(url, data=params, timeout=60)
        data = resp.json() if resp.content else {}
        if resp.ok and "error" not in data:
            return data
        err = data.get("error", {})
        if err.get("code") in RATE_LIMIT_CODES and attempt < len(RATE_LIMIT_WAITS):
            wait = RATE_LIMIT_WAITS[attempt]
            print(f"  Instagram rate limit (code {err.get('code')}); waiting {wait // 60} min before retrying {path}")
            time.sleep(wait)
            continue
        raise InstagramError(f"{method} {path}: {err.get('message', resp.status_code)} (code {err.get('code')})")


def _wait_ready(container_id, version, timeout=300):
    """Poll a container until FINISHED, backing off (5 s -> 30 s) to stay well inside API rate limits."""
    deadline, interval = time.time() + timeout, 5.0
    while time.time() < deadline:
        time.sleep(interval)
        status = _call("GET", container_id, version, fields="status_code").get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise InstagramError(f"Container {container_id} status {status}")
        interval = min(30.0, interval * 1.5)
    raise InstagramError(f"Container {container_id} not ready after {timeout}s")


def publish_carousel(image_urls, caption, version):
    ig_user = os.environ["IG_USER_ID"].strip()
    children, videos = [], []
    for url in image_urls:
        if url.lower().endswith(".mp4"):
            child = _call("POST", f"{ig_user}/media", version, media_type="VIDEO", video_url=url,
                          is_carousel_item="true")["id"]
            videos.append(child)
        else:
            child = _call("POST", f"{ig_user}/media", version, image_url=url, is_carousel_item="true")["id"]
        children.append(child)
    for child in videos:  # images are ready immediately; only videos need processing time
        _wait_ready(child, version)
    carousel = _call("POST", f"{ig_user}/media", version,
                     media_type="CAROUSEL", children=",".join(children), caption=caption)["id"]
    _wait_ready(carousel, version)
    try:
        return _call("POST", f"{ig_user}/media_publish", version, creation_id=carousel)["id"]
    except InstagramError as e:  # e.g. "Fatal (code -1)" although the post went live
        time.sleep(20)
        posted = find_posted(caption, version)
        if posted:
            print(f"  media_publish reported '{e}', but the post is live")
            return posted
        raise


def publish_reel(video_url, caption, version, share_to_feed=False):
    """A Reel (9:16 video). share_to_feed=False keeps it in the Reels tab only (no duplicate grid tile)."""
    ig_user = os.environ["IG_USER_ID"].strip()
    container = _call("POST", f"{ig_user}/media", version, media_type="REELS", video_url=video_url,
                      caption=caption, share_to_feed="true" if share_to_feed else "false",
                      thumb_offset="0")["id"]  # first frame = cover with full text
    _wait_ready(container, version, timeout=600)
    return _call("POST", f"{ig_user}/media_publish", version, creation_id=container)["id"]


def publish_story(media_url, version):
    """A Story (disappears after 24 h). The API can't add the tappable 'shared post' sticker."""
    ig_user = os.environ["IG_USER_ID"].strip()
    kind = {"video_url": media_url} if media_url.lower().endswith(".mp4") else {"image_url": media_url}
    container = _call("POST", f"{ig_user}/media", version, media_type="STORIES", **kind)["id"]
    _wait_ready(container, version)
    return _call("POST", f"{ig_user}/media_publish", version, creation_id=container)["id"]


def recent_media(version, limit=10):
    """Our latest posts (id, caption, timestamp), newest first."""
    ig_user = os.environ["IG_USER_ID"].strip()
    return _call("GET", f"{ig_user}/media", version, fields="id,caption,timestamp", limit=limit).get("data", [])


def find_posted(caption, version, limit=10):
    """Media id of a recent post with this caption, or None. Instagram sometimes publishes even when
    media_publish answers with an error (e.g. 'Application request limit reached'), so check before retrying."""
    key = " ".join(caption.split())[:120]
    for m in recent_media(version, limit):
        if " ".join((m.get("caption") or "").split())[:120] == key:
            return m["id"]
    return None


def comments(media_id, version):
    """Top-level comments on one of our posts (needs instagram_business_manage_comments)."""
    data = _call("GET", f"{media_id}/comments", version, fields="id,text,username,timestamp", limit=50)
    return data.get("data", [])


def reply_to_comment(comment_id, message, version):
    return _call("POST", f"{comment_id}/replies", version, message=message)["id"]


def refresh_token():
    resp = requests.get(f"{BASE}/refresh_access_token", timeout=60, params={
        "grant_type": "ig_refresh_token", "access_token": os.environ["IG_ACCESS_TOKEN"].strip()})
    data = resp.json()
    if "access_token" not in data:
        raise InstagramError(f"Token refresh failed: {data.get('error', {}).get('message', resp.status_code)}")
    return data["access_token"]


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "whoami"
    if cmd == "refresh":
        sys.stdout.write(refresh_token())
    else:
        me = _call("GET", "me", "v23.0", fields="user_id,username,account_type")
        print(f"username: {me.get('username')}\nIG_USER_ID: {me.get('user_id')}\naccount type: {me.get('account_type')}")
