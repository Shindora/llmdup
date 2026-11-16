# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""A k9s-style table: filter as you type, sort by name, status or age, vi movement keys."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import ClassVar

from rich.style import Style
from rich.text import Text
from textual.binding import BindingType
from textual.fuzzy import Matcher
from textual.widgets import DataTable

from llmdup.tui import keys, theme

SORT_FIELDS = ("name", "status", "age")
MATCH = Style(color=theme.BACKGROUND, bgcolor=theme.KEY)


@dataclass(frozen=True)
class RowFilter:
    """A filter typed after `/`: `text`, `!text` to invert, or `-f text` for a fuzzy match."""

    raw: str = ""

    @property
    def invert(self) -> bool:
        """Whether rows that match are hidden."""
        return self.raw.startswith("!")

    @property
    def fuzzy(self) -> bool:
        """Whether the match is fuzzy."""
        return self.raw.startswith("-f ") or self.raw == "-f"

    @property
    def text(self) -> str:
        """The text to look for, without the `!` or `-f` prefix."""
        if self.invert:
            return self.raw[1:].strip()
        if self.fuzzy:
            return self.raw[2:].strip()
        return self.raw.strip()

    @property
    def active(self) -> bool:
        """Whether the filter hides anything."""
        return bool(self.text)

    def matches(self, cells: Iterable[str]) -> bool:
        """Whether a row with these cells is shown."""
        if not self.active:
            return True
        haystack = " ".join(cells)
        if self.fuzzy:
            return Matcher(self.text).match(haystack) > 0
        found = self.text.casefold() in haystack.casefold()
        return found != self.invert

    def mark(self, cell: Text) -> Text:
        """Highlight the filter text inside a cell."""
        if not self.active or self.invert or self.fuzzy:
            return cell
        marked = cell.copy()
        marked.highlight_words([self.text], MATCH, case_sensitive=False)
        return marked


@dataclass(frozen=True)
class Column:
    """One column; the `flex` column takes the width the others leave."""

    key: str
    label: str
    flex: bool = False
    min_width: int = 8
    max_width: int = 60


@dataclass(frozen=True)
class Row:
    """One row with the values the sort keys use."""

    key: str
    cells: tuple[Text, ...]
    name: str = ""
    status: str = ""
    age: str = ""

    @property
    def plain(self) -> tuple[str, ...]:
        """The cells as plain text, for the filter."""
        return tuple(c.plain for c in self.cells)


def cell(value: object, style: str = "") -> Text:
    """Return a one-line table cell that ends in an ellipsis when it does not fit."""
    return Text(str(value), style=style, no_wrap=True, overflow="ellipsis", end="")


class ViewTable(DataTable[Text], inherit_bindings=False):
    """The main table of a view."""

    BINDINGS: ClassVar[list[BindingType]] = keys.bindings(keys.Scope.LIST)

    def __init__(self, columns: Sequence[Column], *, id: str | None = None) -> None:
        super().__init__(cursor_type="row", show_row_labels=False, cell_padding=1, id=id)
        self.specs = tuple(columns)
        self.source: list[Row] = []
        self.row_filter = RowFilter()
        self.order: tuple[str, bool] | None = None
        self._width = 0

    @property
    def kept(self) -> list[Row]:
        """The rows the filter keeps, in sort order."""
        rows = [r for r in self.source if self.row_filter.matches(r.plain)]
        if self.order is not None:
            field, reverse = self.order
            rows.sort(key=lambda r: getattr(r, field), reverse=reverse)
        return rows

    @property
    def current(self) -> Row | None:
        """The row under the cursor."""
        rows = self.kept
        return rows[self.cursor_row] if 0 <= self.cursor_row < len(rows) else None

    def set_rows(self, rows: Iterable[Row]) -> None:
        """Replace the rows, keeping the cursor on the same key."""
        self.source = list(rows)
        self.rebuild()

    def set_filter(self, row_filter: RowFilter) -> None:
        """Filter the rows."""
        self.row_filter = row_filter
        self.rebuild()

    def sort_by(self, field: str) -> bool:
        """Sort by a field; again on the same field reverses. False when no row has that field."""
        if not any(getattr(r, field) for r in self.source):
            return False
        reverse = self.order == (field, False)
        self.order = (field, reverse)
        self.rebuild()
        return True

    def select_key(self, key: str) -> None:
        """Move the cursor to the row with this key, if it is visible."""
        index = next((i for i, r in enumerate(self.kept) if r.key == key), None)
        if index is not None:
            self.move_cursor(row=index)

    def rebuild(self) -> None:
        """Redraw columns and rows from the source rows, the filter and the sort."""
        keep = self.current.key if self.current else None
        rows = self.kept
        self.clear(columns=True)
        for column, width in zip(self.specs, self._widths(rows), strict=True):
            self.add_column(Text(column.label, style=f"bold {theme.DIM}"), width=width, key=column.key)
        for row in rows:
            self.add_row(*(self.row_filter.mark(c) for c in row.cells), key=row.key)
        if keep is not None:
            self.select_key(keep)

    def _widths(self, rows: list[Row]) -> list[int]:
        widths = [
            min(c.max_width, max([len(c.label), *(r.cells[i].cell_len for r in rows if i < len(r.cells))]))
            for i, c in enumerate(self.specs)
        ]
        flex = next((i for i, c in enumerate(self.specs) if c.flex), None)
        scrollbar = self.styles.scrollbar_size_vertical if len(rows) + 1 > self.size.height else 0
        available = self.size.width - scrollbar
        if flex is None or available <= 0:
            return widths
        others = sum(w for i, w in enumerate(widths) if i != flex) + 2 * self.cell_padding * len(widths)
        widths[flex] = max(self.specs[flex].min_width, min(widths[flex], available - others))
        return widths

    def on_resize(self) -> None:
        """Fit the flex column to the new width."""
        if self.size.width != self._width:
            self._width = self.size.width
            self.rebuild()
