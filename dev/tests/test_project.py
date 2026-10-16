from __future__ import annotations

import json
import os
from pathlib import Path

from herdr_explorer.core.project import (
    find_project_root,
    language_for,
    list_relative_files,
    load_file,
    resolve_project_root,
)


def test_language_map() -> None:
    assert language_for(Path("Main.java")) == "java"
    assert language_for(Path("layout.xml")) == "xml"
    assert language_for(Path("app.json")) == "json"
    assert language_for(Path("config.yaml")) == "yaml"
    assert language_for(Path("README.md")) == "markdown"
    assert language_for(Path("ProfileFragment.kt")) is None
    assert language_for(Path("build.gradle.kts")) is None
    assert language_for(Path("local.properties")) is None


def test_load_file_kinds(tmp_path: Path, monkeypatch) -> None:
    text = tmp_path / "Main.java"
    text.write_text("class Main {}\n", encoding="utf-8")
    payload = load_file(text)
    assert payload.kind == "text"
    assert payload.language == "java"
    assert "class Main" in payload.text

    binary = tmp_path / "icon.png"
    binary.write_bytes(b"\x89PNG\x00\n")
    assert load_file(binary).kind == "binary"

    missing = tmp_path / "Gone.kt"
    assert load_file(missing).kind == "missing"

    monkeypatch.setattr("herdr_explorer.core.project.MAX_TEXT_BYTES", 8)
    huge = tmp_path / "Huge.kt"
    huge.write_text("123456789", encoding="utf-8")
    assert load_file(huge).kind == "too_large"


def test_load_file_does_not_modify_bytes(tmp_path: Path) -> None:
    path = tmp_path / "Profile Fragment.kt"
    original = "class ProfileFragment\n画面\n".encode()
    path.write_bytes(original)
    before = path.stat().st_mtime_ns
    assert load_file(path).text.startswith("class ProfileFragment")
    assert path.read_bytes() == original
    assert path.stat().st_mtime_ns == before


def test_iter_files_skips_generated_and_hidden(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Main.kt").write_text("class Main\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "Hidden.kt").write_text("nope\n", encoding="utf-8")
    (tmp_path / ".idea").mkdir()
    (tmp_path / ".idea" / "workspace.xml").write_text("<xml/>\n", encoding="utf-8")
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "R.java").write_text("class R\n", encoding="utf-8")
    (tmp_path / ".hidden").write_text("secret\n", encoding="utf-8")
    assert list_relative_files(tmp_path) == ["src/Main.kt"]


def test_find_project_root_walks_to_git_dir(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    nested = root / "app" / "src"
    nested.mkdir(parents=True)
    (root / ".git").mkdir()
    assert find_project_root(nested) == root.resolve()
    loose = tmp_path / "notes"
    loose.mkdir()
    assert find_project_root(loose) == loose.resolve()


def test_herdr_context_checkout(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "checkout"
    project.mkdir()
    (project / ".git").mkdir()
    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.setenv("HERDR_PLUGIN_ROOT", str(tmp_path / "plugin"))
    monkeypatch.setenv(
        "HERDR_PLUGIN_CONTEXT_JSON",
        json.dumps({"worktree": {"checkout_path": str(project / "app"), "repo_root": str(project)}}),
    )
    monkeypatch.delenv("ANDROID_EXPLORER_ROOT", raising=False)
    assert resolve_project_root() == project.resolve()


def test_herdr_ignores_plugin_checkout(tmp_path: Path, monkeypatch) -> None:
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.setenv("HERDR_PLUGIN_ROOT", str(plugin))
    monkeypatch.setenv("HERDR_PLUGIN_CONTEXT_JSON", json.dumps({"workspace": {"cwd": str(plugin)}}))
    monkeypatch.delenv("HERDR_WORKSPACE_ID", raising=False)
    monkeypatch.delenv("ANDROID_EXPLORER_ROOT", raising=False)
    assert resolve_project_root() is None


def test_workspace_get_checkout(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    (project / ".git").write_text("gitdir: somewhere\n", encoding="utf-8")
    binary = tmp_path / "herdr"
    payload = {
        "result": {
            "workspace": {
                "worktree": {
                    "checkout_path": str(project),
                    "repo_root": str(project),
                }
            }
        }
    }
    binary.write_text(
        "#!/usr/bin/env python3\nimport json, sys\njson.dump(" + repr(payload) + ", sys.stdout)\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.setenv("HERDR_BIN_PATH", str(binary))
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "w1")
    monkeypatch.delenv("HERDR_PLUGIN_CONTEXT_JSON", raising=False)
    monkeypatch.delenv("ANDROID_EXPLORER_ROOT", raising=False)
    assert resolve_project_root() == project.resolve()


def test_outside_herdr_uses_cwd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("HERDR_ENV", raising=False)
    monkeypatch.delenv("ANDROID_EXPLORER_ROOT", raising=False)
    monkeypatch.delenv("HERDR_PLUGIN_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    assert resolve_project_root() == tmp_path.resolve()
    assert os.getcwd() == str(tmp_path)
