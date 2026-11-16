# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""The help view: every key, command and filter syntax of `keys.py`, grouped."""

from dataclasses import dataclass
from typing import ClassVar

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.widgets import Static

from llmdup.tui import keys, theme
from llmdup.tui.keys import Group, View
from llmdup.tui.screens.base import Flash, ViewScreen, view_title
from llmdup.tui.widgets.crumbs import Level
from llmdup.tui.widgets.log import ScrollPane
from llmdup.tui.widgets.table import RowFilter

COLUMN_GAP = 3
HELP_MARGIN = 8


@dataclass(frozen=True)
class Block:
    """A help section: a heading, optional sub-headings, and key, description pairs."""

    title: str
    entries: tuple[tuple[str, str], ...]
    sub: bool = False

    @property
    def height(self) -> int:
        """Lines the block takes, with the gap after it."""
        return len(self.entries) + 2


def blocks() -> list[Block]:
    """Every help section, generated from `keys.py`."""

    def pairs(group: Group) -> tuple[tuple[str, str], ...]:
        return tuple((k.label, k.description) for k in keys.group_keys(group) if k.view is None)

    out = [
        Block("MOVE", pairs(Group.MOVE)),
        Block("HISTORY", pairs(Group.HISTORY)),
        Block("SEARCH", (*pairs(Group.SEARCH), *((s.label, s.description) for s in keys.SYNTAX))),
        Block("SORT", pairs(Group.SORT)),
        Block("COMMAND PROMPT", pairs(Group.PROMPT)),
        Block("COMMANDS", tuple((c.label, c.description) for c in keys.COMMANDS)),
        Block("GENERAL", pairs(Group.GENERAL)),
        Block("DIALOGS", pairs(Group.DIALOG)),
    ]
    out.extend(Block(view.value, tuple((k.label, k.description) for k in keys.view_keys(view)), sub=True) for view in View)
    return out


def matching(block: Block, row_filter: RowFilter) -> Block:
    """Keep the entries the filter matches."""
    return Block(block.title, tuple(e for e in block.entries if row_filter.matches(e)), block.sub)


def heading(block: Block) -> str:
    """The heading line of a block."""
    return f"ACTIONS IN {block.title.upper()}" if block.sub else block.title


def block_width(block: Block) -> int:
    """Cells a block needs so that nothing in it is cut."""
    labels = max((len(label) for label, _ in block.entries), default=0)
    descriptions = max((len(description) for _, description in block.entries), default=0)
    return max(len(heading(block)), labels + 1 + descriptions)


def block_grid(block: Block) -> Table:
    """Draw one block as key and description columns."""
    grid = Table.grid(padding=(0, 1, 0, 0))
    grid.add_column(no_wrap=True, style=theme.KEY)
    grid.add_column(no_wrap=True)
    grid.add_row(Text(heading(block), style=theme.DIM if block.sub else f"bold {theme.ACCENT}"), "")
    for label, description in block.entries:
        grid.add_row(Text(label), Text(description))
    return grid


def columns(found: list[Block], count: int) -> list[list[Block]]:
    """Pack blocks into columns of about equal height, keeping their order."""
    target = sum(b.height for b in found) / count
    out: list[list[Block]] = [[]]
    height = 0
    for block in found:
        if out[-1] and height + block.height > target + 2 and len(out) < count:
            out.append([])
            height = 0
        out[-1].append(block)
        height += block.height
    return out


def layout(found: list[Block], width: int) -> list[list[Block]]:
    """The most columns, up to four, that fit the width without cutting any text."""
    for count in (4, 3, 2):
        packed = [c for c in columns(found, count) if c]
        needed = sum(max(block_width(b) for b in c) + 1 + COLUMN_GAP for c in packed)
        if needed <= width:
            return packed
    return [found]


def help_table(row_filter: RowFilter, width: int) -> Table:
    """The whole help: the blocks of `keys.py` in as many columns as fit."""
    found = [b for b in (matching(b, row_filter) for b in blocks()) if b.entries]
    table = Table.grid(padding=(0, COLUMN_GAP, 0, 0))
    cells = []
    for column in layout(found, width):
        table.add_column(vertical="top", no_wrap=True)
        stack = Table.grid()
        for block in column:
            stack.add_row(block_grid(block))
            stack.add_row("")
        cells.append(stack)
    table.add_row(*cells)
    return table


class HelpScreen(ViewScreen):
    """Every key, grouped."""

    VIEW = View.HELP
    BINDINGS: ClassVar[list[BindingType]] = keys.bindings(keys.Scope.SCREEN, View.HELP)

    def body(self) -> ComposeResult:
        """The help text in a scrolling box."""
        with ScrollPane(id="main", classes="box"):
            yield Static(id="help")

    def on_mount(self) -> None:
        """Draw the help and focus it."""
        super().on_mount()
        self.draw()
        self.query_one(ScrollPane).focus()

    def on_resize(self) -> None:
        """Repack the columns for the new width."""
        self.draw()

    def draw(self) -> None:
        """Draw the help for the current filter and width."""
        self.query_one("#help", Static).update(help_table(self.row_filter, self.help_width))

    @property
    def help_width(self) -> int:
        """Cells left for the help inside the box, its padding and a scrollbar."""
        return max(self.size.width - HELP_MARGIN, 20)

    def apply_filter(self, raw: str) -> None:
        """Filter the keys."""
        super().apply_filter(raw)
        self.draw()

    def title_text(self) -> Text:
        """`help` and the filter."""
        return view_title("help", None, None, self.row_filter)

    def crumbs(self) -> list[str]:
        """The view help was opened from, then help."""
        last = self.tui.history.last
        return [last.view.value if last else "guides", "help"]

    def status(self) -> Flash:
        """Where the keys come from."""
        return "Keys follow k9s where the meaning is the same.", Level.INFO
