# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""The doctor view: each preflight check with what was found and how to fix it."""

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Vertical
from textual.widgets import Static

from llmdup.core.models import CheckResult, CheckStatus, DoctorReport, LlmdupError
from llmdup.tui import keys, theme
from llmdup.tui.keys import View
from llmdup.tui.nav import ViewRef
from llmdup.tui.rows import row
from llmdup.tui.screens.base import Flash, ViewScreen, view_title
from llmdup.tui.screens.dialogs import TextDialog
from llmdup.tui.widgets.crumbs import Level
from llmdup.tui.widgets.table import Column, ViewTable


def summary(report: DoctorReport) -> Text:
    """Count passed, warning and blocking checks."""
    counts = {s: sum(c.status is s for c in report.checks) for s in CheckStatus}
    text = Text.assemble(
        (f"{counts[CheckStatus.OK]} passed", theme.OK),
        "   ",
        (f"{counts[CheckStatus.WARN]} warning{'s' * (counts[CheckStatus.WARN] != 1)}", theme.ACCENT),
        "   ",
        (f"{counts[CheckStatus.FAIL]} blocking", theme.FAIL),
        "   ",
    )
    if counts[CheckStatus.FAIL]:
        text.append("Deploy stays locked until the blocking checks pass.", style=theme.DIM)
    else:
        text.append("Nothing blocks a deploy.", style=theme.DIM)
    return text


class DoctorScreen(ViewScreen):
    """Preflight checks for the cluster, or for one guide's plan."""

    VIEW = View.DOCTOR
    BINDINGS: ClassVar[list[BindingType]] = keys.bindings(keys.Scope.SCREEN, View.DOCTOR)

    def __init__(self, ref: ViewRef, *, message: Flash | None = None) -> None:
        super().__init__(ref, message=message)
        self.report: DoctorReport | None = None
        self.fresh = False

    def body(self) -> ComposeResult:
        """The check table and the summary line."""
        with Vertical(id="main", classes="box"):
            yield ViewTable(
                [
                    Column("status", "STATUS"),
                    Column("check", "CHECK"),
                    Column("found", "FOUND", max_width=34),
                    Column("fix", "FIX", flex=True),
                ],
                id="list",
            )
            yield Static(Text("running the checks…", style=theme.DIM), id="summary")

    @property
    def table(self) -> ViewTable:
        """The check table."""
        return self.query_one("#list", ViewTable)

    async def fetch(self) -> None:
        """Run the checks; `ctrl-r` detects the cluster again first."""
        self.query_one("#summary", Static).update(Text("running the checks…", style=theme.DIM))
        try:
            facts = await self.tui.ensure_facts(fresh=self.fresh)
            choices = self.tui.core.choices(self.ref.arg, facts) if self.ref.arg else None
            self.report = await self.tui.core.doctor(choices, facts)
        except LlmdupError as error:
            self.flash(str(error), Level.ERROR)
            return
        self.table.set_rows(row(c) for c in self.report.checks)
        self.query_one("#summary", Static).update(summary(self.report))
        failed = next((c for c in self.report.checks if c.status is CheckStatus.FAIL), None)
        if failed is not None:
            self.flash(f"{failed.name}: {failed.found}", Level.ERROR)
        else:
            self.clear_flash()
        self.redraw_titles()

    def title_text(self) -> Text:
        """`doctor(<guide>)[count]`."""
        return view_title("doctor", self.ref.arg or "cluster", str(len(self.table.kept)), self.row_filter)

    def crumbs(self) -> list[str]:
        """Guides, then doctor."""
        return ["guides", "doctor"]

    def selected(self) -> CheckResult | None:
        """The check under the cursor."""
        current = self.table.current
        if self.report is None or current is None:
            return None
        return next((c for c in self.report.checks if c.name == current.key), None)

    def action_refresh(self) -> None:
        """Detect the cluster again and run every check."""
        self.fresh = True
        self.load()

    def action_open(self) -> None:
        """Show the fix of the selected check."""
        if (check := self.selected()) is None:
            return
        body = Text()
        body.append("found  ", style=theme.DIM)
        body.append(f"{check.found}\n")
        body.append("fix    ", style=theme.DIM)
        body.append(check.fix or "nothing to fix")
        self.app.push_screen(TextDialog(f"{check.name}: {check.status}", body))

    def copy_text(self) -> str | None:
        """Copy the fix of the selected check."""
        check = self.selected()
        return check.fix if check else None
