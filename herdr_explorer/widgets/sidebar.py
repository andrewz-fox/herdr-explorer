"""File tree and Git change list."""

from __future__ import annotations

from textual import events
from textual.widgets import DirectoryTree, OptionList
from textual.widgets.option_list import Option
from rich.text import Text

from herdr_explorer.core.git import GROUP_LABELS, GROUP_ORDER, GitChange, change_label
from herdr_explorer.core.project import IGNORED_DIRECTORY_NAMES, is_hidden_name


class ProjectTree(DirectoryTree):
    """Lazy directory tree that hides generated and hidden paths."""

    ICON_NODE = "▶ "
    ICON_NODE_EXPANDED = "▼ "

    def on_mount(self) -> None:
        # Selecting a row should not toggle expansion. Clicks and Enter do that
        # explicitly, so a double-click cannot open a folder and close it again.
        self.auto_expand = False
        self._pressed_line: int | None = None

    def filter_paths(self, paths):  # type: ignore[no-untyped-def]
        return [
            path
            for path in paths
            if not is_hidden_name(path.name) and path.name not in IGNORED_DIRECTORY_NAMES
        ]

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button != 1:
            return
        self._pressed_line = self._line_from_pointer(event)
        self._open_line(self._pressed_line, chain=1)

    async def _on_click(self, event: events.Click) -> None:
        # MouseDown already acted on this row. A second click in the same
        # double-click should leave a folder open. prevent_default stops the
        # stock tree handler from selecting the row and expanding it again.
        event.prevent_default()
        event.stop()
        line = self._line_from_pointer(event)
        if event.chain >= 2:
            node = self.get_node_at_line(line) if line is not None else None
            if node is not None and node.allow_expand and not node.is_expanded:
                node.expand()
            return
        if line is not None and line != self._pressed_line:
            self._open_line(line, chain=1)

    def on_directory_tree_directory_selected(
        self, event: DirectoryTree.DirectorySelected
    ) -> None:
        if event.node.allow_expand and not event.node.is_expanded:
            event.node.expand()

    def _line_from_pointer(self, event: events.MouseEvent) -> int | None:
        meta_line = event.style.meta.get("line")
        if isinstance(meta_line, int):
            return meta_line
        line = event.y + self.scroll_offset.y
        if line < 0 or line >= len(self._tree_lines):
            return None
        return line

    def _open_line(self, line: int | None, *, chain: int) -> None:
        if line is None:
            return
        node = self.get_node_at_line(line)
        if node is None or node.data is None:
            return
        self.cursor_line = line
        if node.allow_expand:
            if chain >= 2:
                if not node.is_expanded:
                    node.expand()
                return
            self._toggle_node(node)
            return
        if chain < 2:
            self.post_message(self.FileSelected(node, node.data.path))


class ChangesList(OptionList):
    """Grouped, read-only Git status."""

    def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kwargs)
        self.entries: dict[str, GitChange] = {}

    def set_changes(self, changes: list[GitChange], error: str | None = None) -> None:
        selected = self.highlighted
        selected_id = None
        if selected is not None and 0 <= selected < self.option_count:
            selected_id = self.get_option_at_index(selected).id
        self.entries.clear()
        self.clear_options()
        if error:
            self.add_option(Option(error, disabled=True))
            return
        if not changes:
            self.add_option(Option("No changes", disabled=True))
            return
        restore_at: int | None = None
        for group in GROUP_ORDER:
            grouped = [change for change in changes if change.group == group]
            if not grouped:
                continue
            self.add_option(Option(Text(GROUP_LABELS[group], style="bold"), disabled=True))
            for change in grouped:
                if change.key == selected_id:
                    restore_at = self.option_count
                self.entries[change.key] = change
                self.add_option(Option(_styled_label(change), id=change.key))
        if restore_at is not None:
            self.highlighted = restore_at


def _styled_label(change: GitChange) -> Text:
    styles = {
        "M": "yellow",
        "A": "green",
        "D": "red",
        "R": "cyan",
        "C": "cyan",
        "U": "magenta",
        "?": "green",
    }
    return Text(change_label(change), style=styles.get(change.status, ""))
