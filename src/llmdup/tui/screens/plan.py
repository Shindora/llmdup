# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""The plan view: variables, the ordered steps, and the command of the selected step."""

from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Static

from llmdup.core.models import Choices, LlmdupError, Plan, PlanVariable, Profile, Step, StepTag, VariableSource
from llmdup.core.planner import FLAG_VARIABLES, secret_status
from llmdup.tui import keys, theme
from llmdup.tui.keys import View
from llmdup.tui.nav import ViewRef
from llmdup.tui.screens.base import Flash, ViewScreen, pane_title, view_title
from llmdup.tui.screens.dialogs import EditDialog, TextDialog
from llmdup.tui.widgets.crumbs import Level
from llmdup.tui.widgets.table import Column, Row, ViewTable, cell

CHOICE_FIELDS = {name: flag for flag, name in FLAG_VARIABLES.items()}
MODE = "MODE"
HF = "HF_TOKEN"
HF_STEP = "prerequisites.secrets"
SHOWN_SOURCES = {VariableSource.DETECTED, VariableSource.SUPPORT, VariableSource.PROFILE, VariableSource.FLAG}


def value_cell(name: str, value: str | None, source: VariableSource | None, allowed: list[str] | None, *, sensitive: bool) -> Text:
    """Draw a value, where it came from, and the other allowed values."""
    if sensitive:
        state = secret_status(name)
        return Text.assemble((state, theme.OK if state == "set" else theme.FAIL), (" (never shown)", theme.DIM), no_wrap=True)
    text = Text(value or "", no_wrap=True, overflow="ellipsis")
    if source in SHOWN_SOURCES:
        text.append(f" ({source})", style=theme.DIM)
    others = [v for v in allowed or [] if v != value]
    if others:
        text.append(" · " + " · ".join(others[:3]) + (" …" if len(others) > 3 else ""), style=theme.DIM)
    return text


def variable_rows(plan: Plan, modes: list[str]) -> list[Row]:
    """One row per variable, with the endpoint mode after NAMESPACE."""
    rows = []
    for v in plan.variables:
        rows.append(
            Row(key=v.name, cells=(cell(v.name), value_cell(v.name, v.value, v.source, v.values, sensitive=v.sensitive)), name=v.name)
        )
        if v.name == "NAMESPACE" and plan.mode:
            rows.append(Row(key=MODE, cells=(cell(MODE), value_cell(MODE, plan.mode, None, modes, sensitive=False)), name=MODE))
    if any(s.path == HF_STEP for s in plan.steps) and all(v.name != HF for v in plan.variables):
        rows.append(Row(key=HF, cells=(cell(HF), value_cell(HF, None, None, None, sensitive=True)), name=HF))
    return rows


def step_scope(step: Step) -> Text:
    """Where a step acts: the whole cluster, the namespace, or the local profile."""
    if StepTag.CLUSTER_SCOPED in step.tags:
        return cell("cluster", theme.ACCENT)
    if StepTag.LOCAL in step.tags:
        return cell("local", theme.KEY)
    return cell("namespace")


def step_note(step: Step, plan: Plan) -> Text:
    """Why a step needs attention."""
    if step.path in plan.needs_confirmation:
        return cell("asks first", theme.ACCENT)
    if StepTag.SENDS_TRAFFIC in step.tags:
        return cell("traffic", theme.DIM)
    return cell("")


def step_rows(plan: Plan) -> list[Row]:
    """One row per step, in order."""
    return [
        Row(key=s.path, cells=(cell(str(i)), cell(s.path), step_scope(s), step_note(s, plan)), name=s.path, status=step_scope(s).plain)
        for i, s in enumerate(plan.steps, 1)
    ]


def command_text(plan: Plan, step: Step) -> Text:
    """The command of one step, as rendered from guide.yaml."""
    if StepTag.LOCAL in step.tags:
        origin = "llmdup's local profile"
    elif StepTag.GATEWAY_PROVIDER in step.tags:
        origin = "llmdup: GKE rejects the guide's provider.name=none route"
    elif StepTag.GATEWAY_ROUTE in step.tags:
        origin = "llmdup: GKE routes a minute or two after the Gateway is programmed"
    else:
        origin = f"guide.yaml at llm-d {plan.llmd_ref}, nothing added"
    return Text.assemble((f"# rendered from {origin}\n", theme.DIM), step.command)


