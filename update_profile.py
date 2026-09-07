#!/usr/bin/env python3
"""Refresh the terminal profile's live GitHub values."""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
USERNAME = os.environ.get("GITHUB_USERNAME", "ImAno177")
BIRTH_DATE = os.environ.get("PROFILE_BIRTH_DATE", "2005-07-17")
API_ROOT = "https://api.github.com"
CACHE_PATH = ROOT / "cache" / "profile_stats.json"


def github_get(path: str, params: dict[str, str] | None = None):
    query = f"?{urlencode(params)}" if params else ""
    request = Request(
        f"{API_ROOT}{path}{query}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{USERNAME}-profile-updater",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def owned_repositories() -> list[dict]:
    repositories: list[dict] = []
    page = 1
    while True:
        batch = github_get(
            f"/users/{USERNAME}/repos",
            {"type": "owner", "per_page": "100", "page": str(page)},
        )
        repositories.extend(batch)
        if len(batch) < 100:
            return repositories
        page += 1


def commit_count() -> int | str:
    try:
        result = github_get(
            "/search/commits",
            {"q": f"author:{USERNAME}", "per_page": "1"},
        )
        return int(result["total_count"])
    except (HTTPError, KeyError, TypeError, ValueError):
        return "n/a"


def contributor_stats(repository: dict) -> tuple[list[dict], bool]:
    """Return contributor stats and whether GitHub returned a complete result."""
    for attempt in range(4):
        try:
            result = github_get(f"/repos/{repository['full_name']}/stats/contributors")
            if isinstance(result, list):
                return result, True
        except (HTTPError, ValueError, TypeError):
            pass
        if attempt < 3:
            time.sleep(2)
    return [], False


def repository_loc(repository: dict) -> tuple[int, int, bool]:
    additions = 0
    deletions = 0
    stats, complete = contributor_stats(repository)
    if not complete:
        return additions, deletions, False
    for contributor in stats:
        author = contributor.get("author") or {}
        if author.get("login", "").lower() != USERNAME.lower():
            continue
        for week in contributor.get("weeks", []):
            additions += int(week.get("a", 0))
            deletions += int(week.get("d", 0))
    return additions, deletions, True


def read_loc_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def lines_of_code(repositories: list[dict]) -> tuple[int, int]:
    """Use a small persistent cache so daily runs only inspect changed repos."""
    old_cache = read_loc_cache().get("repositories", {})
    entries: dict[str, dict] = {}
    pending: list[dict] = []

    for repository in repositories:
        key = repository["full_name"]
        pushed_at = repository.get("pushed_at") or repository.get("updated_at")
        cached = old_cache.get(key, {})
        if cached.get("pushed_at") == pushed_at and cached.get("complete"):
            entries[key] = cached
        else:
            pending.append(repository)

    with ThreadPoolExecutor(max_workers=min(6, len(pending)) or 1) as pool:
        futures = {
            pool.submit(repository_loc, repository): repository for repository in pending
        }
        for future, repository in futures.items():
            key = repository["full_name"]
            pushed_at = repository.get("pushed_at") or repository.get("updated_at")
            additions, deletions, complete = future.result()
            if not complete and key in old_cache:
                entries[key] = old_cache[key]
            else:
                entries[key] = {
                    "additions": additions,
                    "deletions": deletions,
                    "pushed_at": pushed_at,
                    "complete": complete,
                }

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps({"repositories": entries}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    additions = sum(int(entry.get("additions", 0)) for entry in entries.values())
    deletions = sum(int(entry.get("deletions", 0)) for entry in entries.values())
    return additions, deletions


def days_in_month(year: int, month: int) -> int:
    first_of_next = dt.date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return (first_of_next - dt.timedelta(days=1)).day


def account_age(start_date: str) -> str:
    created = dt.datetime.fromisoformat(start_date.replace("Z", "+00:00"))
    today = dt.datetime.now(dt.timezone.utc)
    years = today.year - created.year
    months = today.month - created.month
    days = today.day - created.day

    if days < 0:
        months -= 1
        previous_month = today.month - 1 or 12
        previous_year = today.year if today.month > 1 else today.year - 1
        days += days_in_month(previous_year, previous_month)
    if months < 0:
        years -= 1
        months += 12

    def plural(value: int, unit: str) -> str:
        return f"{value} {unit}{'' if value == 1 else 's'}"

    return ", ".join(
        [plural(years, "year"), plural(months, "month"), plural(days, "day")]
    )


def format_number(value: int | str) -> int | str:
    return f"{value:,}" if isinstance(value, int) else value


def replace_tspan_value(svg: str, element_id: str, value: object) -> str:
    pattern = re.compile(
        rf'(<tspan\b[^>]*\bid=["\']{re.escape(element_id)}["\'][^>]*>).*?(</tspan>)',
        re.DOTALL,
    )
    updated, replacements = pattern.subn(
        lambda match: f"{match.group(1)}{html.escape(str(value))}{match.group(2)}",
        svg,
        count=1,
    )
    if replacements != 1:
        raise ValueError(f"Missing SVG marker: {element_id}")
    return updated


def update_svg(path: Path, values: dict[str, object]) -> None:
    svg = path.read_text(encoding="utf-8")
    for element_id, value in values.items():
        svg = replace_tspan_value(svg, element_id, value)
    path.write_text(svg, encoding="utf-8", newline="\n")


def main() -> None:
    user = github_get(f"/users/{USERNAME}")
    repositories = owned_repositories()
    additions, deletions = lines_of_code(repositories)
    values = {
        "uptime_data": account_age(BIRTH_DATE),
        "repo_data": format_number(user.get("public_repos", len(repositories))),
        "star_data": format_number(sum(repo.get("stargazers_count", 0) for repo in repositories)),
        "commit_data": format_number(commit_count()),
        "follower_data": format_number(user.get("followers", 0)),
        "loc_data": format_number(additions - deletions),
        "loc_add": format_number(additions),
        "loc_del": format_number(deletions),
    }
    for filename in ("dark_mode.svg", "light_mode.svg"):
        update_svg(ROOT / filename, values)
    print(json.dumps(values, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
