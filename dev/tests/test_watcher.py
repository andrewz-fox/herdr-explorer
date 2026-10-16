from __future__ import annotations

import threading
from pathlib import Path

from herdr_explorer.core.watcher import Debouncer, ProjectWatcher, classify_event


def test_classify_event_ignores_generated_output(tmp_path: Path) -> None:
    ignored, structure = classify_event(tmp_path, str(tmp_path / "build" / "tmp"), False, "modified")
    assert ignored is True
    assert structure is False
    ignored, structure = classify_event(tmp_path, str(tmp_path / ".idea" / "workspace.xml"), False, "modified")
    assert ignored is True


def test_classify_event_distinguishes_structure(tmp_path: Path) -> None:
    source = tmp_path / "src" / "Main.kt"
    ignored, structure = classify_event(tmp_path, str(source), False, "modified")
    assert (ignored, structure) == (False, False)
    ignored, structure = classify_event(tmp_path, str(source), False, "created")
    assert (ignored, structure) == (False, True)
    ignored, structure = classify_event(tmp_path, str(tmp_path / ".git" / "index"), False, "modified")
    assert (ignored, structure) == (False, False)
    ignored, structure = classify_event(tmp_path, str(tmp_path / "src"), True, "modified")
    assert ignored is True


def test_debouncer_coalesces_events() -> None:
    fired: list[bool] = []
    debouncer = Debouncer(0.05, fired.append)
    debouncer.push(False)
    debouncer.push(True)
    threading.Event().wait(0.2)
    assert fired == [True]


def test_watcher_emits_for_source_edits(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    ready = threading.Event()
    seen: list[bool] = []

    def emit(structure_changed: bool) -> None:
        seen.append(structure_changed)
        ready.set()

    watcher = ProjectWatcher(tmp_path, emit, delay=0.05)
    watcher.start()
    try:
        (source / "Main.kt").write_text("class Main\n", encoding="utf-8")
        assert ready.wait(3)
        assert seen
    finally:
        watcher.stop()
