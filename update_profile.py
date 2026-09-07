#!/usr/bin/env python3
"""Refresh the terminal profile's live GitHub values."""

from __future__ import annotations

import datetime as dt
import hashlib
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
PROFILE_PATH = ROOT / "profile.json"
API_ROOT = "https://api.github.com"
CACHE_PATH = ROOT / "cache" / "profile_stats.json"
ALIGNMENT_COLUMNS = 60
STAT_SEPARATOR_MARKERS = (
    "repo_separator_dots",
    "commit_separator_dots",
    "loc_open_dots",
)
RULE_DOTS = ("header_dots", "contact_dots", "stats_dots")
RIGHT_ALIGNED_DOTS = (
    "os_data_dots",
    "uptime_data_dots",
    "host_data_dots",
    "kernel_data_dots",
    "ide_data_dots",
    "programming_data_dots",
    "computer_data_dots",
    "real_data_dots",
    "software_data_dots",
    "hardware_data_dots",
    "email_data_dots",
    "discord_data_dots",
    "portfolio_data_dots",
    "github_data_dots",
    "star_data_dots",
    "follower_data_dots",
)


def load_profile() -> dict:
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    sections = {
        "terminal": ("os", "host", "kernel", "ide"),
        "contact": ("email", "discord", "portfolio"),
    }
    if not isinstance(profile, dict) or not isinstance(profile.get("username"), str) or not profile["username"]:
        raise ValueError("profile.json must define a non-empty username")
    if not isinstance(profile.get("birth_date"), str) or not profile["birth_date"]:
        raise ValueError("profile.json must define a non-empty birth_date")
    for section, keys in sections.items():
        values = profile.get(section)
        if not isinstance(values, dict) or any(not isinstance(values.get(key), str) for key in keys):
            raise ValueError(f"profile.json is missing valid {section} fields")
    nested_sections = {
        "languages": ("programming", "computer", "real"),
        "hobbies": ("software", "hardware"),
    }
    for section, keys in nested_sections.items():
        values = profile["terminal"].get(section)
        if not isinstance(values, dict) or any(
            not isinstance(values.get(key), str) for key in keys
        ):
            raise ValueError(f"profile.json is missing valid terminal.{section} fields")
    return profile


PROFILE = load_profile()
USERNAME = PROFILE["username"]
BIRTH_DATE = PROFILE["birth_date"]


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


def user_repositories(repo_type: str) -> list[dict]:
    repositories: list[dict] = []
    page = 1
    while True:
        batch = github_get(
            f"/users/{USERNAME}/repos",
            {"type": repo_type, "per_page": "100", "page": str(page)},
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
        newline="\n",
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


def profile_values(profile: dict) -> dict[str, str]:
    terminal = profile["terminal"]
    contact = profile["contact"]
    portfolio = contact["portfolio"].rstrip("/")
    return {
        "os_data": terminal["os"],
        "host_data": terminal["host"],
        "kernel_data": terminal["kernel"],
        "ide_data": terminal["ide"],
        "programming_data": terminal["languages"]["programming"],
        "computer_data": terminal["languages"]["computer"],
        "real_data": terminal["languages"]["real"],
        "software_data": terminal["hobbies"]["software"],
        "hardware_data": terminal["hobbies"]["hardware"],
        "email_data": contact["email"],
        "discord_data": contact["discord"],
        "portfolio_data": re.sub(r"^https?://", "", portfolio),
        "github_data": f"github.com/{profile['username']}",
    }


def dot_fill(length: int) -> str:
    if length <= 0:
        return ""
    if length == 1:
        return " "
    if length == 2:
        return ". "
    return " " + "." * (length - 2) + " "


def rule_fill(length: int) -> str:
    if length <= 0:
        return ""
    return " " + "-" * (length - 1)


def terminal_dots(value: object, width: int) -> str:
    return dot_fill(width - len(str(value)))


def marker_match(svg: str, dots_id: str) -> re.Match[str]:
    pattern = re.compile(
        rf'(?m)^(?P<prefix>.*?<tspan\b[^>]*\bid=["\']{re.escape(dots_id)}["\'][^>]*>)'
        rf'(?P<dots>.*?)(?P<close></tspan>)(?P<suffix>.*)$'
    )
    match = pattern.search(svg)
    if match is None:
        raise ValueError(f"Missing SVG marker: {dots_id}")
    return match


def plain_text(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value))


