from __future__ import annotations

import asyncio
import tomllib
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static

from herdr_explorer.app import AndroidExplorerApp
from herdr_explorer.core.git import parse_unified_diff
from herdr_explorer.widgets.diff_viewer import DiffView
from herdr_explorer.widgets.file_viewer import FileViewer
from herdr_explorer.widgets.sidebar import ChangesList, ProjectTree
from tests.conftest import commit_all

ROOT = Path(__file__).resolve().parents[2]


def test_mouse_clicks_stay_on_the_cell_under_the_pointer() -> None:
    from herdr_explorer.app import _CELL_MOUSE, install_cell_mouse

    install_cell_mouse()
    import textual.drivers.linux_driver as linux_driver

    class Dummy:
        def __init__(self) -> None:
            self._mouse = True
            self.written: list[str] = []

        def write(self, data: str) -> None:
            self.written.append(data)

        def flush(self) -> None:
            return

    dummy = Dummy()
    linux_driver.LinuxDriver._enable_mouse_support(dummy)  # type: ignore[arg-type]
    for mode in ("1000l", "1002l", "1003l"):
        assert mode not in _CELL_MOUSE
    assert "".join(dummy.written) == _CELL_MOUSE
    linux_driver.LinuxDriver._enable_mouse_pixels(dummy)  # type: ignore[arg-type]
    linux_driver.LinuxDriver._query_in_band_window_resize(dummy)  # type: ignore[arg-type]
    assert "".join(dummy.written) == _CELL_MOUSE

    parser = linux_driver.XTermParser()
    parser.mouse_pixels = True
    parser.terminal_size = (100, 40)
    parser.terminal_pixel_size = (1000, 800)
    event = parser.parse_mouse_code("\x1b[<0;11;21M")
    assert event is not None
    assert (event.x, event.y) == (10, 20)

    # Captured from Herdr: press then release at column 9, row 11 (1-based).
    from textual.events import MouseDown, MouseScrollLeft, MouseScrollRight, MouseUp

    decoded = list(parser.feed("\x1b[M )+\x1b[M#)+"))
    assert [type(item) for item in decoded] == [MouseDown, MouseUp]
    assert (decoded[0].x, decoded[0].y) == (8, 10)
    assert decoded[0].button == 1
    assert (decoded[1].x, decoded[1].y) == (8, 10)

    # X10 wheel left/right: button 66 and 67, at column 10, row 5.
    sideways = list(parser.feed("\x1b[Mb*%\x1b[Mc*%"))
    assert [type(item) for item in sideways] == [MouseScrollLeft, MouseScrollRight]
    assert (sideways[0].x, sideways[0].y) == (9, 4)

    # Shift plus a vertical wheel (button 65 + shift) stays a vertical wheel
    # with the shift flag set, which Textual scrolls sideways.
    from textual.events import MouseScrollDown

    shifted = list(parser.feed("\x1b[Me*%"))
    assert [type(item) for item in shifted] == [MouseScrollDown]
    assert shifted[0].shift

    # A wheel report past column 95 contains a byte >= 128. Strict UTF-8
    # decoding used to abort the input thread and close the pane.
    decoder = linux_driver.getincrementaldecoder("utf-8")().decode
    wide = decoder(b"\x1b[Ma" + bytes([200, 50]))
    scrolled = list(parser.feed(wide))
    assert [type(item) for item in scrolled] == [MouseScrollDown]
    assert (scrolled[0].x, scrolled[0].y) == (167, 17)
    assert decoder("café".encode()) == "café"

    # X10 drag with the left button held (32 + 0) is motion with button 1.
    from textual.events import MouseMove

    drag = list(parser.feed("\x1b[M@*%"))
    assert [type(item) for item in drag] == [MouseMove]
    assert drag[0].button == 1


def test_h_and_l_scroll_sideways(repo: Path) -> None:
    path = repo / "Long.kt"
    path.write_text("START" + ("." * 400) + "END\n", encoding="utf-8")
    app = AndroidExplorerApp(root=repo, watch=False)

    def check(pilot) -> None:  # type: ignore[no-untyped-def]
        async def inner() -> None:
            pilot.app.open_path(path)
            await pilot.pause(0.3)
            code = pilot.app.query_one("#code")
            code.focus()
            await pilot.press("l", "l")
            await pilot.pause()
            assert code.scroll_offset.x == 16
            await pilot.press("h")
            await pilot.pause()
            assert code.scroll_offset.x == 8

            from textual.events import MouseDown, MouseMove

            await code._on_mouse_down(
                MouseDown(code, 10, 2, 0, 0, 1, False, False, False, screen_x=40, screen_y=8)
            )
            await code._on_mouse_move(
                MouseMove(code, 4, 2, -6, 0, 1, False, False, False, screen_x=30, screen_y=8)
            )
            assert code.scroll_offset.x == 18

        return inner()

    _run(app, check)


