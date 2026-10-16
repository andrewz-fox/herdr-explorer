"""Read-only source viewer."""

from __future__ import annotations

from rich.segment import Segment
from rich.style import Style

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.strip import Strip
from textual.events import MouseDown, MouseMove, MouseUp
from textual.widgets import Static, TextArea
from textual.widgets._text_area import LanguageDoesNotExist

from herdr_explorer.core.project import FilePayload

_CHANGED_LINE = Style(bgcolor="#14532d")


class HighlightedTextArea(TextArea):
    """Text area that paints a green background on lines changed in the diff."""

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.changed_lines: frozenset[int] = frozenset()
        self._pan_x: int | None = None
        self._pan_y: int | None = None
        self._panning = False

    def render_line(self, y: int) -> Strip:
        strip = super().render_line(y)
        line_index = self.scroll_offset.y + y
        if line_index < 0 or line_index >= self.document.line_count:
            return strip
        if (line_index + 1) not in self.changed_lines:
            return strip
        segments = [
            Segment(
                segment.text,
                (segment.style or Style()) + _CHANGED_LINE,
                segment.control,
            )
            for segment in strip
        ]
        return Strip(segments, strip.cell_length)

    async def _on_mouse_down(self, event: MouseDown) -> None:
        saved = self.scroll_offset
        if event.button == 1:
            self._pan_x = event.screen_x
            self._pan_y = event.screen_y
            self._panning = False
        await super()._on_mouse_down(event)
        # Placing the cursor would jump a horizontally scrolled line back to
        # column 0. Keep the view where it is so a drag can continue from here.
        if event.button == 1:
            self.scroll_to(saved.x, saved.y, animate=False, immediate=True)

    async def _on_mouse_move(self, event: MouseMove) -> None:
        # A sideways drag slides the line. Herdr never sends a sideways swipe,
        # but it does send motion while the button is held.
        if (
            event.button == 1
            and self._pan_x is not None
            and self._pan_y is not None
        ):
            delta_x = event.screen_x - self._pan_x
            delta_y = event.screen_y - self._pan_y
            if not self._panning and abs(delta_x) >= 2 and abs(delta_x) > abs(delta_y):
                self._panning = True
            if self._panning and delta_x:
                self.scroll_relative(x=-delta_x, animate=False, immediate=True)
                self._pan_x = event.screen_x
                self._pan_y = event.screen_y
                return
        await super()._on_mouse_move(event)

    async def _on_mouse_up(self, event: MouseUp) -> None:
        self._pan_x = None
        self._pan_y = None
        self._panning = False
        await super()._on_mouse_up(event)


class FileViewer(Vertical):
    def compose(self) -> ComposeResult:
        yield HighlightedTextArea(
            "",
            read_only=True,
            show_line_numbers=True,
            soft_wrap=False,
            show_cursor=True,
            id="code",
        )
        yield Static("Select a file from the sidebar.", id="file-message")

    def on_mount(self) -> None:
        self.query_one("#code", HighlightedTextArea).display = False

    def set_changed_lines(self, lines: set[int] | frozenset[int]) -> None:
        code = self.query_one("#code", HighlightedTextArea)
        updated = frozenset(lines)
        if code.changed_lines == updated:
            return
        code.changed_lines = updated
        code.refresh()

    def show(self, payload: FilePayload, *, keep_cursor: bool = False) -> None:
        code = self.query_one("#code", HighlightedTextArea)
        message = self.query_one("#file-message", Static)
        if payload.kind != "text":
            code.display = False
            message.display = True
            message.update(payload.message or "Unable to display this file.")
            return
        message.display = False
        code.display = True
        try:
            code.language = payload.language
        except LanguageDoesNotExist:
            code.language = None
        location = code.cursor_location
        scroll = code.scroll_offset
        code.load_text(payload.text)
        if keep_cursor and payload.text:
            lines = payload.text.splitlines() or [""]
            row = min(location[0], len(lines) - 1)
            column = min(location[1], len(lines[row]))
            code.move_cursor((row, column))
            code.scroll_to(scroll.x, scroll.y, animate=False)
        else:
            code.move_cursor((0, 0))

    def reveal_line(self, line_number: int) -> None:
        code = self.query_one("#code", HighlightedTextArea)
        if not code.display:
            return
        row = max(line_number - 1, 0)
        code.focus()
        code.move_cursor((row, 0), center=True)
