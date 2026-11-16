# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""The deploy view: step progress on the left, the live log on the right."""

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal, Vertical
from textual.timer import Timer
from textual.widgets import Static

from llmdup.core.models import Action, RunEvent
from llmdup.tui import keys, theme
from llmdup.tui.keys import View
from llmdup.tui.nav import DEPLOY_LOG, ViewRef
from llmdup.tui.run import DeployRun, LogEntry, LogKind, RowState
from llmdup.tui.screens.base import Flash, ViewScreen, pane_title, view_title
from llmdup.tui.screens.dialogs import TextDialog
from llmdup.tui.widgets.crumbs import Level
from llmdup.tui.widgets.log import ViewLog
from llmdup.tui.widgets.table import Column, Row, ViewTable, cell

STATE_COLOURS = {
    RowState.NEXT: theme.DIM,
    RowState.NOW: theme.ACCENT,
    RowState.DONE: theme.OK,
    RowState.FAILED: theme.FAIL,
    RowState.STOPPED: theme.FAIL,
}
KIND_STYLES = {LogKind.COMMAND: theme.TEXT, LogKind.OUTPUT: theme.DIM, LogKind.LLMDUP: theme.TEXT, LogKind.ERROR: theme.FAIL}
PREFIXES = {LogKind.COMMAND: ("$ ", theme.KEY), LogKind.LLMDUP: ("llmdup ", theme.ACCENT), LogKind.ERROR: ("llmdup ", theme.FAIL)}
EMPTY = "No deploy has run in this session. Press u on a guide or a plan to start one."


def duration(seconds: float | None) -> str:
    """Format seconds as `4s`, `3m12s` or `1h02m`."""
    if seconds is None:
        return ""
    whole = int(seconds)
    if whole < 60:
        return f"{whole}s"
    if whole < 3600:
        return f"{whole // 60}m{whole % 60:02d}s"
    return f"{whole // 3600}h{whole % 3600 // 60:02d}m"


def log_text(entry: LogEntry, *, timestamps: bool = False) -> Text:
    """Draw one log line: commands after `$`, llmdup's own lines after `llmdup`."""
    text = Text()
    if timestamps:
        text.append(entry.at.strftime("%H:%M:%S "), style=theme.DIM)
    if (prefix := PREFIXES.get(entry.kind)) is not None:
        text.append(*prefix)
    text.append(entry.text, style=KIND_STYLES[entry.kind])
    return text


def run_rows(run: DeployRun) -> list[Row]:
    """One row per step of the run."""
    return [
        Row(
            key=r.path,
            cells=(
                cell(r.state, STATE_COLOURS[r.state]),
                cell(r.path, theme.DIM if r.state is RowState.NEXT else ""),
                cell(duration(run.row_seconds(r)), theme.DIM),
            ),
            name=r.path,
            status=r.state,
        )
        for r in run.rows
    ]


def run_status(run: DeployRun) -> Flash:
    """Where the run is, or how it ended."""
    if run.error:
        return run.error, Level.ERROR
    if run.result is not None:
        return run.result.message, Level.OK if run.result.ok else Level.ERROR
    if not run.running:
        return f"{run.verb} cancelled", Level.WARN
    current = run.current()
    where = f"step {run.rows.index(current) + 1} of {len(run.rows)}" if current else "starting"
    return f"{where} · {duration(run.elapsed())} elapsed", Level.INFO


