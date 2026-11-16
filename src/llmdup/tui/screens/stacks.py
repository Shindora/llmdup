# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""The stacks view, one row per deployed stack, and the stack view with its pods, endpoint and last test."""

from datetime import datetime
from functools import partial
from typing import ClassVar

import yaml
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import BindingType
from textual.containers import Vertical
from textual.widgets import Static

from llmdup import __version__
from llmdup.core.models import Action, LlmdupError, PodStatus, RunEvent, StackStatus, StatusReport
from llmdup.tui import keys, theme
from llmdup.tui.keys import View
from llmdup.tui.nav import ViewRef
from llmdup.tui.rows import row
from llmdup.tui.run import DeployRun, LogKind
from llmdup.tui.screens.base import Flash, ViewScreen, pane_title, view_title
from llmdup.tui.screens.deploy import duration
from llmdup.tui.screens.dialogs import TextDialog
from llmdup.tui.widgets.crumbs import Level
from llmdup.tui.widgets.table import Column, Row, RowFilter, ViewTable, cell

HEALTH_COLOURS = {"ready": theme.OK, "not ready": theme.ACCENT, "partial": theme.ACCENT, "failed": theme.FAIL}


def age(updated_at: str | None, now: datetime) -> str:
    """How long ago a stack's state was written."""
    if not updated_at:
        return "-"
    try:
        then = datetime.fromisoformat(updated_at)
    except ValueError:
        return "-"
    return duration(max((now - then).total_seconds(), 0))


def stack_row(stack: StackStatus, now: datetime) -> Row:
    """One row of the stacks view."""
    ready = sum(p.ready for p in stack.pods)
    cells = (
        cell(stack.namespace),
        cell(stack.guide),
        cell(stack.mode or "-"),
        cell(stack.health, HEALTH_COLOURS.get(stack.health, theme.DIM)),
        cell(f"{ready}/{len(stack.pods)}"),
        cell(stack.endpoint or "-", theme.DIM),
        cell(age(stack.updated_at, now), theme.DIM),
    )
    return Row(key=stack.namespace, cells=cells, name=stack.namespace, status=stack.health, age=stack.updated_at or "")


class StackActions(ViewScreen):
    """The keys the stacks view and the stack view share: logs, describe, test, metrics, port-forward, tear down."""

    VIEW = View.STACKS
    BINDINGS: ClassVar[list[BindingType]] = keys.bindings(keys.Scope.SCREEN, View.STACKS)

    def __init__(self, ref: ViewRef, *, message: Flash | None = None) -> None:
        super().__init__(ref, message=message)
        self.report: StatusReport | None = None

    def selected_stack(self) -> StackStatus | None:
        """The stack the keys act on."""
        return None

    def selected_pod(self) -> PodStatus | None:
        """The pod `l` opens: the selected one, else the stack's first model server."""
        stack = self.selected_stack()
        if stack is None or not stack.pods:
            return None
        return next((p for p in stack.pods if p.role == "model server"), stack.pods[0])

    async def read_status(self) -> StatusReport | None:
        """Ask the cluster for the stacks; a failure is shown, not taken as nothing deployed."""
        try:
            self.report = await self.tui.core.status(self.tui.namespace if self.ref.arg is None else self.ref.arg)
        except LlmdupError as error:
            self.flash(f"cannot read the stacks: {error}", Level.ERROR)
            return None
        self.tui.known_namespaces = sorted({*self.tui.known_namespaces, *(s.namespace for s in self.report.stacks)})
        return self.report

    def _need_stack(self) -> StackStatus | None:
        stack = self.selected_stack()
        if stack is None:
            self.flash("no stack selected", Level.WARN)
        return stack

    def action_logs(self) -> None:
        """Open the log of the selected pod."""
        stack = self._need_stack()
        pod = self.selected_pod()
        if stack is not None and pod is None:
            self.flash("this stack has no pods", Level.WARN)
        if stack is not None and pod is not None:
            self.tui.open(ViewRef(View.LOGS, f"{stack.namespace}/{pod.name}"))

    def action_describe(self) -> None:
        """Show everything llmdup knows about the stack."""
        if (stack := self._need_stack()) is not None:
            body = yaml.safe_dump(stack.model_dump(mode="json"), sort_keys=False)
            self.app.push_screen(TextDialog(f"stack {stack.namespace}", body))

    def action_test(self) -> None:
        """Send the guide's test request again."""
        if (stack := self._need_stack()) is not None:
            self.tui.test(stack.namespace)

    def action_metrics(self) -> None:
        """Show the model servers' metrics."""
        if (stack := self._need_stack()) is not None:
            self.run_worker(partial(self._metrics, stack.namespace), group="metrics", exclusive=True, exit_on_error=False)

    async def _metrics(self, namespace: str) -> None:
        self.flash("reading metrics…")
        report = await self.tui.core.metrics(namespace)
        body = Text()
        for pod in report.pods:
            body.append(f"{pod.pod}\n", style=f"bold {theme.KEY}")
            body.append(
                "\n".join(pod.lines) if pod.lines else (pod.error or "no vllm: or sglang: metrics"), style="" if pod.lines else theme.DIM
            )
            body.append("\n\n")
        self.clear_flash()
        self.app.push_screen(TextDialog(f"metrics {namespace}", body if report.pods else Text("no model-server pods", style=theme.DIM)))

    def action_port_forward(self) -> None:
        """Copy the port-forward command that reaches the endpoint from this machine."""
        stack = self._need_stack()
        if stack is None:
            return
        if not stack.port_forward:
            self.flash("this stack has no port-forward command (gateway mode, or no endpoint yet)", Level.WARN)
            return
        self.app.copy_to_clipboard(stack.port_forward)
        self.flash(f"copied: {stack.port_forward}", Level.OK)

    def action_teardown(self) -> None:
        """Tear the stack down, after showing the exact commands."""
        if (stack := self._need_stack()) is not None:
            self.tui.teardown(stack.namespace)


