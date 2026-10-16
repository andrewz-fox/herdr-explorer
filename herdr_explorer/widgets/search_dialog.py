"""Quick Open and Search in Files dialogs."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Checkbox, Input, OptionList, Static
from textual.widgets.option_list import Option
from textual.worker import get_current_worker

from herdr_explorer.core.search import SearchHit, fuzzy_match, search_paths


class QuickOpenScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("down", "focus_results", show=False),
    ]

    def __init__(self, paths: list[str]) -> None:
        super().__init__()
        self.paths = paths
        self._visible: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="quick-dialog"):
            yield Input(placeholder="Quick open", id="quick-input")
            yield OptionList(id="quick-results")

    def on_mount(self) -> None:
        self._show("")
        self.query_one("#quick-input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "quick-input":
            self._show(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "quick-input":
            return
        self._choose_highlighted()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            self.dismiss(event.option.id)

    def action_focus_results(self) -> None:
        results = self.query_one("#quick-results", OptionList)
        if results.option_count:
            results.focus()

    def action_close(self) -> None:
        self.dismiss(None)

    def _show(self, query: str) -> None:
        self._visible = fuzzy_match(query, self.paths)
        results = self.query_one("#quick-results", OptionList)
        results.clear_options()
        if not self._visible:
            results.add_option(Option("No matches", disabled=True))
            return
        for path in self._visible:
            results.add_option(Option(path, id=path))
        results.highlighted = 0

    def _choose_highlighted(self) -> None:
        results = self.query_one("#quick-results", OptionList)
        if results.highlighted is None:
            return
        option = results.get_option_at_index(results.highlighted)
        if option.id:
            self.dismiss(option.id)


class SearchScreen(ModalScreen[tuple[str, int] | None]):
    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("down", "focus_results", show=False),
    ]

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self.hits: list[SearchHit] = []
        self._generation = 0
        self._timer = None

    def compose(self) -> ComposeResult:
        with Vertical(id="search-dialog"):
            yield Input(placeholder="Search in files", id="search-input")
            with Horizontal(id="search-options"):
                yield Checkbox("Regex", id="regex")
                yield Checkbox("Case sensitive", id="case")
            yield Static("", id="search-status")
            yield OptionList(id="search-results")

    def on_mount(self) -> None:
        self.query_one("#search-input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search-input":
            self._schedule()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        del event
        self._schedule()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search-input":
            self._choose_highlighted()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is None:
            return
        hit = self.hits[int(event.option.id)]
        self.dismiss((hit.path, hit.line_number))

    def action_focus_results(self) -> None:
        results = self.query_one("#search-results", OptionList)
        if results.option_count:
            results.focus()

    def action_close(self) -> None:
        self.dismiss(None)

    def _schedule(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        self._timer = self.set_timer(0.2, self._start_search)

    def _start_search(self) -> None:
        query = self.query_one("#search-input", Input).value
        regex = self.query_one("#regex", Checkbox).value
        case_sensitive = self.query_one("#case", Checkbox).value
        self._generation += 1
        generation = self._generation
        if not query:
            self._apply_hits(generation, [], "")
            return
        self.query_one("#search-status", Static).update("Searching…")
        self.run_worker(
            lambda: self._search(generation, query, regex, case_sensitive),
            thread=True,
            exclusive=True,
            group="search",
        )

    def _search(self, generation: int, query: str, regex: bool, case_sensitive: bool) -> None:
        worker = get_current_worker()
        error = ""
        hits: list[SearchHit] = []
        try:
            hits = search_paths(self.root, query, regex=regex, case_sensitive=case_sensitive)
        except ValueError as exc:
            error = str(exc)
        if worker.is_cancelled or generation != self._generation:
            return
        self.app.call_from_thread(self._apply_hits, generation, hits, error)

    def _apply_hits(self, generation: int, hits: list[SearchHit], error: str) -> None:
        if generation != self._generation:
            return
        self.hits = hits
        status = self.query_one("#search-status", Static)
        results = self.query_one("#search-results", OptionList)
        results.clear_options()
        if error:
            status.update(error)
            return
        if not hits:
            status.update("No matches")
            results.add_option(Option("No matches", disabled=True))
            return
        status.update(f"{len(hits)} match{'es' if len(hits) != 1 else ''}")
        for index, hit in enumerate(hits):
            label = f"{hit.path}:{hit.line_number}: {hit.text.strip()}"
            results.add_option(Option(label, id=str(index)))
        results.highlighted = 0

    def _choose_highlighted(self) -> None:
        results = self.query_one("#search-results", OptionList)
        if results.highlighted is None:
            return
        option = results.get_option_at_index(results.highlighted)
        if option.id is None or not self.hits:
            return
        hit = self.hits[int(option.id)]
        self.dismiss((hit.path, hit.line_number))
