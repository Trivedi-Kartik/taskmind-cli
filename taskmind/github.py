"""Import GitHub issues as tasks via the free GitHub REST API (stdlib only)."""

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"


class GitHubError(Exception):
    pass


def token():
    """Use GITHUB_TOKEN / GH_TOKEN, or the GitHub CLI's login if installed. Optional for public repos."""
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(name):
            return os.environ[name]
    if shutil.which("gh"):
        result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return None


def _get(path, params=None, auth=None):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "taskmind"}
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as err:
        if err.code == 404:
            raise GitHubError("Repo not found (private repos need GITHUB_TOKEN or `gh auth login`).") from err
        if err.code == 403:
            raise GitHubError("GitHub rate limit hit. Set GITHUB_TOKEN for a higher free limit.") from err
        raise GitHubError(f"GitHub HTTP {err.code}") from err
    except urllib.error.URLError as err:
        raise GitHubError(f"Could not reach GitHub: {err.reason}") from err


def fetch_issues(owner_repo, state="open", labels=None, assignee=None, limit=50):
    if owner_repo.count("/") != 1:
        raise GitHubError("Use the form owner/repo")
    params = {"state": state, "per_page": min(limit, 100)}
    if labels:
        params["labels"] = labels
    if assignee:
        params["assignee"] = assignee
    items = _get(f"/repos/{owner_repo}/issues", params, token())
    # The issues endpoint also returns pull requests; skip them.
    return [i for i in items if "pull_request" not in i][:limit]


def issue_state(owner_repo, number):
    return _get(f"/repos/{owner_repo}/issues/{number}", auth=token()).get("state")