DOT_TSPAN_PATTERN = re.compile(
    r'<tspan\b[^>]*\bid=["\'][^"\']*_dots["\'][^>]*>.*?</tspan>',
    re.DOTALL,
)


def required_alignment_columns(svg: str) -> int:
    lengths = (
        len(plain_text(DOT_TSPAN_PATTERN.sub(" ", line)).strip())
        for line in svg.splitlines()
        if 'x="390"' in line
    )
    return max(ALIGNMENT_COLUMNS, *lengths)


def resize_svg(svg: str, alignment_columns: int) -> str:
    width = max(985, 985 + (alignment_columns - ALIGNMENT_COLUMNS) * 10)
    svg = re.sub(r'width="\d+px"', f'width="{width}px"', svg)
    return re.sub(
        r'viewBox="0 0 \d+ 530"',
        f'viewBox="0 0 {width} 530"',
        svg,
        count=1,
    )


def right_align_marker(
    svg: str, marker_id: str, fill, alignment_columns: int
) -> str:
    match = marker_match(svg, marker_id)
    prefix = plain_text(match.group("prefix")).lstrip()
    suffix = plain_text(match.group("suffix")).strip()
    padding = alignment_columns - len(prefix) - len(suffix)
    if padding < 0:
        raise ValueError(f"SVG field overflows alignment column: {marker_id}")
    return svg[: match.start()] + match.group("prefix") + fill(padding) + match.group("close") + match.group("suffix") + svg[match.end() :]


def align_rule(svg: str, rule_id: str, alignment_columns: int) -> str:
    match = marker_match(svg, rule_id)
    prefix = plain_text(match.group("prefix")).lstrip()
    suffix = plain_text(match.group("suffix")).strip()
    padding = alignment_columns - len(prefix) - len(suffix)
    if padding < 0:
        raise ValueError(f"SVG rule overflows alignment column: {rule_id}")
    return svg[: match.start()] + match.group("prefix") + rule_fill(padding) + match.group("close") + match.group("suffix") + svg[match.end() :]


def align_marker_to_column(svg: str, marker_id: str, column: int) -> str:
    prefix = plain_text(marker_match(svg, marker_id).group("prefix")).lstrip()
    padding = column - len(prefix)
    if padding < 0:
        raise ValueError(f"SVG marker overflows alignment column: {marker_id}")
    return replace_tspan_value(svg, marker_id, " " * padding)


def align_diff_group(
    svg: str, values: dict[str, object], open_column: int, alignment_columns: int
) -> str:
    close_column = alignment_columns - 1
    additions = f"{values['loc_add']}++"
    deletions = f"{values['loc_del']}--"
    midpoint = (open_column + close_column + 1) // 2
    minimum_comma = open_column + len(additions) + 2
    maximum_comma = close_column - len(deletions) - 2
    if minimum_comma > maximum_comma:
        raise ValueError("LOC diff values overflow the stats row")
    comma_column = min(max(midpoint, minimum_comma), maximum_comma)
    left_width = comma_column - open_column - 1
    right_width = close_column - comma_column - 1

    left_padding = left_width - len(additions)
    right_padding = right_width - len(deletions)
    spaces = {
        "loc_add_left_dots": (left_padding + 1) // 2,
        "loc_add_right_dots": left_padding // 2,
        "loc_del_left_dots": (right_padding + 1) // 2,
        "loc_close_dots": right_padding // 2,
    }
    for marker_id, length in spaces.items():
        svg = replace_tspan_value(svg, marker_id, " " * length)
    return svg


