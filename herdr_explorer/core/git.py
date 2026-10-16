"""Read-only Git status and unified diff parsing.

The wrapper refuses every Git command except `status` and `diff`.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

ALLOWED_COMMANDS = frozenset({"status", "diff"})
UNMERGED = frozenset({"DD", "AU", "UD", "UA", "DU", "AA", "UU"})
GROUP_ORDER = ("conflict", "staged", "unstaged", "untracked")
GROUP_LABELS = {
    "conflict": "Merge conflicts",
    "staged": "Staged",
    "unstaged": "Unstaged",
    "untracked": "Untracked",
}


class GitCommandError(RuntimeError):
    """Raised when a Git command is refused or fails."""


@dataclass(frozen=True)
class GitChange:
    group: str
    status: str
    path: str
    original_path: str | None = None

    @property
    def key(self) -> str:
        return f"{self.group}:{self.path}"


@dataclass(frozen=True)
class DiffRow:
    kind: str
    left_no: int | None
    right_no: int | None
    left: str
    right: str


@dataclass(frozen=True)
class ParsedDiff:
    rows: tuple[DiffRow, ...]
    hunk_rows: tuple[int, ...]
    text: str


def changed_line_numbers(parsed: ParsedDiff) -> frozenset[int]:
    """1-based line numbers in the updated file that were added or edited."""

    return frozenset(
        row.right_no
        for row in parsed.rows
        if row.kind in {"add", "change"} and row.right_no is not None
    )


def change_label(change: GitChange) -> str:
    if change.group == "untracked":
        return change.path
    if change.original_path and change.status in {"R", "C"}:
        return f"{change.status} {change.original_path} -> {change.path}"
    return f"{change.status} {change.path}"


def run_git(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a read-only Git command in `root`."""

    if not args or args[0] not in ALLOWED_COMMANDS:
        command = args[0] if args else ""
        raise GitCommandError(f"Refusing git command: {command or '(empty)'}")
    if any(arg in {"--output", "--ext-diff"} for arg in args):
        raise GitCommandError("Refusing a Git option that can leave the repository.")
    argv = ["git", *args]
    if args[0] == "diff" and "--no-ext-diff" not in args:
        argv = ["git", "diff", "--no-ext-diff", *args[1:]]
    try:
        return subprocess.run(
            argv,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitCommandError("git is not installed") from exc


def parse_porcelain_z(blob: str) -> list[GitChange]:
    parts = blob.split("\0")
    if parts and parts[-1] == "":
        parts.pop()
    changes: list[GitChange] = []
    index = 0
    while index < len(parts):
        entry = parts[index]
        index += 1
        if len(entry) < 3:
            continue
        status_x, status_y = entry[0], entry[1]
        path = entry[3:]
        original: str | None = None
        if "R" in (status_x, status_y) or "C" in (status_x, status_y):
            if index >= len(parts):
                break
            # porcelain -z prints the current path, then the original path.
            original = parts[index]
            index += 1
        xy = status_x + status_y
        if xy == "!!" or not path:
            continue
        if xy in UNMERGED:
            changes.append(GitChange("conflict", "U", path, original))
            continue
        if xy == "??":
            changes.append(GitChange("untracked", "?", path))
            continue
        if status_x not in {" ", "?"}:
            changes.append(GitChange("staged", status_x, path, original))
        if status_y not in {" ", "?"}:
            changes.append(GitChange("unstaged", status_y, path, original))
    return changes


def list_changes(root: Path) -> list[GitChange]:
    completed = run_git(root, ["status", "--porcelain", "-z"])
    if completed.returncode != 0:
        message = completed.stderr.strip() or "git status failed"
        raise GitCommandError(message)
    return parse_porcelain_z(completed.stdout)


def _line_number(value: int | None) -> int | None:
    if value is None or value <= 0:
        return None
    return value


def _rows_from_hunk(header: str, body: list[tuple[str, str]]) -> list[DiffRow]:
    rows = [DiffRow("hunk", None, None, header, header)]
    left_no, right_no = _hunk_starts(header)
    index = 0
    while index < len(body):
        prefix = body[index][0]
        if prefix == " ":
            text = body[index][1]
            rows.append(DiffRow("context", _line_number(left_no), _line_number(right_no), text, text))
            left_no = _advance(left_no)
            right_no = _advance(right_no)
            index += 1
            continue
        if prefix == "-":
            deleted: list[str] = []
            while index < len(body) and body[index][0] == "-":
                deleted.append(body[index][1])
                index += 1
            added: list[str] = []
            while index < len(body) and body[index][0] == "+":
                added.append(body[index][1])
                index += 1
            rows.extend(_pair(deleted, added, left_no, right_no))
            left_no = _advance(left_no, len(deleted))
            right_no = _advance(right_no, len(added))
            continue
        if prefix == "+":
            added = []
            while index < len(body) and body[index][0] == "+":
                added.append(body[index][1])
                index += 1
            rows.extend(_pair([], added, left_no, right_no))
            right_no = _advance(right_no, len(added))
            continue
        index += 1
    return rows


def _hunk_starts(header: str) -> tuple[int | None, int | None]:
    try:
        ranges = header.split("@@", 2)[1].strip().split()
        old = ranges[0][1:].split(",", 1)[0]
        new = ranges[1][1:].split(",", 1)[0]
        return int(old), int(new)
    except (IndexError, ValueError):
        return 1, 1


def _advance(value: int | None, count: int = 1) -> int | None:
    if value is None:
        return None
    return value + count


def _pair(
    deleted: list[str],
    added: list[str],
    left_no: int | None,
    right_no: int | None,
) -> list[DiffRow]:
    rows: list[DiffRow] = []
    total = max(len(deleted), len(added))
    for offset in range(total):
        left = deleted[offset] if offset < len(deleted) else None
        right = added[offset] if offset < len(added) else None
        current_left = _line_number(None if left_no is None else left_no + offset)
        current_right = _line_number(None if right_no is None else right_no + offset)
        if left is not None and right is not None:
            rows.append(DiffRow("change", current_left, current_right, left, right))
        elif left is not None:
            rows.append(DiffRow("delete", current_left, None, left, ""))
        else:
            rows.append(DiffRow("add", None, current_right, "", right or ""))
    return rows


def parse_unified_diff(text: str) -> ParsedDiff:
    lines = text.splitlines()
    rows: list[DiffRow] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.startswith("@@"):
            index += 1
            continue
        header = line
        index += 1
        body: list[tuple[str, str]] = []
        while index < len(lines) and not lines[index].startswith("@@") and not lines[index].startswith("diff --git"):
            current = lines[index]
            index += 1
            if current.startswith("\\"):
                continue
            if current == "":
                body.append((" ", ""))
            else:
                body.append((current[0], current[1:]))
        rows.extend(_rows_from_hunk(header, body))
    if not rows:
        message = text.strip() or "No differences."
        rows = [DiffRow("message", None, None, message, message)]
    hunk_rows = tuple(position for position, row in enumerate(rows) if row.kind == "hunk")
    return ParsedDiff(tuple(rows), hunk_rows, text)


def diff_args(change: GitChange) -> list[str] | None:
    """Return Git diff arguments, or None when the diff is read from disk."""

    if change.group == "untracked":
        return None
    if change.group == "staged":
        return ["diff", "--cached", "--find-renames", "--unified=3", "--", change.path]
    if change.group == "review":
        return ["diff", "HEAD", "--find-renames", "--unified=3", "--", change.path]
    return ["diff", "--find-renames", "--unified=3", "--", change.path]


def untracked_diff(root: Path, relative: str) -> str:
    path = root / relative
    if not path.is_file():
        return "This file is not in the working tree."
    try:
        data = path.read_bytes()
    except OSError as exc:
        return str(exc)
    if b"\0" in data[:8192]:
        return f"Binary files /dev/null and b/{relative} differ"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return f"Binary files /dev/null and b/{relative} differ"
    lines = text.splitlines()
    if not lines:
        return f"diff --git a/{relative} b/{relative}\n--- /dev/null\n+++ b/{relative}\n@@ -0,0 +0,0 @@\n"
    body = "".join(f"+{line}\n" for line in lines)
    return (
        f"diff --git a/{relative} b/{relative}\n"
        f"--- /dev/null\n"
        f"+++ b/{relative}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n"
        f"{body}"
    )


def load_diff(root: Path, change: GitChange) -> ParsedDiff:
    args = diff_args(change)
    if args is None:
        return parse_unified_diff(untracked_diff(root, change.path))
    completed = run_git(root, args)
    if completed.returncode not in (0, 1):
        message = completed.stderr.strip() or "git diff failed"
        raise GitCommandError(message)
    return parse_unified_diff(completed.stdout)