class PlanScreen(ViewScreen):
    """Plan a deploy: edit values, read each step's command, save the script, deploy."""

    VIEW = View.PLAN
    BINDINGS: ClassVar[list[BindingType]] = keys.bindings(keys.Scope.SCREEN, View.PLAN)

    def __init__(self, ref: ViewRef, *, message: Flash | None = None) -> None:
        super().__init__(ref, message=message)
        self.guide = ref.arg or ""
        self.choices: Choices | None = None
        self.plan: Plan | None = None
        self.modes: list[str] = []

    def body(self) -> ComposeResult:
        """Variables and steps side by side, the selected command below."""
        with Horizontal(id="panes"):
            with Vertical(id="vars-box", classes="box"):
                yield ViewTable([Column("name", "NAME"), Column("value", "VALUE", flex=True)], id="vars")
            with Vertical(id="main", classes="box"):
                yield ViewTable(
                    [Column("n", "#", min_width=2), Column("step", "STEP", flex=True), Column("scope", "SCOPE"), Column("note", "NOTE")],
                    id="list",
                )
        with VerticalScroll(id="command-box", classes="box"):
            yield Static(id="command")

    @property
    def steps(self) -> ViewTable:
        """The steps table."""
        return self.query_one("#list", ViewTable)

    @property
    def variables(self) -> ViewTable:
        """The variables table."""
        return self.query_one("#vars", ViewTable)

    async def fetch(self) -> None:
        """Build the plan from the guide and the current choices."""
        try:
            facts = await self.tui.ensure_facts()
            self.choices = self.choices or self.tui.core.choices(self.guide, facts)
            self.modes = self.tui.core.info(self.guide).modes
            self.plan = self.tui.core.plan(self.choices, facts)
        except LlmdupError as error:
            self.flash(f"{error}{f' ({error.hint})' if error.hint else ''}", Level.ERROR)
            return
        self.show_plan(self.plan)

    def show_plan(self, plan: Plan) -> None:
        """Draw variables, steps and the selected command."""
        self.variables.set_rows(variable_rows(plan, self.modes))
        self.steps.set_rows(step_rows(plan))
        self.show_command()
        self.redraw_titles()
        self.redraw_crumbs()

    def selected_step(self) -> Step | None:
        """The step under the cursor."""
        current = self.steps.current
        if self.plan is None or current is None:
            return None
        return next((s for s in self.plan.steps if s.path == current.key), None)

    def show_command(self) -> None:
        """Draw the command of the selected step."""
        step = self.selected_step()
        box = self.query_one("#command-box")
        box.border_title = pane_title("command", step.path if step else None)
        self.query_one("#command", Static).update(command_text(self.plan, step) if self.plan and step else Text())

    @on(DataTable.RowHighlighted, "#list")
    def _step_highlighted(self) -> None:
        self.show_command()

    def title_text(self) -> Text:
        """`plan(<guide>)[steps]`."""
        return view_title("plan", self.guide, str(len(self.steps.kept)), self.row_filter)

    def redraw_titles(self) -> None:
        """Title the steps and the variables pane."""
        super().redraw_titles()
        self.query_one("#vars-box").border_title = pane_title("variables", str(len(self.variables.kept)))

    def crumbs(self) -> list[str]:
        """Guides, the guide, plan."""
        return ["guides", self.guide, "plan"]

    def status(self) -> Flash:
        """How many steps, and how many ask first."""
        if self.plan is None:
            return "planning…", Level.INFO
        asks = len(self.plan.needs_confirmation)
        profile = " · local profile (simulator)" if self.plan.profile is Profile.LOCAL else ""
        return (
            f"{len(self.plan.steps)} steps · {asks} cluster-scoped step{'s' * (asks != 1)} ask{'s' * (asks == 1)} before running{profile}",
            Level.INFO,
        )

    def copy_text(self) -> str | None:
        """Copy the selected step's command, or the selected variable's value."""
        if self.variables.has_focus:
            current = self.variables.current
            variable = self._variable(current.key) if current else None
            return None if variable is None or variable.sensitive else variable.value
        step = self.selected_step()
        return step.command if step else None

    def _variable(self, name: str) -> PlanVariable | None:
        return next((v for v in self.plan.variables if v.name == name), None) if self.plan else None

    def action_next_pane(self) -> None:
        """Switch between variables and steps."""
        (self.steps if self.variables.has_focus else self.variables).focus()

    def action_open(self) -> None:
        """Show the selected command, or edit the selected variable."""
        if self.variables.has_focus:
            self.action_edit()
            return
        step = self.selected_step()
        if self.plan is not None and step is not None:
            self.app.push_screen(TextDialog(f"command {step.path}", command_text(self.plan, step)))

    def action_edit(self) -> None:
        """Edit the selected variable and plan again."""
        current = self.variables.current
        if not self.variables.has_focus or current is None or self.plan is None:
            self.flash("select a variable first: tab to the variables pane", Level.WARN)
            return
        if current.key == MODE:
            self.app.push_screen(EditDialog(MODE, self.plan.mode or "", self.modes), self._edited(MODE))
            return
        variable = self._variable(current.key)
        if current.key == HF or (variable is not None and variable.sensitive):
            self.flash(f"{current.key} is read from the environment only", Level.WARN)
            return
        if variable is None:
            return
        self.app.push_screen(EditDialog(variable.name, variable.value or "", variable.values or []), self._edited(variable.name))

    def _edited(self, name: str) -> Callable[[str | None], None]:
        def apply(value: str | None) -> None:
            if value is None or self.choices is None:
                return
            self.replan(name, value)

        return apply

    def replan(self, name: str, value: str) -> None:
        """Change one choice and build the plan again; a value the guide refuses keeps the old plan."""
        if self.choices is None:
            return
        if name == MODE:
            choices = self.choices.model_copy(update={"mode": value})
        elif name in CHOICE_FIELDS:
            choices = self.choices.model_copy(update={CHOICE_FIELDS[name]: value})
        else:
            choices = self.choices.model_copy(update={"sets": {**self.choices.sets, name: value}})
        try:
            plan = self.tui.core.plan(choices, self.tui.facts)
        except LlmdupError as error:
            self.flash(f"{error}{f' ({error.hint})' if error.hint else ''}", Level.ERROR)
            return
        self.choices, self.plan = choices, plan
        self.show_plan(plan)
        self.flash(f"{name}={value}", Level.OK)
        self.variables.select_key(name)

    def action_save(self) -> None:
        """Write the plan as one bash script to the current directory."""
        if self.plan is None:
            return
        path = self.tui.core.save(Path(f"llmdup-{self.guide}.sh"), self.plan.script)
        self.flash(f"saved {path}", Level.OK)

    def action_deploy(self) -> None:
        """Deploy with these choices."""
        if self.choices is not None:
            self.tui.deploy(self.guide, self.choices)
