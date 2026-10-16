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


def _call(method, path, version, **params):
    # Token goes in the body/params but never into error messages.
    params["access_token"] = os.environ["IG_ACCESS_TOKEN"].strip()
    url = f"{BASE}/{version}/{path}"
    if method == "GET":
        resp = requests.get(url, params=params, timeout=60)
    else:
        resp = requests.post(url, data=params, timeout=60)
    data = resp.json() if resp.content else {}
    if not resp.ok or "error" in data:
        err = data.get("error", {})
        raise InstagramError(f"{method} {path}: {err.get('message', resp.status_code)} (code {err.get('code')})")
    return data


def _wait_ready(container_id, version, timeout=300):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = _call("GET", container_id, version, fields="status_code").get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise InstagramError(f"Container {container_id} status {status}")
        time.sleep(4)
    raise InstagramError(f"Container {container_id} not ready after {timeout}s")


def publish_carousel(image_urls, caption, version):
    ig_user = os.environ["IG_USER_ID"].strip()
    children = []
    for url in image_urls:
        if url.lower().endswith(".mp4"):
            child = _call("POST", f"{ig_user}/media", version, media_type="VIDEO", video_url=url,
                          is_carousel_item="true")["id"]
        else:
            child = _call("POST", f"{ig_user}/media", version, image_url=url, is_carousel_item="true")["id"]
        children.append(child)
    for child in children:
        _wait_ready(child, version)
    carousel = _call("POST", f"{ig_user}/media", version,
                     media_type="CAROUSEL", children=",".join(children), caption=caption)["id"]
    _wait_ready(carousel, version)
    return _call("POST", f"{ig_user}/media_publish", version, creation_id=carousel)["id"]


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