class StacksScreen(StackActions):
    """One row per stack llmdup deployed."""

    def body(self) -> ComposeResult:
        """The stacks table."""
        with Vertical(id="main", classes="box"):
            yield ViewTable(
                [
                    Column("namespace", "NAMESPACE"),
                    Column("guide", "GUIDE"),
                    Column("mode", "MODE"),
                    Column("health", "HEALTH"),
                    Column("pods", "PODS"),
                    Column("endpoint", "ENDPOINT", flex=True),
                    Column("age", "AGE"),
                ],
                id="list",
            )

    @property
    def table(self) -> ViewTable:
        """The stacks table."""
        return self.query_one("#list", ViewTable)

    async def fetch(self) -> None:
        """Read the stacks."""
        report = await self.read_status()
        if report is None:
            return
        now = self.tui.core.now()
        self.table.set_rows(stack_row(s, now) for s in report.stacks)
        if not report.stacks:
            self.flash("nothing deployed by llmdup", Level.INFO)
        else:
            self.clear_flash()
        self.redraw_titles()

    def title_text(self) -> Text:
        """`stacks(<namespace>)[count]`."""
        return view_title("stacks", self.tui.namespace or "all", str(len(self.table.kept)), self.row_filter)

    def selected_stack(self) -> StackStatus | None:
        """The stack under the cursor."""
        current = self.table.current
        if self.report is None or current is None:
            return None
        return next((s for s in self.report.stacks if s.namespace == current.key), None)

    def action_open(self) -> None:
        """Open the selected stack."""
        if (stack := self._need_stack()) is not None:
            self.tui.open(ViewRef(View.STACKS, stack.namespace))


def info_grid(stack: StackStatus) -> Table:
    """The stack's guide, endpoint and state, in two columns."""
    ready = sum(p.ready for p in stack.pods)
    mark = "✓" if stack.health == "ready" else "!"
    reach = "inside the cluster · press <shift-f> to copy a port-forward" if stack.port_forward else "-"
    rows = [
        ("Guide", Text(stack.guide), "Endpoint", Text(stack.endpoint or "-")),
        ("Mode", Text(stack.mode or "-"), "Reachable", Text(reach)),
        (
            "Profile",
            Text(stack.profile),
            "State",
            Text(f"{mark} {stack.health} · {ready} of {len(stack.pods)} pods ready", HEALTH_COLOURS.get(stack.health, theme.DIM)),
        ),
        (
            "Release",
            Text(stack.release.name if stack.release else "none"),
            "Deployed by",
            Text(f"llmdup {__version__} from llm-d {stack.llmd_ref}"),
        ),
    ]
    grid = Table.grid(padding=(0, 2), expand=True)
    grid.add_column(no_wrap=True, min_width=11)
    grid.add_column(no_wrap=True, overflow="ellipsis", min_width=max(len(r[1]) for r in rows))
    grid.add_column(no_wrap=True, min_width=11)
    grid.add_column(no_wrap=True, overflow="ellipsis", ratio=1)
    for a, b, c, d in rows:
        grid.add_row(Text(a, style=theme.DIM), b, Text(c, style=theme.DIM), d)
    if stack.failed_step:
        grid.add_row(Text("Failed step", style=theme.DIM), Text(stack.failed_step, style=theme.FAIL), Text(), Text())
    return grid


