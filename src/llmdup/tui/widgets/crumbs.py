# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""The breadcrumb line with a right-aligned flash message."""

from collections.abc import Sequence
from enum import StrEnum

from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from llmdup.tui import theme


class Level(StrEnum):
    """How a flash message is coloured."""

    INFO = theme.DIM
    OK = theme.OK
    WARN = theme.ACCENT
    ERROR = theme.FAIL


def crumbs_text(crumbs: Sequence[str]) -> Text:
    """Draw crumbs as blocks: parents in the key colour, the current view in the accent colour."""
    text = Text(no_wrap=True, overflow="ellipsis")
    for i, crumb in enumerate(crumbs):
        colour = theme.ACCENT if i == len(crumbs) - 1 else theme.KEY
        text.append(f" {crumb} ", style=f"bold {theme.BACKGROUND} on {colour}")
        text.append(" ")
    return text


class Crumbs(Static):
    """Where you are on the left, the last message on the right."""

    def show(self, crumbs: Sequence[str], message: str, level: Level) -> None:
        """Draw the crumbs and the message."""
        grid = Table.grid(expand=True)
        grid.add_column(no_wrap=True)
        grid.add_column(justify="right", no_wrap=True, overflow="ellipsis", ratio=1)
        grid.add_row(crumbs_text(crumbs), Text(message, style=str(level)))
        self.update(grid)
