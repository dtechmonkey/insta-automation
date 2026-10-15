"""GitHub Issues as the daily heads-up: opened by the draft stage (GitHub emails the repo owner),
closed by the post stage. Needs GITHUB_TOKEN + GITHUB_REPOSITORY (set automatically in Actions)."""
import os

import requests

API = "https://api.github.com"


def _ctx():
    token, repo = os.environ.get("GITHUB_TOKEN", "").strip(), os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not (token and repo):
        return None
    return repo, {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def open_issue(title, body):
    ctx = _ctx()
    if not ctx:
        print("  (no GitHub token: heads-up issue not created)")
        return None
    repo, headers = ctx
    r = requests.post(f"{API}/repos/{repo}/issues", headers=headers, json={"title": title, "body": body}, timeout=30)
    r.raise_for_status()
    issue = r.json()
    print(f"  heads-up issue #{issue['number']}: {issue['html_url']}")
    return issue["number"]


def close_issue(number, comment):
    ctx = _ctx()
    if not (ctx and number):
        return
    repo, headers = ctx
    requests.post(f"{API}/repos/{repo}/issues/{number}/comments", headers=headers, json={"body": comment}, timeout=30)
    requests.patch(f"{API}/repos/{repo}/issues/{number}", headers=headers, json={"state": "closed"}, timeout=30)
