"""Debounced filesystem watching that never writes project files."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from textual.message import Message
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from herdr_explorer.core.project import IGNORED_DIRECTORY_NAMES

WATCH_SKIP = IGNORED_DIRECTORY_NAMES
STRUCTURE_EVENTS = frozenset({"created", "deleted", "moved"})


class RefreshSignal(Message):
    """Ask the app to reload Git state and, when needed, the file tree."""

    def __init__(self, structure_changed: bool) -> None:
        super().__init__()
        self.structure_changed = structure_changed


class Debouncer:
    def __init__(self, delay: float, emit: Callable[[bool], None]) -> None:
        self.delay = delay
        self.emit = emit
        self._timer: threading.Timer | None = None
        self._structure = False
        self._lock = threading.Lock()

    def push(self, structure_changed: bool) -> None:
        with self._lock:
            self._structure = self._structure or structure_changed
            if self._timer is not None:
                self._timer.cancel()
            timer = threading.Timer(self.delay, self._fire)
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _fire(self) -> None:
        with self._lock:
            structure = self._structure
            self._structure = False
            self._timer = None
        self.emit(structure)

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._structure = False


def classify_event(root: Path, raw_path: str, is_directory: bool, event_type: str) -> tuple[bool, bool]:
    """Return `(ignored, structure_changed)` for one filesystem event."""

    try:
        relative = Path(raw_path).resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return True, False
    parts = relative.parts
    if any(part in WATCH_SKIP or (part.startswith(".") and part != ".git") for part in parts):
        return True, False
    if ".git" in parts:
        return False, False
    if is_directory and event_type == "modified":
        return True, False
    return False, event_type in STRUCTURE_EVENTS


class _Handler(FileSystemEventHandler):
    def __init__(self, root: Path, debouncer: Debouncer) -> None:
        super().__init__()
        self.root = root
        self.debouncer = debouncer

    def on_any_event(self, event) -> None:  # type: ignore[no-untyped-def]
        paths = [event.src_path]
        dest = getattr(event, "dest_path", None)
        if dest:
            paths.append(dest)
        ignored = True
        structure = False
        for raw_path in paths:
            event_ignored, event_structure = classify_event(
                self.root,
                str(raw_path),
                bool(event.is_directory),
                event.event_type,
            )
            ignored = ignored and event_ignored
            structure = structure or event_structure
        if ignored:
            return
        self.debouncer.push(structure)


class ProjectWatcher:
    def __init__(self, root: Path, emit: Callable[[bool], None], delay: float = 0.2) -> None:
        self.root = root
        self._debouncer = Debouncer(delay, emit)
        self._observer = Observer()
        self._started = False

    def start(self) -> None:
        self._observer.schedule(_Handler(self.root, self._debouncer), str(self.root), recursive=True)
        self._observer.start()
        self._started = True

    def stop(self) -> None:
        self._debouncer.cancel()
        if not self._started:
            return
        self._observer.stop()
        self._observer.join(timeout=2)
        self._started = False