def test_text(run: DeployRun | None, namespace: str) -> Text:
    """The last test request of this session, or how to send one."""
    if run is None or run.plan.action is not Action.TEST or run.plan.namespace != namespace:
        return Text.assemble(
            ("No test request in this session. ", theme.DIM), ("<t>", theme.KEY), (" sends the guide's test again.", theme.DIM)
        )
    text = Text()
    if run.running:
        current = run.current()
        text.append(f"▶ running {current.path if current else 'the test'}…\n", style=theme.ACCENT)
    elif run.ok:
        text.append("✓ the guide's tests passed through the router\n", style=theme.OK)
    else:
        text.append(f"✗ {run.error or (run.result.message if run.result else 'cancelled')}\n", style=theme.FAIL)
    output = [e.text for e in run.log if e.kind is LogKind.OUTPUT][-4:]
    text.append("\n".join(output), style=theme.DIM)
    return text


class StackScreen(StackActions):
    """One stack: what it runs, its pods, and its last test request."""

    def body(self) -> ComposeResult:
        """Info, pods and test panes."""
        with Vertical(id="info-box", classes="box"):
            yield Static(id="info")
        with Vertical(id="main", classes="box"):
            yield ViewTable(
                [
                    Column("name", "NAME", flex=True),
                    Column("role", "ROLE"),
                    Column("ready", "READY"),
                    Column("status", "STATUS"),
                    Column("restarts", "RESTARTS"),
                ],
                id="list",
            )
        with Vertical(id="test-box", classes="box"):
            yield Static(id="test")

    @property
    def namespace(self) -> str:
        """The stack's namespace."""
        return self.ref.arg or ""

    @property
    def table(self) -> ViewTable:
        """The pods table."""
        return self.query_one("#list", ViewTable)

    def on_mount(self) -> None:
        """Listen to test runs as well."""
        super().on_mount()
        self.tui.run_listeners.append(self.follow)

    def on_unmount(self) -> None:
        """Stop listening."""
        if self.follow in self.tui.run_listeners:
            self.tui.run_listeners.remove(self.follow)

    def follow(self, event: RunEvent | None) -> None:
        """Draw the test pane again; reload when a run ends."""
        del event
        run = self.tui.deploy_run
        self.query_one("#test", Static).update(test_text(run, self.namespace))
        if run is not None and not run.running:
            self.load()

    async def fetch(self) -> None:
        """Read the stack."""
        self.query_one("#test", Static).update(test_text(self.tui.deploy_run, self.namespace))
        report = await self.read_status()
        if report is None:
            return
        stack = self.selected_stack()
        if stack is None:
            self.query_one("#info", Static).update(Text(f"llmdup has no stack in {self.namespace} now.", style=theme.DIM))
            self.table.set_rows([])
        else:
            self.query_one("#info", Static).update(info_grid(stack))
            self.table.set_rows(row(p) for p in stack.pods)
        self.redraw_titles()

    def selected_stack(self) -> StackStatus | None:
        """This view's stack."""
        return next((s for s in self.report.stacks if s.namespace == self.namespace), None) if self.report else None

    def selected_pod(self) -> PodStatus | None:
        """The pod under the cursor."""
        stack = self.selected_stack()
        current = self.table.current
        if stack is None or current is None:
            return super().selected_pod()
        return next((p for p in stack.pods if p.name == current.key), None)

    def title_text(self) -> Text:
        """`pods[count]`, under the stack's title."""
        return view_title("pods", None, str(len(self.table.kept)), self.row_filter)

    def redraw_titles(self) -> None:
        """Title the three panes."""
        super().redraw_titles()
        self.query_one("#info-box").border_title = view_title("stack", self.namespace, None, RowFilter())
        run = self.tui.deploy_run
        test = run.plan.steps[0].path if run is not None and run.plan.action is Action.TEST and run.plan.steps else "verify.tests"
        self.query_one("#test-box").border_title = pane_title("test", test)

    def crumbs(self) -> list[str]:
        """Stacks, then the namespace."""
        return ["stacks", self.namespace]

    def status(self) -> Flash:
        """The stack's health."""
        stack = self.selected_stack()
        if stack is None:
            return "", Level.INFO
        if stack.health == "ready":
            return "endpoint is ready", Level.OK
        return f"stack is {stack.health}", Level.WARN

    def action_open(self) -> None:
        """Open the log of the selected pod."""
        self.action_logs()

    def copy_text(self) -> str | None:
        """Copy the endpoint."""
        stack = self.selected_stack()
        return stack.endpoint if stack else None
