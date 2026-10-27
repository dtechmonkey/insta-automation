"""Research real GitHub repositories for "N repos you should know" list posts.

Everything shown (names, links, stars, language, licence) comes from GitHub's API, and descriptions are
written from each repo's own README, so nothing can be invented.
"""
import base64
import os
from datetime import datetime, timedelta, timezone

import requests

API = "https://api.github.com"


def _headers():
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    return {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})}


def search(query, n=6, min_stars=1000, active_days=120):
    """Popular, recently active, openly licensed repos matching `query` (GitHub search syntax allowed)."""
    since = (datetime.now(timezone.utc) - timedelta(days=active_days)).strftime("%Y-%m-%d")
    q = f"{query} stars:>={min_stars} pushed:>={since} archived:false"
    r = requests.get(f"{API}/search/repositories", headers=_headers(), timeout=30,
                     params={"q": q, "sort": "stars", "order": "desc", "per_page": 30})
    r.raise_for_status()
    picked, owners = [], set()
    for repo in r.json().get("items", []):
        lic = (repo.get("license") or {}).get("spdx_id")
        if not lic or lic == "NOASSERTION" or not repo.get("description"):
            continue
        if repo["owner"]["login"] in owners:  # variety: one repo per owner
            continue
        owners.add(repo["owner"]["login"])
        picked.append(_entry(repo))
        if len(picked) == n:
            break
    return picked


def _entry(repo):
    return {
        "full_name": repo["full_name"], "name": repo["name"], "owner": repo["owner"]["login"],
        "url": repo["html_url"], "homepage": repo.get("homepage") or "", "description": repo["description"] or "",
        "stars": repo["stargazers_count"], "language": repo.get("language") or "",
        "license": (repo.get("license") or {}).get("spdx_id") or "", "pushed": repo["pushed_at"][:10],
        "card": f"https://opengraph.githubassets.com/1/{repo['full_name']}",  # GitHub's social preview card
    }


def get(full_name):
    """One hand-picked repo, same fields as search()."""
    r = requests.get(f"{API}/repos/{full_name}", headers=_headers(), timeout=30)
    r.raise_for_status()
    return _entry(r.json())


def readme(full_name, limit=2500):
    r = requests.get(f"{API}/repos/{full_name}/readme", headers=_headers(), timeout=30)
    if not r.ok:
        return ""
    text = base64.b64decode(r.json().get("content", "")).decode("utf-8", "ignore")
    return text[:limit]


def stars_label(n):
    return f"{n / 1000:.1f}K".replace(".0K", "K") if n >= 1000 else str(n)
