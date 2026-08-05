#!/usr/bin/env python3
"""Render GitHub profile stat cards as SVG files committed to this repository.

The profile README used to embed cards from the public github-readme-stats
instance. That service is shared and rate limited, and it answered 503 for
every card during three consecutive CI runs, so the images broke on the
profile page. Generating the SVGs here means the README only ever points at
files GitHub itself serves.

Two cards are produced, each in a light and a dark variant, so the README can
select between them with <picture> and prefers-color-scheme:

    assets/stats-light.svg   assets/stats-dark.svg
    assets/langs-light.svg   assets/langs-dark.svg

Usage:
    generate_stats.py --user rodolfoplondero [--out assets]
    generate_stats.py --user rodolfoplondero --fixture test.json   # no network
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

API = "https://api.github.com"

# Subset of GitHub's linguist colours; anything missing falls back to a
# deterministic colour derived from the language name.
LANGUAGE_COLOURS = {
    "Batchfile": "#C1F12E",
    "C": "#555555",
    "C#": "#178600",
    "CSS": "#563d7c",
    "Dart": "#00B4AB",
    "Dockerfile": "#384d54",
    "Go": "#00ADD8",
    "HTML": "#e34c26",
    "Java": "#b07219",
    "JavaScript": "#f1e05a",
    "Jupyter Notebook": "#DA5B0B",
    "Kotlin": "#A97BFF",
    "Lua": "#000080",
    "MATLAB": "#e16737",
    "Makefile": "#427819",
    "Objective-C": "#438eff",
    "PHP": "#4F5D95",
    "Perl": "#0298c3",
    "PowerShell": "#012456",
    "Python": "#3572A5",
    "R": "#198CE7",
    "Ruby": "#701516",
    "Rust": "#dea584",
    "SCSS": "#c6538c",
    "Shell": "#89e051",
    "Swift": "#F05138",
    "TeX": "#3D6117",
    "TypeScript": "#3178c6",
    "Visual Basic .NET": "#945db7",
    "Vue": "#41b883",
}

THEMES = {
    "light": {
        "bg": "#ffffff",
        "border": "#d0d7de",
        "title": "#0969da",
        "text": "#1f2328",
        "muted": "#656d76",
        "track": "#eaeef2",
    },
    "dark": {
        "bg": "#0d1117",
        "border": "#30363d",
        "title": "#58a6ff",
        "text": "#e6edf3",
        "muted": "#8b949e",
        "track": "#21262d",
    },
}

FONT = "-apple-system,BlinkMacSystemFont,Segoe UI,Ubuntu,Helvetica,Arial,sans-serif"


def language_colour(name: str) -> str:
    if name in LANGUAGE_COLOURS:
        return LANGUAGE_COLOURS[name]
    # Stable pseudo-random hue so unknown languages keep the same colour
    # between runs instead of flickering on every regeneration.
    hue = sum(ord(c) * (i + 1) for i, c in enumerate(name)) % 360
    return f"hsl({hue}, 55%, 50%)"


def api_get(path: str, token: str | None):
    url = path if path.startswith("http") else f"{API}{path}"
    request = urllib.request.Request(url)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", "profile-stats-generator")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def fetch_data(user: str, token: str | None) -> dict:
    profile = api_get(f"/users/{user}", token)

    repos: list[dict] = []
    page = 1
    while True:
        batch = api_get(f"/users/{user}/repos?per_page=100&type=owner&page={page}", token)
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    owned = [r for r in repos if not r.get("fork")]

    per_repo: list[dict[str, int]] = []
    for repo in owned:
        if repo.get("size", 0) == 0:
            continue
        try:
            breakdown = api_get(repo["languages_url"], token)
        except urllib.error.HTTPError as error:
            print(f"  ! languages for {repo['name']}: {error}", file=sys.stderr)
            continue
        if breakdown:
            per_repo.append(breakdown)

    return {
        "name": profile.get("name") or user,
        "public_repos": profile.get("public_repos", 0),
        "followers": profile.get("followers", 0),
        "stars": sum(r.get("stargazers_count", 0) for r in owned),
        "own_repos": len(owned),
        "per_repo_languages": per_repo,
    }


def aggregate_languages(data: dict, weight: str, exclude: set[str]) -> dict[str, float]:
    """Combine the per-repository language breakdowns into one distribution.

    Counting raw bytes lets a single repository decide the whole card: Jupyter
    notebooks embed their output and generated HTML runs to megabytes, so those
    two alone drowned out every language actually written by hand. Under the
    default 'repo' weighting each repository contributes the same total, so the
    card reflects what gets worked in rather than what serialises large.
    """
    per_repo = data.get("per_repo_languages")
    if per_repo is None:  # fixture written against the older shape
        return {k: float(v) for k, v in data.get("languages", {}).items() if k not in exclude}

    totals: dict[str, float] = {}
    for breakdown in per_repo:
        filtered = {k: v for k, v in breakdown.items() if k not in exclude}
        total = sum(filtered.values())
        if not total:
            continue
        for name, count in filtered.items():
            share = count / total if weight == "repo" else count
            totals[name] = totals.get(name, 0.0) + share
    return totals


def card(width: int, height: int, title: str, body: str, theme: dict) -> str:
    return f"""<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" \
xmlns="http://www.w3.org/2000/svg" role="img">
  <rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="6"
        fill="{theme['bg']}" stroke="{theme['border']}"/>
  <text x="25" y="35" font-family="{FONT}" font-size="16" font-weight="600"
        fill="{theme['title']}">{escape(title)}</text>
{body}
</svg>
"""


def render_stats(data: dict, theme: dict) -> str:
    rows = [
        ("Public repositories", data["public_repos"]),
        ("Stars earned", data["stars"]),
        ("Followers", data["followers"]),
        ("Languages used", len(data["languages"])),
    ]
    lines = []
    y = 70
    for label, value in rows:
        lines.append(
            f'  <text x="25" y="{y}" font-family="{FONT}" font-size="14" '
            f'fill="{theme["muted"]}">{escape(label)}</text>'
        )
        lines.append(
            f'  <text x="395" y="{y}" font-family="{FONT}" font-size="14" '
            f'font-weight="600" text-anchor="end" fill="{theme["text"]}">{value}</text>'
        )
        y += 27
    title = f"{data['name']}'s GitHub stats"
    return card(420, 165, title, "\n".join(lines), theme)


def render_languages(data: dict, theme: dict, top: int = 6) -> str:
    languages = sorted(data["languages"].items(), key=lambda kv: kv[1], reverse=True)
    total = sum(count for _, count in languages)
    if not total:
        body = (
            f'  <text x="25" y="70" font-family="{FONT}" font-size="14" '
            f'fill="{theme["muted"]}">No language data available</text>'
        )
        return card(340, 165, "Most used languages", body, theme)

    shown = languages[:top]
    parts = []

    # Stacked bar, clipped to rounded corners so the ends stay smooth.
    parts.append('  <clipPath id="bar"><rect x="25" y="55" width="290" height="10" rx="5"/></clipPath>')
    parts.append(f'  <rect x="25" y="55" width="290" height="10" rx="5" fill="{theme["track"]}"/>')
    parts.append('  <g clip-path="url(#bar)">')
    offset = 25.0
    for name, count in shown:
        width = 290 * count / total
        parts.append(
            f'    <rect x="{offset:.2f}" y="55" width="{width:.2f}" height="10" '
            f'fill="{language_colour(name)}"/>'
        )
        offset += width
    parts.append("  </g>")

    # Two-column legend.
    for index, (name, count) in enumerate(shown):
        column, row = index % 2, index // 2
        x = 25 + column * 150
        y = 90 + row * 22
        share = 100 * count / total
        parts.append(f'  <circle cx="{x + 5}" cy="{y - 4}" r="5" fill="{language_colour(name)}"/>')
        parts.append(
            f'  <text x="{x + 16}" y="{y}" font-family="{FONT}" font-size="12" '
            f'fill="{theme["text"]}">{escape(name)} <tspan fill="{theme["muted"]}">'
            f"{share:.1f}%</tspan></text>"
        )

    return card(340, 165, "Most used languages", "\n".join(parts), theme)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True)
    parser.add_argument("--out", default="assets")
    parser.add_argument("--fixture", help="Read stats from a JSON file instead of the API")
    parser.add_argument(
        "--weight",
        choices=("repo", "bytes"),
        default="repo",
        help="'repo' gives every repository equal say (default); 'bytes' counts raw size",
    )
    parser.add_argument(
        "--exclude-language",
        action="append",
        default=[],
        metavar="NAME",
        help="Drop a language entirely; repeatable",
    )
    args = parser.parse_args()

    if args.fixture:
        data = json.loads(Path(args.fixture).read_text())
    else:
        data = fetch_data(args.user, os.environ.get("GITHUB_TOKEN"))

    data["languages"] = aggregate_languages(data, args.weight, set(args.exclude_language))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, theme in THEMES.items():
        (out / f"stats-{name}.svg").write_text(render_stats(data, theme))
        (out / f"langs-{name}.svg").write_text(render_languages(data, theme))

    print(
        f"{data['own_repos']} own repos, {data['stars']} stars, "
        f"{data['followers']} followers, {len(data['languages'])} languages"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
