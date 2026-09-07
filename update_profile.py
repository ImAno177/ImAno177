#!/usr/bin/env python3
"""Refresh the terminal profile's live GitHub values."""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
USERNAME = os.environ.get("GITHUB_USERNAME", "ImAno177")
API_ROOT = "https://api.github.com"


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


def days_in_month(year: int, month: int) -> int:
    first_of_next = dt.date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return (first_of_next - dt.timedelta(days=1)).day


def account_age(created_at: str) -> str:
    created = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
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
    values = {
        "uptime_data": account_age(user["created_at"]),
        "repo_data": user.get("public_repos", len(repositories)),
        "star_data": sum(repo.get("stargazers_count", 0) for repo in repositories),
        "commit_data": commit_count(),
        "follower_data": user.get("followers", 0),
        "updated_data": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%MZ"),
    }
    for filename in ("dark_mode.svg", "light_mode.svg"):
        update_svg(ROOT / filename, values)
    print(json.dumps(values, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