def test_manifest_contract() -> None:
    from herdr_explorer import __version__

    manifest = tomllib.loads((ROOT / "herdr-plugin.toml").read_text(encoding="utf-8"))
    package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert manifest["id"] == "herdr-explorer"
    assert manifest["version"] == __version__
    assert package["project"]["version"] == __version__
    assert manifest["panes"][0]["title"] == f"File Explorer {__version__}"
    assert manifest["min_herdr_version"] == "0.7.0"
    assert manifest["platforms"] == ["linux", "macos"]
    assert manifest["build"][0]["command"] == ["sh", "scripts/install.sh"]
    pane = manifest["panes"][0]
    assert pane["id"] == "explorer"
    assert pane["placement"] == "split"
    assert pane["command"] == ["sh", "scripts/launch.sh"]


def test_scripts_are_valid_shell() -> None:
    import subprocess

    for path in (ROOT / "scripts" / "install.sh", ROOT / "scripts" / "launch.sh", ROOT / "dev" / "bootstrap.sh"):
        subprocess.run(["sh", "-n", str(path)], check=True)


def _run(app: AndroidExplorerApp, body) -> None:  # type: ignore[no-untyped-def]
    async def _wrapper() -> None:
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            await body(pilot)

    asyncio.run(_wrapper())


def test_clicking_a_folder_expands_it(repo: Path) -> None:
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    nested = repo / "app" / "src"
    nested.mkdir(parents=True)
    (nested / "Main.kt").write_text("class Main\n", encoding="utf-8")
    app = AndroidExplorerApp(root=repo, watch=False)

    def check(pilot) -> None:  # type: ignore[no-untyped-def]
        async def inner() -> None:
            tree = pilot.app.query_one(ProjectTree)
            await tree.reload()
            await pilot.pause(0.3)
            folder = next(
                node
                for node in tree.root.children
                if node.data is not None and node.data.path.name == "app"
            )
            assert not folder.is_expanded
            await pilot.click(tree, offset=(6, folder.line))
            await pilot.pause(0.4)
            assert folder.is_expanded
            assert any(
                child.data is not None and child.data.path.name == "src"
                for child in folder.children
            )

            await pilot.double_click(tree, offset=(6, folder.line))
            await pilot.pause(0.4)
            assert folder.is_expanded

            await pilot.click(tree, offset=(6, folder.line))
            await pilot.pause(0.2)
            assert not folder.is_expanded
            tree.focus()
            tree.move_cursor(folder)
            await pilot.press("enter")
            await pilot.pause(0.4)
            assert folder.is_expanded

            readme = next(
                node
                for node in tree.root.children
                if node.data is not None and node.data.path.name == "README.md"
            )
            await pilot.click(tree, offset=(6, readme.line))
            await pilot.pause()
            assert "README.md" in str(pilot.app.query_one("#path-bar", Static).content)

        return inner()

    _run(app, check)


def test_clicking_changes_tab_switches(repo: Path) -> None:
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    app = AndroidExplorerApp(root=repo, watch=False)

    def check(pilot) -> None:  # type: ignore[no-untyped-def]
        async def inner() -> None:
            tabs = pilot.app.query_one("#browser-tabs")
            assert tabs.active == "files"
            changes = pilot.app.query_one("#--content-tab-changes")
            # The right edge of the Changes half, past the word itself.
            await pilot.click(offset=(changes.region.right - 1, changes.region.y))
            await pilot.pause()
            assert tabs.active == "changes"

            files = pilot.app.query_one("#--content-tab-files")
            underline = pilot.app.query_one("Underline")
            await pilot.click(offset=(files.region.x + 1, underline.region.y))
            await pilot.pause()
            assert tabs.active == "files"

            # A press that slips from the tab onto the file tree still switches.
            await pilot.mouse_down(offset=(changes.region.right - 1, changes.region.y))
            await pilot.mouse_up("#tree")
            await pilot.pause()
            assert tabs.active == "changes"

        return inner()

    _run(app, check)


