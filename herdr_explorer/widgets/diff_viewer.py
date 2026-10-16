"""One scrollable side-by-side diff."""

from __future__ import annotations

from rich.cells import cell_len, set_cell_size
from rich.segment import Segment
from rich.style import Style

from textual.events import MouseDown, MouseMove, MouseUp
from textual.geometry import Size
from textual.strip import Strip
from textual.widget import Widget

from herdr_explorer.core.git import DiffRow, ParsedDiff

# Explicit hex colors. Named "white" can collapse into the green background.
_DELETE = Style(color="#fff1f2", bgcolor="#7f1d1d")
_DELETE_EMPTY = Style(color="#fecaca", bgcolor="#451a1a")
_ADD = Style(color="#f0fdf4", bgcolor="#14532d")
_ADD_EMPTY = Style(color="#bbf7d0", bgcolor="#052e16")
_HUNK = Style(color="#67e8f9", bold=True)
_HUNK_CURRENT = Style(color="#083344", bgcolor="#67e8f9", bold=True)
_DIVIDER = Style(color="#a1a1aa")
_PLAIN = Style()
_MESSAGE = Style(color="#e4e4e7", italic=True)


class DiffView(Widget):
    """Side-by-side diff. Both columns live on the same row, so they scroll together."""

    can_focus = True

    def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kwargs)
        self.rows: list[DiffRow] = []
        self.hunk_rows: list[int] = []
        self.hunk_index = 0
        self._gutter = 4
        self._column_width = 40
        self._pan_x: int | None = None

    def set_diff(self, parsed: ParsedDiff) -> None:
        self.rows = list(parsed.rows)
        self.hunk_rows = list(parsed.hunk_rows)
        self.hunk_index = 0
        numbers = [
            number
            for row in self.rows
            for number in (row.left_no, row.right_no)
            if number is not None
        ]
        self._gutter = max(4, len(str(max(numbers))) if numbers else 4)
        widest = 1
        for row in self.rows:
            if row.kind == "hunk":
                widest = max(widest, cell_len(row.left))
            else:
                widest = max(widest, cell_len(self._marked(row, "left")))
                widest = max(widest, cell_len(self._marked(row, "right")))
        self._column_width = max(24, widest)
        self._sync_size()
        self.scroll_to(0, 0, animate=False)
        self.refresh()

    def next_hunk(self) -> None:
        self._move_hunk(1)

    def previous_hunk(self) -> None:
        self._move_hunk(-1)

    def _move_hunk(self, step: int) -> None:
        if not self.hunk_rows:
            return
        self.hunk_index = (self.hunk_index + step) % len(self.hunk_rows)
        self.scroll_to(y=self.hunk_rows[self.hunk_index], animate=False)
        self.refresh()

    def on_mouse_down(self, event: MouseDown) -> None:
        if event.button != 1:
            return
        self._pan_x = event.screen_x
        self.capture_mouse()

    def on_mouse_move(self, event: MouseMove) -> None:
        if event.button != 1 or self._pan_x is None:
            return
        delta_x = event.screen_x - self._pan_x
        if not delta_x:
            return
        self.scroll_relative(x=-delta_x, animate=False, immediate=True)
        self._pan_x = event.screen_x

    def on_mouse_up(self, event: MouseUp) -> None:
        del event
        self._pan_x = None
        self.release_mouse()

    def on_resize(self, event) -> None:  # type: ignore[no-untyped-def]
        del event
        self._sync_size()

    def _sync_size(self) -> None:
        # Horizontal scroll moves through a long line. It must not push the
        # right column off the screen, or an added line is only a green block.
        visible = self._visible_width()
        span = self._column_span()
        extra = max(0, self._column_width - span)
        width = max(visible, span * 2 + 1, 1) + extra
        height = max(len(self.rows), 1)
        size = Size(width, height)
        if self.virtual_size != size:
            self.virtual_size = size

    def _visible_width(self) -> int:
        width = self.scrollable_content_region.width
        if width <= 0:
            width = self.size.width
        return max(width, 0)

    def _column_span(self) -> int:
        visible = self._visible_width()
        if visible <= 1:
            return self._column_width
        return max(12, (visible - 1) // 2)

    def _marked(self, row: DiffRow, side: str) -> str:
        if side == "left":
            number, text = row.left_no, row.left
            marker = "-" if row.kind in {"delete", "change"} else " "
        else:
            number, text = row.right_no, row.right
            marker = "+" if row.kind in {"add", "change"} else " "
        gutter = " " * self._gutter if number is None else f"{number:>{self._gutter}}"
        return f"{gutter} {marker} {text}"

    def render_line(self, y: int) -> Strip:
        row_index = self.scroll_offset.y + y
        visible = self._visible_width()
        scroll_x = self.scroll_offset.x
        if visible <= 0:
            return Strip.blank(1, _PLAIN)
        if row_index < 0 or row_index >= len(self.rows):
            return Strip.blank(visible, _PLAIN)
        row = self.rows[row_index]
        if row.kind in {"hunk", "message"}:
            style = _HUNK if row.kind == "hunk" else _MESSAGE
            if row.kind == "hunk" and self.hunk_rows and row_index == self.hunk_rows[self.hunk_index]:
                style = _HUNK_CURRENT
            shown = _window(row.left, scroll_x, visible)
            return _with_styles(Strip([Segment(shown, style)]).crop_extend(0, visible, style), visible)

        if row.kind == "add":
            left_style, right_style = _ADD_EMPTY, _ADD
        elif row.kind == "delete":
            left_style, right_style = _DELETE, _DELETE_EMPTY
        elif row.kind == "change":
            left_style, right_style = _DELETE, _ADD
        else:
            left_style = right_style = _PLAIN
        span = self._column_span()
        left = _window(self._marked(row, "left"), scroll_x, span)
        right = _window(self._marked(row, "right"), scroll_x, span)
        strip = Strip(
            [
                Segment(left, left_style),
                Segment("│", _DIVIDER),
                Segment(right, right_style),
            ]
        )
        return _with_styles(strip.crop_extend(0, visible, _PLAIN), visible)


def _window(text: str, scroll: int, width: int) -> str:
    """Return `width` cells of `text`, starting `scroll` cells in."""

    if width <= 0:
        return ""
    if scroll > 0:
        text = _drop_cells(text, scroll)
    return set_cell_size(text, width)


def _drop_cells(text: str, cells: int) -> str:
    remaining = cells
    for index, character in enumerate(text):
        size = cell_len(character)
        if size > remaining:
            return text[index:]
        remaining -= size
        if remaining == 0:
            return text[index + 1 :]
    return ""


def _with_styles(strip: Strip, width: int) -> Strip:
    """Give every segment a style so monochrome terminals can render the diff."""

    segments = [
        Segment(segment.text, segment.style or _PLAIN, segment.control)
        for segment in strip
        if segment.text or segment.control
    ]
    if not segments:
        return Strip.blank(width, _PLAIN)
    return Strip(segments)
