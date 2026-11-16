# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""Table rows for core results: guides, check results and pods."""

from functools import singledispatch

from rich.text import Text

from llmdup.core.models import CheckResult, CheckStatus, GuideSummary, PodStatus
from llmdup.tui import theme
from llmdup.tui.widgets.table import Row, cell

SUPPORT_COLOURS = {"validated": theme.OK, "community": theme.ACCENT, "unsupported": theme.FAIL}
CHECK_MARKS = {
    CheckStatus.OK: ("✓ ok", theme.OK),
    CheckStatus.WARN: ("! warn", theme.ACCENT),
    CheckStatus.FAIL: ("✗ fail", theme.FAIL),
    CheckStatus.SKIP: ("- skip", theme.DIM),
}
STATUS_ORDER = {CheckStatus.FAIL: "0", CheckStatus.WARN: "1", CheckStatus.OK: "2", CheckStatus.SKIP: "3"}


def yes_no(*, value: bool | None, yes: str = "yes", no: str = "no") -> Text:
    """Draw a yes or no cell; `None` means unknown."""
    if value is None:
        return cell("-", theme.DIM)
    return cell(yes, theme.OK) if value else cell(no, theme.DIM)


@singledispatch
def row(item: object) -> Row:
    """Turn one core result into a table row.

    Raises:
        NotImplementedError: No row is registered for the item's type.
    """
    msg = f"tui has no row for {type(item).__name__}"
    raise NotImplementedError(msg)


@row.register
def _(item: GuideSummary) -> Row:
    support = item.support or "-"
    style = theme.DIM if support == "unsupported" else ""
    cells = (
        cell(item.name, style),
        cell(item.title, style),
        cell(", ".join(item.modes) or "-", style),
        cell(support, SUPPORT_COLOURS.get(support, theme.DIM)),
        yes_no(value=item.deployed),
    )
    return Row(key=item.name, cells=cells, name=item.name, status=support)


@row.register
def _(item: CheckResult) -> Row:
    mark, colour = CHECK_MARKS[item.status]
    cells = (cell(mark, colour), cell(item.name), cell(item.found), cell(item.fix or "", theme.DIM))
    return Row(key=item.name, cells=cells, name=item.name, status=STATUS_ORDER[item.status])


@row.register
def _(item: PodStatus) -> Row:
    phase = cell(item.phase, theme.OK if item.phase == "Running" and item.ready else theme.ACCENT)
    cells = (cell(item.name), cell(item.role), yes_no(value=item.ready, no="not ready"), phase, cell(str(item.restarts)))
    return Row(key=item.name, cells=cells, name=item.name, status=f"{int(item.ready)}{item.phase}")