def test_long_added_line_stays_on_screen() -> None:
    class Host(App[None]):
        def compose(self) -> ComposeResult:
            yield DiffView()

    added = "ADDED_" + ("x" * 200)
    parsed = parse_unified_diff(f"@@ -0,0 +1,1 @@\n+{added}\n")

    async def _wrapper() -> None:
        async with Host().run_test(size=(60, 16)) as pilot:
            await pilot.pause()
            view = pilot.app.query_one(DiffView)
            view.set_diff(parsed)
            await pilot.pause()
            add_index = next(index for index, row in enumerate(view.rows) if row.kind == "add")
            strip = view.render_line(add_index - view.scroll_offset.y)
            text = "".join(segment.text for segment in strip)
            assert "ADDED_" in text
            added_segment = next(segment for segment in strip if "ADDED_" in segment.text)
            assert added_segment.style is not None
            assert added_segment.style.color is not None
            assert added_segment.style.bgcolor is not None
            assert "f0fdf4" in added_segment.style.color.name.lower()
            assert "14532d" in added_segment.style.bgcolor.name.lower()

    asyncio.run(_wrapper())


def test_changed_lines_stay_marked_in_the_file(repo: Path) -> None:
    path = repo / "Long.kt"
    path.write_text("keep\n", encoding="utf-8")
    commit_all(repo)
    path.write_text("keep\n" + ("ADDED_" + "x" * 80) + "\n", encoding="utf-8")
    app = AndroidExplorerApp(root=repo, watch=False)

    def check(pilot) -> None:  # type: ignore[no-untyped-def]
        async def inner() -> None:
            pilot.app.open_path(path)
            await pilot.pause(0.6)
            code = pilot.app.query_one("#code")
            assert 2 in code.changed_lines
            marked = code.render_line(1)
            assert any(
                segment.style is not None
                and segment.style.bgcolor is not None
                and "14532d" in segment.style.bgcolor.name.lower()
                for segment in marked
            )
            context = code.render_line(0)
            assert not any(
                segment.style is not None
                and segment.style.bgcolor is not None
                and "14532d" in segment.style.bgcolor.name.lower()
                for segment in context
            )
            await pilot.press("d")
            await pilot.pause(0.5)
            diff = pilot.app.query_one(DiffView)
            assert diff.display
            add_index = next(index for index, row in enumerate(diff.rows) if row.kind == "add")
            text = "".join(
                segment.text
                for segment in diff.render_line(add_index - diff.scroll_offset.y)
            )
            assert "ADDED_" in text
            await pilot.press("d")
            await pilot.pause()
            assert not diff.display
            assert 2 in code.changed_lines

        return inner()

    _run(app, check)


def test_browses_and_does_not_modify_file(repo: Path) -> None:
    source = repo / "src" / "main"
    source.mkdir(parents=True)
    kotlin = source / "ProfileFragment.kt"
    original = "class ProfileFragment {\n    fun render() = \"Profile\"\n}\n"
    kotlin.write_text(original, encoding="utf-8")
    (repo / "build").mkdir()
    (repo / "build" / "Generated.kt").write_text("nope\n", encoding="utf-8")
    (repo / "app" / "src" / "main" / "res").mkdir(parents=True)
    xml = repo / "app" / "src" / "main" / "res" / "colors.xml"
    xml.write_text("<resources></resources>\n", encoding="utf-8")
    commit_all(repo)
    kotlin.write_text(original.replace("Profile", "Reviewed"), encoding="utf-8")
    before = kotlin.read_bytes()

    app = AndroidExplorerApp(root=repo, watch=False)

    def check(pilot) -> None:  # type: ignore[no-untyped-def]
        async def inner() -> None:
            tree = pilot.app.query_one(ProjectTree)
            await tree.reload()
            names = [child.data.path.name for child in tree.root.children if child.data is not None]
            assert "src" in names
            assert "build" not in names
            pilot.app.open_path(kotlin)
            await pilot.pause()
            viewer = pilot.app.query_one(FileViewer)
            assert "Reviewed" in viewer.query_one("#code").text
            assert kotlin.read_bytes() == before
            await pilot.press("c")
            assert pilot.app.query_one("#browser-tabs").active == "changes"
            await pilot.pause(0.4)
            changes = pilot.app.query_one(ChangesList)
            assert any(option.id == "unstaged:src/main/ProfileFragment.kt" for option in changes.options if option.id)
            await pilot.press("d")
            await pilot.pause(0.5)
            diff = pilot.app.query_one(DiffView)
            assert diff.display
            assert any(row.kind == "change" for row in diff.rows)
            await pilot.press("d")
            await pilot.pause()
            assert not pilot.app.query_one(DiffView).display
            await pilot.press("question_mark")
            await pilot.pause()
            assert "Quick open" in str(pilot.app.screen.query_one(Static).content)
            await pilot.press("escape")
            await pilot.pause()

        return inner()

    _run(app, check)
    assert kotlin.read_bytes() == before


