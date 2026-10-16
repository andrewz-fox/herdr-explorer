from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Tester",
    "GIT_AUTHOR_EMAIL": "tester@example.com",
    "GIT_COMMITTER_NAME": "Tester",
    "GIT_COMMITTER_EMAIL": "tester@example.com",
}


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=check,
        capture_output=True,
        text=True,
        env=GIT_ENV,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "android"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "tester@example.com")
    git(root, "config", "user.name", "Tester")
    return root


def commit_all(root: Path, message: str = "snapshot") -> None:
    git(root, "add", "-A")
    git(root, "commit", "-m", message)
