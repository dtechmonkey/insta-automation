"""Host images at public URLs on a `media` branch of the (public) GitHub repo.

Instagram's API downloads images from a URL, so each slide must be publicly reachable.
Uses the GitHub REST API with the GITHUB_TOKEN that Actions provides automatically.
"""
import base64
import os
import time
from datetime import datetime, timedelta, timezone

import requests

BRANCH = "media"
API = "https://api.github.com"
KEEP_DAYS = 30


def _session():
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
    })
    return s


def _ensure_branch(s, repo):
    if s.get(f"{API}/repos/{repo}/git/ref/heads/{BRANCH}").status_code == 200:
        return
    default = s.get(f"{API}/repos/{repo}").json()["default_branch"]
    sha = s.get(f"{API}/repos/{repo}/git/ref/heads/{default}").json()["object"]["sha"]
    s.post(f"{API}/repos/{repo}/git/refs", json={"ref": f"refs/heads/{BRANCH}", "sha": sha}).raise_for_status()


def _wait_public(url):
    for _ in range(10):
        r = requests.head(url, timeout=20)
        if r.status_code == 200:
            return
        time.sleep(3)
    raise RuntimeError(f"Image not publicly reachable: {url} (is the repo public?)")


def upload(paths, folder):
    repo = os.environ["GITHUB_REPOSITORY"]
    s = _session()
    _ensure_branch(s, repo)
    urls = []
    for path in paths:
        dest = f"{folder}/{path.name}"
        resp = s.put(f"{API}/repos/{repo}/contents/{dest}", json={
            "message": f"media: {dest}",
            "content": base64.b64encode(path.read_bytes()).decode(),
            "branch": BRANCH,
        })
        resp.raise_for_status()
        urls.append(f"https://raw.githubusercontent.com/{repo}/{BRANCH}/{dest}")
    for url in urls:
        _wait_public(url)
    return urls


def cleanup(channel):
    """Delete media folders older than KEEP_DAYS (folder names start with YYYY-MM-DD)."""
    repo = os.environ["GITHUB_REPOSITORY"]
    s = _session()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    resp = s.get(f"{API}/repos/{repo}/contents/{channel}", params={"ref": BRANCH})
    if resp.status_code != 200:
        return
    for d in resp.json():
        if d["type"] != "dir" or d["name"][:10] >= cutoff:
            continue
        for f in s.get(d["url"]).json():
            s.delete(f"{API}/repos/{repo}/contents/{f['path']}",
                     json={"message": f"cleanup {f['path']}", "sha": f["sha"], "branch": BRANCH})
