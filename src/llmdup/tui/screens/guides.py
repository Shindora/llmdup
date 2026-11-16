# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""The guides view: every guide of the release, its support for this cluster, and whether it is deployed."""

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Vertical

from llmdup.core.models import GuideInfo, GuideSummary, LlmdupError
from llmdup.tui import keys, theme
from llmdup.tui.keys import View
from llmdup.tui.nav import ViewRef
from llmdup.tui.rows import row
from llmdup.tui.screens.base import Flash, ViewScreen, view_title
from llmdup.tui.screens.dialogs import TextDialog
from llmdup.tui.widgets.crumbs import Level
from llmdup.tui.widgets.table import Column, ViewTable


def describe(info: GuideInfo) -> Text:
    """Draw what `llmdup guides show` prints."""
    text = Text()
    text.append(f"{info.title}\n", style=f"bold {theme.TEXT}")
    text.append(f"section {info.section} · dir {info.dir} · modes {', '.join(info.modes) or '-'}\n\n", style=theme.DIM)
    text.append("VARIABLES\n", style=f"bold {theme.ACCENT}")
    for v in info.variables:
        value = "<from the environment>" if v.sensitive else (v.default or "")
        text.append(f"  {v.name:<20}", style=theme.KEY)
        text.append(f"{value}")
        text.append(f"  {', '.join(v.values)}\n" if v.values else "\n", style=theme.DIM)
    if info.support:
        text.append("\nSUPPORT\n", style=f"bold {theme.ACCENT}")
        for a in info.support.accelerators:
            cells = ", ".join(f"{e} {s}" for e, s in a.engines.items())
            text.append(f"  {a.name:<20}", style=theme.KEY)
            text.append(f"{cells}\n")
    text.append("\nSTEPS\n", style=f"bold {theme.ACCENT}")
    for s in info.steps:
        text.append(f"  {s.path:<36}", style=theme.KEY)
        text.append(f"{', '.join(s.tags)}\n", style=theme.DIM)
    return text


class GuidesScreen(ViewScreen):
    """Guides by section; `enter` plans one, `u` deploys it."""

    VIEW = View.GUIDES
    BINDINGS: ClassVar[list[BindingType]] = keys.bindings(keys.Scope.SCREEN, View.GUIDES)

    def __init__(self, ref: ViewRef, *, message: Flash | None = None) -> None:
        super().__init__(ref, message=message)
        self.summaries: list[GuideSummary] = []
        self.section: str | None = None
        self.reading = True

    def body(self) -> ComposeResult:
        """The guide table."""
        with Vertical(id="main", classes="box"):
            yield ViewTable(
                [
                    Column("guide", "GUIDE", min_width=12),
                    Column("title", "WHAT IT DOES", flex=True, min_width=16),
                    Column("modes", "MODES"),
                    Column("support", "SUPPORT"),
                    Column("deployed", "DEPLOYED"),
                ],
                id="list",
            )

    @property
    def table(self) -> ViewTable:
        """The guide table."""
        return self.query_one("#list", ViewTable)

    @property
    def sections(self) -> list[str]:
        """Sections in index order."""
        return list(dict.fromkeys(s.section for s in self.summaries))

    async def fetch(self) -> None:
        """Read the guides and which of them are deployed."""
        self.reading = True
        try:
            facts = await self.tui.ensure_facts()
            self.summaries = await self.tui.core.guides(facts)
        except LlmdupError as error:
            self.flash(str(error), Level.ERROR)
            return
        finally:
            self.reading = False
        self.tui.guide_names = [s.name for s in self.summaries]
        self.show_rows()

    def show_rows(self) -> None:
        """Draw the guides of the chosen section."""
        rows = [s for s in self.summaries if self.section in {None, s.section}]
        table = self.table
        support = next((f"{s.accelerator} + {s.engine}" for s in rows if s.accelerator and s.engine), "support")
        table.specs = (*table.specs[:3], Column("support", support.upper()), table.specs[4])
        table.set_rows(row(s) for s in rows)
        self.redraw_titles()
        self.redraw_crumbs()

    def title_text(self) -> Text:
        """`guides(<section>)[count]` and the filter."""
        return view_title("guides", self.section or "all", str(len(self.table.kept)), self.row_filter)

    def status(self) -> Flash:
        """How many guides the release and the section hold."""
        if self.reading:
            return "reading the guides…", Level.INFO
        total = f"{len(self.summaries)} guides in llm-d {self.tui.core.ref}"
        if self.section is None:
            return total, Level.INFO
        return f"{total} · {sum(s.section == self.section for s in self.summaries)} in {self.section}", Level.INFO

    def selected(self) -> str | None:
        """The guide under the cursor."""
        current = self.table.current
        if current is None:
            self.flash("no guide selected", Level.WARN)
        return current.key if current else None

    def copy_text(self) -> str | None:
        """Copy the guide name."""
        return self.selected()

    def action_open(self) -> None:
        """Plan the selected guide."""
        if guide := self.selected():
            self.tui.open(ViewRef(View.PLAN, guide))

    def action_describe(self) -> None:
        """Describe the selected guide."""
        if not (guide := self.selected()):
            return
        try:
            info = self.tui.core.info(guide)
        except LlmdupError as error:
            self.flash(str(error), Level.ERROR)
            return
        self.app.push_screen(TextDialog(f"guide {guide}", describe(info)))

    def action_doctor(self) -> None:
        """Run the preflight checks for the selected guide."""
        if guide := self.selected():
            self.tui.open(ViewRef(View.DOCTOR, guide))

    def action_deploy(self) -> None:
        """Deploy the selected guide with its default choices."""
        if guide := self.selected():
            self.tui.deploy_guide(guide)

    def action_next_pane(self) -> None:
        """Show the next section."""
        order: list[str | None] = [None, *self.sections]
        self.section = order[(order.index(self.section) + 1) % len(order)]
        self.show_rows()
