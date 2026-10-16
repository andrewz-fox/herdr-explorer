"""Filename fuzzy match and file-content search."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from herdr_explorer.core.project import MAX_TEXT_BYTES, iter_files, relative_display

MAX_MATCHES = 200
RG_GLOBS = (
    "!**/.git/**",
    "!**/build/**",
    "!**/.gradle/**",
    "!**/.idea/**",
    "!**/.cxx/**",
    "!**/captures/**",
    "!**/.externalNativeBuild/**",
    "!**/.kotlin/**",
    "!**/out/**",
    "!**/generated/**",
)


@dataclass(frozen=True)
class SearchHit:
    path: str
    line_number: int
    text: str


def fuzzy_score(query: str, candidate: str) -> int | None:
    needle = query.lower()
    haystack = candidate.lower()
    if not needle:
        return 1
    found_at = haystack.find(needle)
    if found_at >= 0:
        score = 120 - found_at
        name = haystack.rsplit("/", 1)[-1]
        if name.startswith(needle) or f"/{needle}" in haystack:
            score += 30
        return score
    score = 0
    start = 0
    previous = -2
    for character in needle:
        found = haystack.find(character, start)
        if found < 0:
            return None
        score += 2
        if found == previous + 1:
            score += 5
        if found == 0 or haystack[found - 1] in "/._-":
            score += 3
        previous = found
        start = found + 1
    return score - (len(haystack) // 25)


def fuzzy_match(query: str, paths: list[str], limit: int = 80) -> list[str]:
    ranked: list[tuple[int, str]] = []
    for path in paths:
        name = path.rsplit("/", 1)[-1]
        path_score = fuzzy_score(query, path)
        name_score = fuzzy_score(query, name)
        scores = [score for score in (path_score, name_score) if score is not None]
        if not scores:
            continue
        ranked.append((max(scores), path))
    ranked.sort(key=lambda item: (-item[0], len(item[1]), item[1].lower()))
    if not query:
        return [path for _score, path in ranked[:limit]]
    return [path for _score, path in ranked[:limit]]


def rg_command(root: Path, query: str, *, regex: bool, case_sensitive: bool) -> list[str]:
    command = ["rg", "--json", "--max-filesize", "1M"]
    for pattern in RG_GLOBS:
        command.extend(["--glob", pattern])
    if not regex:
        command.append("--fixed-strings")
    if not case_sensitive:
        command.append("--ignore-case")
    command.extend(["--", query, str(root)])
    return command


def parse_rg_json(stdout: str, root: Path) -> list[SearchHit]:
    hits: list[SearchHit] = []
    for line in stdout.splitlines():
        if len(hits) >= MAX_MATCHES:
            break
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("type") != "match":
            continue
        data = item.get("data")
        if not isinstance(data, dict):
            continue
        raw_path = data.get("path", {}).get("text")
        line_number = data.get("line_number")
        text = data.get("lines", {}).get("text", "")
        if not isinstance(raw_path, str) or not isinstance(line_number, int):
            continue
        path = Path(raw_path)
        if path.is_absolute():
            relative = relative_display(path, root)
        else:
            relative = path.as_posix()
        hits.append(SearchHit(relative, line_number, str(text).rstrip("\n")))
    return hits


def _python_search(
    root: Path,
    query: str,
    *,
    regex: bool,
    case_sensitive: bool,
) -> list[SearchHit]:
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(query if regex else re.escape(query), flags)
    except re.error as exc:
        raise ValueError(str(exc)) from exc
    hits: list[SearchHit] = []
    for path in iter_files(root):
        if len(hits) >= MAX_MATCHES:
            break
        try:
            if path.stat().st_size > MAX_TEXT_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                hits.append(SearchHit(relative_display(path, root), number, line))
                if len(hits) >= MAX_MATCHES:
                    break
    return hits


def search_paths(
    root: Path,
    query: str,
    *,
    regex: bool = False,
    case_sensitive: bool = False,
) -> list[SearchHit]:
    if not query:
        return []
    rg = shutil.which("rg")
    if rg:
        completed = subprocess.run(
            rg_command(root, query, regex=regex, case_sensitive=case_sensitive),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode in (0, 1):
            return parse_rg_json(completed.stdout, root)
        if completed.returncode == 2 and "regex" in completed.stderr.lower():
            raise ValueError(completed.stderr.strip())
    return _python_search(root, query, regex=regex, case_sensitive=case_sensitive)