def align_stats(svg: str, values: dict[str, object], alignment_columns: int) -> str:
    svg = replace_tspan_value(
        svg, "repo_data_dots", terminal_dots(values["repo_data"], 6)
    )
    repo_prefix = plain_text(
        marker_match(svg, "repo_separator_dots").group("prefix")
    ).lstrip()
    separator_column = max(alignment_columns // 2 - 1, len(repo_prefix))

    for dots_id, value_id in (
        ("commit_data_dots", "commit_data"),
        ("loc_data_dots", "loc_data"),
    ):
        prefix = plain_text(marker_match(svg, dots_id).group("prefix")).lstrip()
        svg = replace_tspan_value(
            svg,
            dots_id,
            terminal_dots(values[value_id], separator_column - 1 - len(prefix)),
        )

    for marker_id in STAT_SEPARATOR_MARKERS:
        svg = align_marker_to_column(svg, marker_id, separator_column)
    return align_diff_group(svg, values, separator_column, alignment_columns)


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
    alignment_columns = required_alignment_columns(svg)
    for rule_id in RULE_DOTS:
        svg = align_rule(svg, rule_id, alignment_columns)
    svg = align_stats(svg, values, alignment_columns)
    for dots_id in RIGHT_ALIGNED_DOTS:
        svg = right_align_marker(svg, dots_id, dot_fill, alignment_columns)
    path.write_text(resize_svg(svg, alignment_columns), encoding="utf-8", newline="\n")


def update_readme(profile: dict) -> None:
    readme_path = ROOT / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    contact = profile["contact"]
    links = (
        f"[Email](mailto:{contact['email']}) · "
        f"[Discord](https://discord.com/users/{contact['discord']}) · "
        f"[Portfolio]({contact['portfolio']})"
    )
    pattern = re.compile(
        r"(?ms)(<!-- profile-contact:start -->\n).*?(\n<!-- profile-contact:end -->)"
    )
    updated, replacements = pattern.subn(
        lambda match: f"{match.group(1)}{links}{match.group(2)}",
        readme,
        count=1,
    )
    if replacements != 1:
        raise ValueError("README.md is missing the profile contact markers")

    raw_base = (
        f"https://raw.githubusercontent.com/{profile['username']}"
        f"/{profile['username']}/main"
    )
    for filename in ("dark_mode.svg", "light_mode.svg"):
        version = hashlib.sha256((ROOT / filename).read_bytes()).hexdigest()[:12]
        pattern = re.compile(
            rf"({re.escape(raw_base)}/{re.escape(filename)}\?v=)[^\"'\s)]+"
        )
        updated, replacements = pattern.subn(rf"\g<1>{version}", updated, count=1)
        if replacements != 1:
            raise ValueError(f"README.md is missing the {filename} image URL")
    readme_path.write_text(updated, encoding="utf-8", newline="\n")


def main() -> None:
    user = github_get(f"/users/{USERNAME}")
    repositories = user_repositories("all")
    owned_repositories = [
        repository
        for repository in repositories
        if repository.get("owner", {}).get("login", "").lower()
        == USERNAME.lower()
    ]
    additions, deletions = lines_of_code(repositories)
    uptime = account_age(BIRTH_DATE)
    repos = format_number(user.get("public_repos", len(owned_repositories)))
    contributed = format_number(len(repositories))
    stars = format_number(
        sum(repo.get("stargazers_count", 0) for repo in owned_repositories)
    )
    commits = format_number(commit_count())
    followers = format_number(user.get("followers", 0))
    loc = format_number(additions - deletions)
    loc_add = format_number(additions)
    loc_del = format_number(deletions)
    values = profile_values(PROFILE)
    values.update({
        "uptime_data": uptime,
        "repo_data": repos,
        "contrib_data": contributed,
        "star_data": stars,
        "commit_data": commits,
        "follower_data": followers,
        "loc_data": loc,
        "loc_add": loc_add,
        "loc_del": loc_del,
    })
    for filename in ("dark_mode.svg", "light_mode.svg"):
        update_svg(ROOT / filename, values)
    update_readme(PROFILE)
    print(json.dumps(values, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
