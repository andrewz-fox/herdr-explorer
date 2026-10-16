from __future__ import annotations

from pathlib import Path

import pytest

from herdr_explorer.core.search import fuzzy_match, parse_rg_json, rg_command, search_paths


def test_fuzzy_match_prefers_filename() -> None:
    paths = [
        "app/src/main/java/com/example/ProfileFragment.kt",
        "app/src/main/java/com/example/ProfileViewModel.kt",
        "README.md",
        "settings.gradle.kts",
    ]
    matches = fuzzy_match("Prof", paths)
    assert matches[0].endswith("ProfileFragment.kt") or matches[0].endswith("ProfileViewModel.kt")
    assert "README.md" not in matches
    assert fuzzy_match("zzz", paths) == []
    assert fuzzy_match("", paths)[0]


def test_rg_command_flags(tmp_path: Path) -> None:
    regex_command = rg_command(tmp_path, "foo.*", regex=True, case_sensitive=True)
    assert "--fixed-strings" not in regex_command
    assert "--ignore-case" not in regex_command
    plain = rg_command(tmp_path, "Foo", regex=False, case_sensitive=False)
    assert "--fixed-strings" in plain
    assert "--ignore-case" in plain
    assert "!**/build/**" in plain


def test_parse_rg_json(tmp_path: Path) -> None:
    source = tmp_path / "src" / "Main.kt"
    stdout = "\n".join(
        [
            '{"type":"begin","data":{"path":{"text":"src/Main.kt"}}}',
            '{"type":"match","data":{"path":{"text":"'
            + str(source)
            + '"},"lines":{"text":"class Main\\n"},"line_number":4}}',
        ]
    )
    hits = parse_rg_json(stdout, tmp_path)
    assert hits[0].path == "src/Main.kt"
    assert hits[0].line_number == 4
    assert hits[0].text == "class Main"


def test_search_paths_finds_content(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "Main.kt").write_text("class Main {\n    val token = NEEDLE_TOKEN\n}\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "Skip.kt").write_text("NEEDLE_TOKEN\n", encoding="utf-8")
    hits = search_paths(tmp_path, "NEEDLE_TOKEN")
    assert [hit.path for hit in hits] == ["src/Main.kt"]
    assert hits[0].line_number == 2


def test_python_fallback(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "Note.txt").write_text("Alpha\nbeta\n", encoding="utf-8")
    monkeypatch.setattr("herdr_explorer.core.search.shutil.which", lambda name: None)
    assert search_paths(tmp_path, "alpha")[0].line_number == 1
    assert search_paths(tmp_path, "alpha", case_sensitive=True) == []
    with pytest.raises(ValueError):
        search_paths(tmp_path, "(", regex=True)
