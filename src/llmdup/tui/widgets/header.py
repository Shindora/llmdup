# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""The header: cluster facts, the key hints of the view, and the logo."""

from collections.abc import Sequence

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from llmdup import __version__
from llmdup.tui import theme

HINT_ROWS = 5
HINT_COLUMNS = 3


def facts_grid(facts: Sequence[tuple[str, str]]) -> Table:
    """Lay out label and value pairs, labels in the accent colour."""
    grid = Table.grid(padding=(0, 1))
    grid.add_column(style=theme.ACCENT, no_wrap=True)
    grid.add_column(no_wrap=True, overflow="ellipsis")
    for label, value in facts:
        grid.add_row(Text(f"{label}:"), Text(value))
    return grid


def hint_text(hint: str) -> Text:
    """Draw `<key> what` with the key in the key colour."""
    key, _, what = hint.partition("> ")
    return Text.assemble((f"{key}>", theme.KEY), " ", what, no_wrap=True, overflow="ellipsis")


def hints_grid(hints: Sequence[str]) -> Table:
    """Lay out key hints in columns of five, filling each column top to bottom."""
    grid = Table.grid(padding=(0, 1), expand=True)
    for _ in range(HINT_COLUMNS):
        grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    shown = list(hints[: HINT_ROWS * HINT_COLUMNS])
    for row in range(HINT_ROWS):
        cells = [shown[c * HINT_ROWS + row] if c * HINT_ROWS + row < len(shown) else "" for c in range(HINT_COLUMNS)]
        if any(cells):
            grid.add_row(*(hint_text(c) if c else Text() for c in cells))
    return grid


class ClusterHeader(Horizontal):
    """Cluster facts on the left, generated key hints in the middle, the logo on the right."""

    def compose(self) -> ComposeResult:
        """Lay out the three parts."""
        yield Static(id="facts")
        yield Static(id="hints")
        yield Static(Text.assemble(("llmdup", f"bold {theme.ACCENT}"), "\n", (__version__, theme.DIM), justify="right"), id="logo")

    def show(self, facts: Sequence[tuple[str, str]], hints: Sequence[str]) -> None:
        """Draw new facts and hints."""
        self.query_one("#facts", Static).update(facts_grid(facts))
        self.query_one("#hints", Static).update(hints_grid(hints))
