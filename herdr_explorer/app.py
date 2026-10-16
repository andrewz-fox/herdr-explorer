"""File Explorer application."""

from __future__ import annotations

import sys
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.errors import NoWidget
from textual.events import MouseDown, MouseEvent, MouseMove
from textual.screen import ModalScreen
from textual.widgets import Footer, Static, TabbedContent, TabPane
from textual.widgets._tabbed_content import ContentTab, ContentTabs
from textual.worker import get_current_worker

from herdr_explorer.core.git import (
    GitChange,
    GitCommandError,
    changed_line_numbers,
    list_changes,
    load_diff,
)
from herdr_explorer.core.project import (
    find_project_root,
    list_relative_files,
    load_file,
    relative_display,
    resolve_project_root,
)
from herdr_explorer.core.watcher import ProjectWatcher, RefreshSignal
from herdr_explorer.widgets.diff_viewer import DiffView
from herdr_explorer.widgets.file_viewer import FileViewer
from herdr_explorer.widgets.search_dialog import QuickOpenScreen, SearchScreen
from herdr_explorer.widgets.sidebar import ChangesList, ProjectTree

NO_PROJECT = (
    "No project directory found.\n\n"
    "Open File Explorer from a Herdr workspace, or pass a path:\n"
    "python -m herdr_explorer /path/to/project"
)

HELP_TEXT = """\
File Explorer

f              Focus Files
c              Focus Changes
d              Toggle diff
/              Quick open
ctrl+shift+f   Search in files
n              Next hunk
p              Previous hunk
h / l          Scroll left / right
shift+swipe    Scroll sideways with the trackpad or mouse wheel
drag           Click and drag a file or diff sideways
b              Toggle sidebar
r              Refresh
enter          Expand a folder, or open a file
space          Collapse or expand a folder
?              Shortcut help
escape         Close dialog

Click a folder to expand it. Click it again to collapse it.

Changed lines stay highlighted in the file after you close the diff.

The viewer is read-only. Git status and diff never modify the repository.
"""


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", show=False)]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog"):
            yield Static(HELP_TEXT)

    def action_close(self) -> None:
        self.dismiss(None)


