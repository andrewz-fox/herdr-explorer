from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from herdr_explorer.core.git import (
    GitChange,
    GitCommandError,
    changed_line_numbers,
    list_changes,
    load_diff,
    parse_porcelain_z,
    parse_unified_diff,
    run_git,
)
from tests.conftest import commit_all, git


def fingerprint(root: Path) -> tuple[tuple[tuple[str, bytes], ...], bytes, str]:
    files: list[tuple[str, bytes]] = []
    for path in sorted(root.rglob("*")):
        if ".git" in path.parts or not path.is_file():
            continue
        files.append((path.relative_to(root).as_posix(), path.read_bytes()))
    status = subprocess.run(
        ["git", "status", "--porcelain", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return (tuple(files), status, head)


def test_refuses_commands_that_modify_git(repo: Path) -> None:
    (repo / "A.kt").write_text("class A\n", encoding="utf-8")
    commit_all(repo)
    before = fingerprint(repo)
    for args in (
        ["add", "A.kt"],
        ["commit", "-m", "nope"],
        ["reset", "--hard"],
        ["checkout", "--", "A.kt"],
        ["clean", "-fd"],
        ["stash"],
        ["merge", "main"],
        ["rebase", "main"],
        ["diff", "--ext-diff"],
    ):
        with pytest.raises(GitCommandError):
            run_git(repo, args)
    assert fingerprint(repo) == before


def test_status_groups(repo: Path) -> None:
    (repo / "Keep.kt").write_text("class Keep\n", encoding="utf-8")
    (repo / "Staged.kt").write_text("one\n", encoding="utf-8")
    (repo / "Both.kt").write_text("one\n", encoding="utf-8")
    (repo / "OldName.kt").write_text("rename me\n", encoding="utf-8")
    (repo / "Removed.kt").write_text("gone\n", encoding="utf-8")
    (repo / "Unstaged.kt").write_text("worktree\n", encoding="utf-8")
    commit_all(repo)

    (repo / "Staged.kt").write_text("two\n", encoding="utf-8")
    git(repo, "add", "Staged.kt")
    (repo / "Both.kt").write_text("staged\n", encoding="utf-8")
    git(repo, "add", "Both.kt")
    (repo / "Both.kt").write_text("staged\nunstaged\n", encoding="utf-8")
    (repo / "Unstaged.kt").write_text("worktree changed\n", encoding="utf-8")
    (repo / "Removed.kt").unlink()
    (repo / "NewName.kt").write_text((repo / "OldName.kt").read_text(encoding="utf-8"), encoding="utf-8")
    (repo / "OldName.kt").unlink()
    git(repo, "add", "-A", "--", "OldName.kt", "NewName.kt")
    (repo / "My File.kt").write_text("spaces\n", encoding="utf-8")
    (repo / "画面.kt").write_text("unicode\n", encoding="utf-8")

    before = fingerprint(repo)
    changes = list_changes(repo)
    assert fingerprint(repo) == before

    by_key = {(change.group, change.path): change for change in changes}
    assert by_key[("staged", "Staged.kt")].status == "M"
    assert by_key[("staged", "Both.kt")].status == "M"
    assert by_key[("unstaged", "Both.kt")].status == "M"
    assert by_key[("unstaged", "Unstaged.kt")].status == "M"
    assert by_key[("unstaged", "Removed.kt")].status == "D"
    assert by_key[("staged", "NewName.kt")].status == "R"
    assert by_key[("staged", "NewName.kt")].original_path == "OldName.kt"
    assert by_key[("untracked", "My File.kt")].path == "My File.kt"
    assert by_key[("untracked", "画面.kt")].path == "画面.kt"


def test_merge_conflict(repo: Path) -> None:
    path = repo / "Conflict.kt"
    path.write_text("base\n", encoding="utf-8")
    commit_all(repo, "base")
    git(repo, "checkout", "-b", "other")
    path.write_text("other\n", encoding="utf-8")
    commit_all(repo, "other")
    git(repo, "checkout", "main")
    path.write_text("main\n", encoding="utf-8")
    commit_all(repo, "main")
    git(repo, "merge", "other", check=False)
    changes = list_changes(repo)
    assert any(change.group == "conflict" and change.path == "Conflict.kt" for change in changes)


def test_clean_repository(repo: Path) -> None:
    (repo / "A.kt").write_text("class A\n", encoding="utf-8")
    commit_all(repo)
    assert list_changes(repo) == []


def test_non_repository(tmp_path: Path) -> None:
    with pytest.raises(GitCommandError):
        list_changes(tmp_path)


def test_side_by_side_diff_pairs_hunks() -> None:
    text = """\
diff --git a/A.kt b/A.kt
--- a/A.kt
+++ b/A.kt
@@ -1,4 +1,4 @@
 same
-old
+new
 context
+added
"""
    parsed = parse_unified_diff(text)
    kinds = [row.kind for row in parsed.rows]
    assert kinds == ["hunk", "context", "change", "context", "add"]
    change = parsed.rows[2]
    assert (change.left, change.right) == ("old", "new")
    assert change.left_no == 2
    assert change.right_no == 2
    added = parsed.rows[4]
    assert added.left == ""
    assert added.right == "added"
    assert parsed.hunk_rows == (0,)
    assert changed_line_numbers(parsed) == frozenset({2, 4})


def test_load_diff_does_not_change_repository(repo: Path) -> None:
    path = repo / "ProfileFragment.kt"
    path.write_text("class ProfileFragment {\n    fun render() = \"before\"\n}\n", encoding="utf-8")
    commit_all(repo)
    path.write_text("class ProfileFragment {\n    fun render() = \"after\"\n}\n", encoding="utf-8")
    (repo / "ProfileState.kt").write_text("class ProfileState\n", encoding="utf-8")
    before = fingerprint(repo)
    unstaged = load_diff(repo, GitChange("unstaged", "M", "ProfileFragment.kt"))
    untracked = load_diff(repo, GitChange("untracked", "?", "ProfileState.kt"))
    assert any(row.kind == "change" for row in unstaged.rows)
    assert any(row.kind == "add" and "ProfileState" in row.right for row in untracked.rows)
    assert fingerprint(repo) == before


def test_deleted_file_diff(repo: Path) -> None:
    path = repo / "Gone.kt"
    path.write_text("class Gone\n", encoding="utf-8")
    commit_all(repo)
    path.unlink()
    parsed = load_diff(repo, GitChange("unstaged", "D", "Gone.kt"))
    assert any(row.kind == "delete" and row.left == "class Gone" for row in parsed.rows)


def test_porcelain_parser_skips_ignored() -> None:
    changes = parse_porcelain_z("!! ignored\0?? visible.kt\0")
    assert [change.path for change in changes] == ["visible.kt"]
