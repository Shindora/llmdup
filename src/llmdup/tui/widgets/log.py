# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""A log pane with the same movement keys and filter as the tables."""

from collections.abc import Iterable
from typing import ClassVar

from rich.text import Text
from textual.binding import BindingType
from textual.containers import VerticalScroll
from textual.widgets import RichLog

from llmdup.tui import keys
from llmdup.tui.widgets.table import RowFilter


class ViewLog(RichLog, inherit_bindings=False):
    """A log that keeps its lines, so it can filter, wrap and redraw them."""

    BINDINGS: ClassVar[list[BindingType]] = keys.bindings(keys.Scope.LIST)

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(min_width=1, wrap=False, markup=False, highlight=False, auto_scroll=True, id=id)
        self.entries: list[Text] = []
        self.row_filter = RowFilter()

    @property
    def kept(self) -> list[Text]:
        """The lines the filter keeps."""
        return [line for line in self.entries if self.row_filter.matches((line.plain,))]

    def set_lines(self, lines: Iterable[Text]) -> None:
        """Replace every line."""
        self.entries = list(lines)
        self.redraw()

    def add_lines(self, lines: Iterable[Text]) -> None:
        """Append lines, drawing the ones the filter keeps."""
        for line in lines:
            self.entries.append(line)
            if self.row_filter.matches((line.plain,)):
                self.write(self.row_filter.mark(line))

    def set_filter(self, row_filter: RowFilter) -> None:
        """Filter the lines."""
        self.row_filter = row_filter
        self.redraw()

    def toggle_wrap(self) -> bool:
        """Turn wrapping on or off and return the new setting."""
        self.wrap = not self.wrap
        self.redraw()
        return self.wrap

    def redraw(self) -> None:
        """Draw the kept lines again."""
        self.clear()
        for line in self.kept:
            self.write(self.row_filter.mark(line))

    def action_cursor_down(self) -> None:
        """Scroll one line down."""
        self.scroll_down(animate=False)

    def action_cursor_up(self) -> None:
        """Scroll one line up."""
        self.scroll_up(animate=False)

    def action_scroll_top(self) -> None:
        """Scroll to the first line."""
        self.scroll_home(animate=False)

    def action_scroll_bottom(self) -> None:
        """Scroll to the last line."""
        self.scroll_end(animate=False)

    def action_page_down(self) -> None:
        """Scroll one page down."""
        self.scroll_page_down(animate=False)

    def action_page_up(self) -> None:
        """Scroll one page up."""
        self.scroll_page_up(animate=False)


class ScrollPane(VerticalScroll, inherit_bindings=False):
    """A scrolling pane with the movement keys of the tables."""

    BINDINGS: ClassVar[list[BindingType]] = keys.bindings(keys.Scope.LIST)

    def action_cursor_down(self) -> None:
        """Scroll one line down."""
        self.scroll_down(animate=False)

    def action_cursor_up(self) -> None:
        """Scroll one line up."""
        self.scroll_up(animate=False)

    def action_scroll_top(self) -> None:
        """Scroll to the top."""
        self.scroll_home(animate=False)

    def action_scroll_bottom(self) -> None:
        """Scroll to the bottom."""
        self.scroll_end(animate=False)

    def action_page_down(self) -> None:
        """Scroll one page down."""
        self.scroll_page_down(animate=False)

    def action_page_up(self) -> None:
        """Scroll one page up."""
        self.scroll_page_up(animate=False)