class AndroidExplorerApp(App[None]):
    """Read-only project browser and Git review companion."""

    TITLE = "File Explorer"
    CSS_PATH = "styles/app.tcss"
    BINDINGS = [
        Binding("f", "focus_files", "Files"),
        Binding("c", "focus_changes", "Changes"),
        Binding("d", "toggle_diff", "Diff"),
        Binding("slash", "quick_open", "Open"),
        Binding("ctrl+shift+f", "search_files", "Search"),
        Binding("n", "next_hunk", "Next", show=False),
        Binding("p", "previous_hunk", "Prev", show=False),
        Binding("h", "scroll_sideways(-8)", "Left", show=False),
        Binding("l", "scroll_sideways(8)", "Right", show=False),
        Binding("b", "toggle_sidebar", "Sidebar", show=False),
        Binding("r", "refresh", "Refresh"),
        Binding("question_mark", "help", "Help"),
    ]

    def __init__(self, root: Path | None = None, *, watch: bool = True) -> None:
        super().__init__()
        self.project_root = resolve_project_root(root)
        self.watch_enabled = watch
        self.current_file: Path | None = None
        self.selected_change: GitChange | None = None
        self.diff_visible = False
        self.changes: list[GitChange] = []
        self._file_index: list[str] | None = None
        self._watcher: ProjectWatcher | None = None
        if self.project_root is not None:
            self.sub_title = self.project_root.name

    def compose(self) -> ComposeResult:
        if self.project_root is None:
            yield Static(NO_PROJECT, id="empty-state")
            yield Footer()
            return
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                with TabbedContent(id="browser-tabs"):
                    with TabPane("Files", id="files"):
                        yield ProjectTree(self.project_root, id="tree")
                    with TabPane("Changes", id="changes"):
                        yield ChangesList(id="changes-list")
            with Vertical(id="main"):
                yield Static("No file selected", id="path-bar")
                yield FileViewer(id="file-viewer")
                yield DiffView(id="diff-view")
        yield Footer()

    def on_mount(self) -> None:
        if self.project_root is None:
            return
        tree = self.query_one(ProjectTree)
        tree.root.set_label(self.project_root.name)
        tree.root.expand()
        tree.focus()
        self.refresh_status()
        if self.watch_enabled:
            self._start_watcher()

    def on_unmount(self) -> None:
        self._stop_watcher()

    def on_directory_tree_file_selected(self, event: ProjectTree.FileSelected) -> None:
        event.stop()
        self.open_path(event.path, from_changes=False)

    def on_option_list_option_selected(self, event: ChangesList.OptionSelected) -> None:
        change = self._change_for_option(event.option.id)
        if change is not None:
            self.open_change(change)

    def on_refresh_signal(self, message: RefreshSignal) -> None:
        self.refresh_status(reload_tree=message.structure_changed)

    async def on_event(self, event) -> None:  # type: ignore[no-untyped-def]
        if isinstance(event, MouseEvent) and not isinstance(event, MouseMove):
            try:
                widget, _ = self.get_widget_at(event.screen_x, event.screen_y)
                target = f"{type(widget).__name__}#{widget.id}"
            except Exception as exc:  # noqa: BLE001
                target = f"<{exc}>"
            debug_input(
                f"{type(event).__name__} x={event.screen_x} y={event.screen_y} "
                f"button={event.button} forwarded={event.is_forwarded} widget={target}"
            )
        await super().on_event(event)

    def on_mouse_down(self, event: MouseDown) -> None:
        """Switch Files/Changes as soon as the pointer goes down on the tab bar.

        Textual only synthesizes a click when the press and release hit the same
        widget. The tab label and its underline are different widgets, and the
        label used to be a few cells wide, so a normal click on Changes often
        never became a click at all.
        """
        tab_id = self._tab_at(event.screen_x, event.screen_y)
        if tab_id is None or not self._pointer_on_tab_bar(event.screen_x, event.screen_y):
            return
        self._show_sidebar_tab(tab_id)

    def _pointer_on_tab_bar(self, x: int, y: int) -> bool:
        try:
            widget, _ = self.get_widget_at(x, y)
        except NoWidget:
            return False
        while widget is not None:
            if isinstance(widget, ContentTabs):
                return True
            widget = widget.parent
        return False

    def _tab_at(self, x: int, y: int) -> str | None:
        if self.project_root is None:
            return None
        try:
            bar = self.query_one(ContentTabs)
        except NoMatches:
            return None
        if (x, y) not in bar.region:
            return None
        for tab in bar.query(ContentTab):
            if tab.disabled or not tab.display:
                continue
            if tab.region.x <= x < tab.region.right:
                return ContentTab.sans_prefix(tab.id or "")
        return None

    def _show_sidebar_tab(self, tab_id: str) -> None:
        self.query_one(TabbedContent).active = tab_id
        if tab_id == "changes":
            self.query_one(ChangesList).focus()
        elif tab_id == "files":
            self.query_one(ProjectTree).focus()

    def action_focus_files(self) -> None:
        if self.project_root is None:
            return
        self._show_sidebar_tab("files")

    def action_focus_changes(self) -> None:
        if self.project_root is None:
            return
        self._show_sidebar_tab("changes")

    def action_toggle_sidebar(self) -> None:
        if self.project_root is None:
            return
        sidebar = self.query_one("#sidebar")
        sidebar.display = not sidebar.display

    def action_toggle_diff(self) -> None:
        if self.project_root is None:
            return
        if self.diff_visible:
            self._show_diff(False)
            return
        if self._diff_target() is None:
            self.notify("Select a file to diff")
            return
        self._show_diff(True)
        self._load_diff_async()

    def action_next_hunk(self) -> None:
        if self.diff_visible:
            self.query_one(DiffView).next_hunk()

    def action_previous_hunk(self) -> None:
        if self.diff_visible:
            self.query_one(DiffView).previous_hunk()

    def action_scroll_sideways(self, step: int) -> None:
        if self.project_root is None:
            return
        target = self.focused
        if not isinstance(target, ProjectTree):
            if self.diff_visible:
                target = self.query_one(DiffView)
            else:
                target = self.query_one("#code")
        target.scroll_relative(x=step, animate=False)

    def action_refresh(self) -> None:
        self.refresh_status(reload_tree=True)
        self._file_index = None

    def action_quick_open(self) -> None:
        if self.project_root is None:
            return
        self.run_worker(self._open_quick_open, thread=True, exclusive=True, group="index")

    def action_search_files(self) -> None:
        if self.project_root is None:
            return
        self.push_screen(SearchScreen(self.project_root), self._open_search_result)

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def open_path(self, path: Path, *, line: int | None = None, from_changes: bool = False) -> None:
        if self.project_root is None:
            return
        relative = relative_display(path, self.project_root)
        if not from_changes:
            self.selected_change = self._review_change(relative)
        self.current_file = path
        self._update_path_bar(path, relative)
        payload = load_file(path)
        viewer = self.query_one(FileViewer)
        viewer.set_changed_lines(set())
        viewer.show(payload)
        if line is not None and payload.kind == "text":
            viewer.reveal_line(line)
        self._load_diff_async()

    def open_change(self, change: GitChange) -> None:
        if self.project_root is None:
            return
        self.selected_change = change
        self.open_path(self.project_root / change.path, from_changes=True)

    def refresh_status(self, *, reload_tree: bool = False) -> None:
        if self.project_root is None:
            return
        root = self.project_root
        current = self.current_file
        target = self._diff_target()
        self.run_worker(
            lambda: self._refresh_worker(root, current, target, reload_tree),
            thread=True,
            exclusive=True,
            group="git",
        )

    def _refresh_worker(
        self,
        root: Path,
        current: Path | None,
        target: GitChange | None,
        reload_tree: bool,
    ) -> None:
        worker = get_current_worker()
        error: str | None = None
        changes: list[GitChange] = []
        try:
            changes = list_changes(root)
        except GitCommandError as exc:
            error = str(exc)
        if worker.is_cancelled:
            return
        self.call_from_thread(self._apply_changes, changes, error)
        if current is not None and not worker.is_cancelled:
            payload = load_file(current)
            self.call_from_thread(self._show_refreshed, current, payload)
        if target is not None:
            try:
                parsed = load_diff(root, target)
            except GitCommandError as exc:
                parsed = None
                diff_error = str(exc)
            else:
                diff_error = None
            if not worker.is_cancelled:
                self.call_from_thread(self._apply_diff, parsed, diff_error, target)
        if reload_tree and not worker.is_cancelled:
            self.call_from_thread(self._reload_tree)

    def _show_refreshed(self, path: Path, payload) -> None:  # type: ignore[no-untyped-def]
        if self.current_file != path:
            return
        self.query_one(FileViewer).show(payload, keep_cursor=True)

    def _apply_changes(self, changes: list[GitChange], error: str | None) -> None:
        self.changes = changes
        if self.project_root is None:
            return
        self.query_one(ChangesList).set_changes(changes, error)

    def _apply_diff(
        self,
        parsed,  # type: ignore[no-untyped-def]
        error: str | None,
        expected: GitChange | None = None,
    ) -> None:
        if expected is not None and self._diff_target() != expected:
            return
        view = self.query_one(DiffView)
        viewer = self.query_one(FileViewer)
        if error:
            from herdr_explorer.core.git import parse_unified_diff

            view.set_diff(parse_unified_diff(error))
            viewer.set_changed_lines(set())
            return
        if parsed is not None:
            view.set_diff(parsed)
            viewer.set_changed_lines(set(changed_line_numbers(parsed)))

    def _reload_tree(self) -> None:
        tree = self.query_one(ProjectTree)
        scroll_y = tree.scroll_offset.y
        tree.reload()
        tree.scroll_to(y=scroll_y, animate=False)
        self._file_index = None

    def _load_diff_async(self) -> None:
        target = self._diff_target()
        if target is None or self.project_root is None:
            return
        root = self.project_root
        self.run_worker(
            lambda: self._diff_worker(root, target),
            thread=True,
            exclusive=True,
            group="diff",
        )

    def _diff_worker(self, root: Path, target: GitChange) -> None:
        worker = get_current_worker()
        try:
            parsed = load_diff(root, target)
            error = None
        except GitCommandError as exc:
            parsed = None
            error = str(exc)
        if worker.is_cancelled:
            return
        self.call_from_thread(self._apply_diff, parsed, error, target)

    def _open_quick_open(self) -> None:
        if self.project_root is None:
            return
        if self._file_index is None:
            self._file_index = list_relative_files(self.project_root)
        paths = self._file_index
        self.call_from_thread(self.push_screen, QuickOpenScreen(paths), self._open_quick_result)

    def _open_quick_result(self, relative: str | None) -> None:
        if relative and self.project_root is not None:
            self._show_diff(False)
            self.open_path(self.project_root / relative)

    def _open_search_result(self, result: tuple[str, int] | None) -> None:
        if result is None or self.project_root is None:
            return
        relative, line = result
        self._show_diff(False)
        self.open_path(self.project_root / relative, line=line)

    def _show_diff(self, visible: bool) -> None:
        self.diff_visible = visible
        self.query_one("#file-viewer").display = not visible
        diff = self.query_one(DiffView)
        diff.display = visible
        if self.current_file is not None and self.project_root is not None:
            self._update_path_bar(self.current_file, relative_display(self.current_file, self.project_root))
        if visible:
            diff.focus()

    def _update_path_bar(self, path: Path, relative: str) -> None:
        prefix = "Diff  " if self.diff_visible else ""
        self.query_one("#path-bar", Static).update(f"{prefix}{path.name}  {relative}")

    def _change_for_option(self, option_id: str | None) -> GitChange | None:
        if option_id is None:
            return None
        return self.query_one(ChangesList).entries.get(option_id)

    def _review_change(self, relative: str) -> GitChange:
        matches = [change for change in self.changes if change.path == relative]
        untracked = next((change for change in matches if change.group == "untracked"), None)
        if untracked is not None and all(change.group == "untracked" for change in matches):
            return untracked
        return GitChange("review", "M", relative)

    def _diff_target(self) -> GitChange | None:
        if self.selected_change is not None:
            return self.selected_change
        if self.current_file is None or self.project_root is None:
            return None
        return self._review_change(relative_display(self.current_file, self.project_root))

    def _start_watcher(self) -> None:
        if self.project_root is None:
            return
        watcher = ProjectWatcher(self.project_root, self._emit_refresh)
        try:
            watcher.start()
        except OSError as exc:
            self.notify(f"File watching is unavailable: {exc}", severity="warning")
            return
        self._watcher = watcher

    def _stop_watcher(self) -> None:
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None

    def _emit_refresh(self, structure_changed: bool) -> None:
        try:
            self.post_message(RefreshSignal(structure_changed))
        except Exception:
            return


