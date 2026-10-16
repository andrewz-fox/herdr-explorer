"""Project root lookup and read-only file loading."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

IGNORED_DIRECTORY_NAMES = frozenset(
    {
        "build",
        ".gradle",
        ".idea",
        ".cxx",
        ".externalNativeBuild",
        ".kotlin",
        "captures",
        "out",
        "generated",
    }
)

LANGUAGE_BY_SUFFIX = {
    ".java": "java",
    ".xml": "xml",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".md": "markdown",
    ".markdown": "markdown",
}

MAX_TEXT_BYTES = 2_000_000
_BINARY_PROBE = 8192


@dataclass(frozen=True)
class FilePayload:
    """Contents of a file the viewer is allowed to show."""

    path: Path
    kind: str
    text: str = ""
    language: str | None = None
    message: str = ""


def is_hidden_name(name: str) -> bool:
    return name.startswith(".")


def is_ignored_directory(name: str) -> bool:
    return name in IGNORED_DIRECTORY_NAMES or is_hidden_name(name)


def language_for(path: Path) -> str | None:
    return LANGUAGE_BY_SUFFIX.get(path.suffix.lower())


def relative_display(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return path.name


def find_project_root(start: Path) -> Path:
    """Use the Git checkout that contains `start`, or `start` itself."""

    try:
        resolved = start.expanduser().resolve()
    except OSError:
        return start
    if not resolved.exists():
        return resolved
    for candidate in (resolved, *resolved.parents):
        if (candidate / ".git").exists():
            return candidate
    return resolved


def load_file(path: Path) -> FilePayload:
    """Read a UTF-8 text file without writing anything."""

    if not path.exists():
        return FilePayload(path, "missing", message="This file is not in the working tree.")
    if not path.is_file():
        return FilePayload(path, "unreadable", message="Not a file.")
    try:
        size = path.stat().st_size
    except OSError as exc:
        return FilePayload(path, "unreadable", message=str(exc))
    if size > MAX_TEXT_BYTES:
        return FilePayload(path, "too_large", message="This file is too large to preview.")
    try:
        data = path.read_bytes()
    except OSError as exc:
        return FilePayload(path, "unreadable", message=str(exc))
    if b"\0" in data[:_BINARY_PROBE]:
        return FilePayload(path, "binary", message="Binary file.")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return FilePayload(path, "binary", message="File is not UTF-8 text.")
    return FilePayload(path, "text", text=text, language=language_for(path))


def iter_files(root: Path) -> Iterable[Path]:
    """Yield project files, skipping hidden and generated directories."""

    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        directories: list[Path] = []
        files: list[Path] = []
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if is_ignored_directory(entry.name):
                        continue
                    directories.append(entry)
                elif entry.is_file() and not is_hidden_name(entry.name):
                    files.append(entry)
            except OSError:
                continue
        for directory in sorted(directories, key=lambda item: item.name.lower(), reverse=True):
            stack.append(directory)
        for file in sorted(files, key=lambda item: item.name.lower()):
            yield file


def list_relative_files(root: Path) -> list[str]:
    return [relative_display(path, root) for path in iter_files(root)]


def _is_plugin_root(path: Path) -> bool:
    plugin_root = os.environ.get("HERDR_PLUGIN_ROOT")
    if not plugin_root:
        return False
    try:
        return path.resolve() == Path(plugin_root).expanduser().resolve()
    except OSError:
        return False


def _accept_directory(value: object) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        path = Path(value).expanduser().resolve()
    except OSError:
        return None
    if not path.is_dir() or _is_plugin_root(path):
        return None
    return find_project_root(path)


def candidate_paths(payload: object) -> Iterable[object]:
    """Yield path-like values from a Herdr context or command payload."""

    if not isinstance(payload, dict):
        return
    worktree = payload.get("worktree")
    if isinstance(worktree, dict):
        for key in ("checkout_path", "repo_root", "path", "cwd", "root"):
            yield worktree.get(key)
    workspace = payload.get("workspace")
    if isinstance(workspace, dict):
        yield from candidate_paths(workspace)
        for key in ("cwd", "path", "root", "checkout_path"):
            yield workspace.get(key)
    result = payload.get("result")
    if isinstance(result, dict) and result is not payload:
        yield from candidate_paths(result)
    for pane_key in ("focused_pane", "pane"):
        pane = payload.get(pane_key)
        if isinstance(pane, dict):
            for key in ("cwd", "foreground_cwd", "path"):
                yield pane.get(key)
    panes = payload.get("panes")
    if isinstance(panes, list):
        for pane in panes:
            if isinstance(pane, dict):
                for key in ("cwd", "foreground_cwd"):
                    yield pane.get(key)
    for key in ("cwd", "checkout_path", "path"):
        if key in payload and not isinstance(payload.get(key), dict):
            yield payload.get(key)


def first_directory(payload: object) -> Path | None:
    for value in candidate_paths(payload):
        path = _accept_directory(value)
        if path is not None:
            return path
    return None


def _herdr_json(args: list[str]) -> dict | None:
    binary = os.environ.get("HERDR_BIN_PATH") or "herdr"
    try:
        completed = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = completed.stdout.strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        if start < 0:
            return None
        try:
            value = json.loads(text[start:])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _root_from_herdr() -> Path | None:
    raw_context = os.environ.get("HERDR_PLUGIN_CONTEXT_JSON")
    if raw_context:
        try:
            context = json.loads(raw_context)
        except json.JSONDecodeError:
            context = None
        found = first_directory(context)
        if found is not None:
            return found

    workspace_id = os.environ.get("HERDR_WORKSPACE_ID")
    if not workspace_id:
        return None

    workspace = _herdr_json(["workspace", "get", workspace_id])
    found = first_directory(workspace)
    if found is not None:
        return found

    pane_id = os.environ.get("HERDR_PANE_ID")
    if pane_id:
        found = first_directory(_herdr_json(["pane", "get", pane_id]))
        if found is not None:
            return found

    return first_directory(_herdr_json(["pane", "list", "--workspace", workspace_id]))


def resolve_project_root(explicit: Path | None = None) -> Path | None:
    """Resolve the directory File Explorer should browse.

    Herdr starts plugin commands in the plugin checkout, so an explicit path,
    `ANDROID_EXPLORER_ROOT`, or the Herdr workspace is required inside Herdr.
    """

    if explicit is not None:
        return _accept_directory(str(explicit))
    override = os.environ.get("ANDROID_EXPLORER_ROOT")
    if override:
        return _accept_directory(override)
    if os.environ.get("HERDR_ENV") == "1":
        return _root_from_herdr()
    try:
        cwd = Path.cwd().resolve()
    except OSError:
        return None
    if not cwd.is_dir() or _is_plugin_root(cwd):
        return None
    return find_project_root(cwd)