class DeployScreen(ViewScreen):
    """Follow the running deploy, test or tear down; leaving the view does not stop it."""

    VIEW = View.DEPLOY
    BINDINGS: ClassVar[list[BindingType]] = keys.bindings(keys.Scope.SCREEN, View.DEPLOY)

    def __init__(self, ref: ViewRef, *, message: Flash | None = None) -> None:
        super().__init__(ref, message=message)
        self.followed: DeployRun | None = None
        self.shown = 0
        self.timer: Timer | None = None

    def body(self) -> ComposeResult:
        """Steps on the left, log on the right."""
        with Horizontal(id="panes"):
            with Vertical(id="main", classes="box"):
                yield ViewTable([Column("state", "STATE"), Column("step", "STEP", flex=True), Column("time", "TIME")], id="list")
                yield Static(Text("Leaving this view does not stop the deploy.", style=theme.DIM), id="note")
            with Vertical(id="log-box", classes="box"):
                yield ViewLog(id="log")

    @property
    def steps(self) -> ViewTable:
        """The steps table."""
        return self.query_one("#list", ViewTable)

    @property
    def log_view(self) -> ViewLog:
        """The log pane."""
        return self.query_one("#log", ViewLog)

    def on_mount(self) -> None:
        """Listen to the app's runs."""
        super().on_mount()
        self.tui.run_listeners.append(self.follow)
        self.timer = self.set_interval(1, self._tick)

    def on_unmount(self) -> None:
        """Stop listening; the run goes on."""
        if self.follow in self.tui.run_listeners:
            self.tui.run_listeners.remove(self.follow)

    async def fetch(self) -> None:
        """Follow the app's current run."""
        self.follow(None)

    def follow(self, event: RunEvent | None) -> None:
        """Draw what changed in the run, switching to a newer run when one starts."""
        del event
        run = self.tui.deploy_run
        if run is not self.followed:
            self.followed, self.shown = run, 0
            self.log_view.set_lines([] if run is not None else [Text(EMPTY, style=theme.DIM)])
        if run is None:
            self.redraw_titles()
            return
        self.steps.set_rows(run_rows(run))
        new = run.log[self.shown :]
        self.shown = len(run.log)
        self.log_view.add_lines(log_text(e) for e in new)
        if self.log_view.auto_scroll and (current := run.current()) is not None:
            self.steps.select_key(current.path)
        self.redraw_titles()
        self.redraw_crumbs()

    def _tick(self) -> None:
        if self.followed is not None and self.followed.running:
            self.steps.set_rows(run_rows(self.followed))
            self.redraw_crumbs()

    def title_text(self) -> Text:
        """`deploy(<guide>)[done/total]`."""
        run = self.followed
        if run is None:
            return view_title("deploy", None, None, self.row_filter)
        return view_title(run.verb, run.plan.guide, f"{run.done}/{len(run.rows)}", self.row_filter)

    def redraw_titles(self) -> None:
        """Title the steps and the log pane."""
        super().redraw_titles()
        self.query_one("#log-box").border_title = pane_title("log", "autoscroll" if self.log_view.auto_scroll else "paused")

    def crumbs(self) -> list[str]:
        """Guides or stacks, then the guide or namespace, then the verb."""
        run = self.followed
        if run is None:
            return ["deploy"]
        if run.plan.action is Action.UP:
            return ["guides", run.plan.guide, run.verb]
        return ["stacks", run.plan.namespace, run.verb]

    def status(self) -> Flash:
        """Where the run is."""
        return run_status(self.followed) if self.followed else ("", Level.INFO)

    def action_cancel_run(self) -> None:
        """Stop the run at the current step."""
        self.tui.cancel_run()

    def action_logs(self) -> None:
        """Open the full log."""
        self.tui.open(ViewRef(View.LOGS, DEPLOY_LOG))

    def action_autoscroll(self) -> None:
        """Follow the log and the running step, or stop following."""
        self.log_view.auto_scroll = not self.log_view.auto_scroll
        self.redraw_titles()
        self.flash(f"autoscroll {'on' if self.log_view.auto_scroll else 'off'}")

    def action_open(self) -> None:
        """Show the output of the selected step."""
        current = self.steps.current
        if self.followed is None or current is None:
            return
        lines = [log_text(e) for e in self.followed.lines_of(current.key)]
        body = Text("\n").join(lines) if lines else Text("no output yet", style=theme.DIM)
        self.app.push_screen(TextDialog(f"output {current.key}", body))

    def action_next_pane(self) -> None:
        """Switch between the steps and the log."""
        (self.log_view if self.steps.has_focus else self.steps).focus()

    def copy_text(self) -> str | None:
        """Copy the whole log."""
        return "\n".join(e.text for e in self.followed.log) if self.followed else None