# 1000 is click and wheel reporting; 1002 adds motion while a button is held,
# so a scrollbar thumb can be dragged. Never send 1000l/1002l/1003l here: any
# of them turns mouse tracking off, and Herdr then stops delivering clicks.
# 1016 (pixel mouse) stays off so a click stays on the cell under the pointer.
_CELL_MOUSE = "\x1b[?1000h\x1b[?1002h\x1b[?1006h\x1b[?1016l"
DEBUG_INPUT_FLAG = Path(__file__).resolve().parents[1] / ".debug-input"
DEBUG_INPUT_LOG = Path("/tmp/android-explorer-input.log")


def _mouse_byte(character: str) -> int:
    """Return the raw byte for an X10 mouse character.

    Bytes 128-255 arrive as surrogate code points when stdin is decoded with
    ``surrogateescape``.
    """

    value = ord(character)
    if 0xDC00 <= value <= 0xDCFF:
        return value - 0xDC00
    return value


def debug_input(line: str) -> None:
    if not DEBUG_INPUT_FLAG.exists():
        return
    with DEBUG_INPUT_LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def install_cell_mouse() -> None:
    """Use cell coordinates for clicks.

    Textual also turns on pixel mouse tracking. Herdr answers that request,
    and the click then lands on the wrong row, so folders and files never
    receive it. Keyboard input does not use this channel.
    """
    import textual.drivers.linux_driver as linux_driver
    from textual._xterm_parser import XTermParser

    class _CellMouseParser(XTermParser):
        def feed(self, data: str):  # type: ignore[no-untyped-def]
            debug_input(f"raw {data!r}")
            return super().feed(data)

        @property
        def mouse_pixels(self) -> bool:  # type: ignore[override]
            return False

        @mouse_pixels.setter
        def mouse_pixels(self, value: bool) -> None:
            return

        def parse_mouse_code(self, code: str):  # type: ignore[no-untyped-def]
            decoded = super().parse_mouse_code(code)
            if decoded is not None:
                return decoded
            # Herdr reports clicks as ESC [ M Cb Cx Cy, with each byte
            # offset by 32. Textual only decodes the newer SGR form.
            if not code.startswith("\x1b[M") or len(code) != 6:
                return None
            from textual import events

            info = _mouse_byte(code[3]) - 32
            column = _mouse_byte(code[4]) - 32
            row = _mouse_byte(code[5]) - 32
            if column < 1 or row < 1:
                return None
            x = float(column - 1)
            y = float(row - 1)
            shift = bool(info & 4)
            meta = bool(info & 8)
            ctrl = bool(info & 16)
            button_code = info & 3
            if info & 64:
                # 64 up, 65 down, 66 left, 67 right. Herdr sends the sideways ones too.
                event_class = (
                    events.MouseScrollUp,
                    events.MouseScrollDown,
                    events.MouseScrollLeft,
                    events.MouseScrollRight,
                )[button_code]
                button = 0
            elif info & 32:
                event_class = events.MouseMove
                button = 0 if button_code == 3 else button_code + 1
            elif button_code == 3:
                event_class = events.MouseUp
                button = 1
            else:
                event_class = events.MouseDown
                button = button_code + 1
            delta_x = int(x) - int(self.last_x)
            delta_y = int(y) - int(self.last_y)
            self.last_x = x
            self.last_y = y
            return event_class(
                None,
                x,
                y,
                delta_x,
                delta_y,
                button,
                shift,
                meta,
                ctrl,
                screen_x=x,
                screen_y=y,
            )

    def _enable_mouse_support(self) -> None:
        if not self._mouse:
            return
        self.write(_CELL_MOUSE)
        self.flush()
        debug_input(f"enabled mouse reporting {_CELL_MOUSE!r}")

    def _ignore(self) -> None:
        return

    # X10 mouse columns past 95 are bytes 128-255. Textual decodes stdin as
    # strict UTF-8, so those bytes raise and the input thread closes the pane.
    original_decoder = linux_driver.getincrementaldecoder

    def _getincrementaldecoder(encoding: str):  # type: ignore[no-untyped-def]
        if encoding.lower() in {"utf-8", "utf8"}:
            decoder = original_decoder(encoding)

            def factory(errors: str = "surrogateescape"):  # type: ignore[no-untyped-def]
                return decoder(errors=errors)

            return factory
        return original_decoder(encoding)

    linux_driver.getincrementaldecoder = _getincrementaldecoder  # type: ignore[assignment]
    linux_driver.XTermParser = _CellMouseParser  # type: ignore[misc]
    linux_driver.LinuxDriver._enable_mouse_support = _enable_mouse_support  # type: ignore[method-assign]
    linux_driver.LinuxDriver._enable_mouse_pixels = _ignore  # type: ignore[method-assign]
    linux_driver.LinuxDriver._enable_in_band_window_resize = _ignore  # type: ignore[method-assign]
    linux_driver.LinuxDriver._query_in_band_window_resize = _ignore  # type: ignore[method-assign]


def main(argv: list[str] | None = None) -> int:
    install_cell_mouse()
    args = list(sys.argv[1:] if argv is None else argv)
    explicit: Path | None = None
    if args:
        explicit = Path(args[0]).expanduser()
        if not explicit.exists() or not explicit.is_dir():
            print(f"Not a directory: {explicit}", file=sys.stderr)
            return 2
        explicit = find_project_root(explicit)
    AndroidExplorerApp(root=explicit).run()
    return 0