def test_quick_open_and_search(repo: Path) -> None:
    source = repo / "app" / "src"
    source.mkdir(parents=True)
    target = source / "ProfileFragment.kt"
    target.write_text("class ProfileFragment {\n    val token = NEEDLE_TOKEN\n}\n", encoding="utf-8")
    commit_all(repo)
    app = AndroidExplorerApp(root=repo, watch=False)

    def check(pilot) -> None:  # type: ignore[no-untyped-def]
        async def inner() -> None:
            pilot.app._file_index = ["app/src/ProfileFragment.kt"]
            await pilot.press("slash")
            await pilot.pause()
            await pilot.press(*"Profile")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert "class ProfileFragment" in pilot.app.query_one("#code").text
            await pilot.press("ctrl+shift+f")
            await pilot.pause()
            await pilot.press(*"NEEDLE_TOKEN")
            await pilot.pause(0.5)
            results = pilot.app.screen.query_one("#search-results")
            labels = [str(option.prompt) for option in results.options]
            assert any("ProfileFragment.kt:2" in label for label in labels)
            await pilot.press("enter")
            await pilot.pause()
            code = pilot.app.query_one("#code")
            assert code.cursor_location[0] == 1

        return inner()

    _run(app, check)


def test_non_android_repository(tmp_path: Path) -> None:
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "README.md").write_text("# Notes\n", encoding="utf-8")
    app = AndroidExplorerApp(root=notes, watch=False)

    def check(pilot) -> None:  # type: ignore[no-untyped-def]
        async def inner() -> None:
            pilot.app.open_path(notes / "README.md")
            await pilot.pause()
            assert "Notes" in pilot.app.query_one("#code").text
            await pilot.pause(0.4)
            assert pilot.app.query_one(ChangesList).option_count >= 1

        return inner()

    _run(app, check)


def test_empty_project_message(monkeypatch) -> None:
    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.delenv("HERDR_PLUGIN_CONTEXT_JSON", raising=False)
    monkeypatch.delenv("HERDR_WORKSPACE_ID", raising=False)
    monkeypatch.delenv("ANDROID_EXPLORER_ROOT", raising=False)
    app = AndroidExplorerApp(watch=False)

    def check(pilot) -> None:  # type: ignore[no-untyped-def]
        async def inner() -> None:
                assert "No project directory found" in str(pilot.app.query_one("#empty-state", Static).content)

        return inner()

    _run(app, check)


def test_screenshots(repo: Path) -> None:
    source = repo / "src" / "main" / "java" / "com" / "example"
    source.mkdir(parents=True)
    kotlin = source / "ProfileFragment.kt"
    kotlin.write_text(
        "package com.example\n\nclass ProfileFragment {\n    fun render() = \"Profile\"\n}\n",
        encoding="utf-8",
    )
    commit_all(repo)
    kotlin.write_text(
        "package com.example\n\nclass ProfileFragment {\n    fun render() = \"Reviewed\"\n}\n",
        encoding="utf-8",
    )
    (source / "ProfileState.kt").write_text("class ProfileState\n", encoding="utf-8")
    app = AndroidExplorerApp(root=repo, watch=False)
    destination = ROOT / "dev" / "screenshots"
    destination.mkdir(exist_ok=True)

    def check(pilot) -> None:  # type: ignore[no-untyped-def]
        async def inner() -> None:
            tree = pilot.app.query_one(ProjectTree)
            await tree.reload()
            await pilot.pause()
            pilot.app.open_path(kotlin)
            await pilot.pause()
            (destination / "files.svg").write_text(
                pilot.app.export_screenshot(title="Files"),
                encoding="utf-8",
            )
            await pilot.press("c")
            await pilot.pause(0.5)
            (destination / "changes.svg").write_text(
                pilot.app.export_screenshot(title="Changes"),
                encoding="utf-8",
            )
            await pilot.press("d")
            await pilot.pause(0.5)
            (destination / "diff.svg").write_text(
                pilot.app.export_screenshot(title="Diff"),
                encoding="utf-8",
            )

        return inner()

    _run(app, check)
    for name in ("files.svg", "changes.svg", "diff.svg"):
        svg = (destination / name).read_text(encoding="utf-8")
        assert svg.startswith("<svg") or "<svg" in svg[:200]
        assert len(svg) > 200
