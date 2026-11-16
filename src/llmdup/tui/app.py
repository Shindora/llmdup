# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""The llmdup terminal UI: views, history, the `:` commands, and the worker that runs deploys."""

import asyncio
from contextlib import aclosing
from functools import partial
from typing import ClassVar

from textual import on, work
from textual.app import App
from textual.binding import BindingType
from textual.screen import ModalScreen
from textual.worker import Worker, WorkerState

from llmdup.core.models import Action, Choices, ClusterFacts, DoctorFailedError, LlmdupError, RunEvent
from llmdup.core.runner import Prepared
from llmdup.tui import keys, theme
from llmdup.tui.core import Core
from llmdup.tui.keys import View
from llmdup.tui.nav import DEPLOY_LOG, History, ViewRef
from llmdup.tui.run import DeployRun, Listener, render
from llmdup.tui.screens.base import Flash, ViewScreen
from llmdup.tui.screens.deploy import run_status
from llmdup.tui.screens.dialogs import ConfirmDialog, TextDialog
from llmdup.tui.screens.factory import make_screen
from llmdup.tui.widgets.crumbs import Level
from llmdup.tui.widgets.prompt import Mode

VIEW_COMMANDS = {"guides": View.GUIDES, "doctor": View.DOCTOR, "stacks": View.STACKS, "help": View.HELP}

CSS = f"""
Screen {{
    background: {theme.BACKGROUND};
    color: {theme.TEXT};
    padding: 0 1;
}}
ClusterHeader {{ height: 5; }}
#facts {{ width: auto; max-width: 44; margin-right: 2; }}
#hints {{ width: 1fr; }}
#logo {{ width: 7; }}
Prompt {{ height: 3; border: solid {theme.KEY}; padding: 0 1; display: none; }}
#prompt-mode {{ width: 2; }}
#prompt-input {{ width: 1fr; background: {theme.BACKGROUND}; }}
#prompt-hint {{ width: auto; max-width: 48; }}
.box {{ border: solid {theme.BORDER}; border-title-align: center; height: 1fr; background: {theme.BACKGROUND}; }}
Crumbs {{ height: 1; }}
DataTable {{ background: {theme.BACKGROUND}; height: 1fr; }}
DataTable > .datatable--header {{ background: {theme.BACKGROUND}; color: {theme.DIM}; }}
DataTable > .datatable--cursor {{ background: {theme.BORDER}; color: {theme.TEXT}; text-style: none; }}
DataTable:focus > .datatable--cursor {{ background: {theme.ACCENT}; color: {theme.BACKGROUND}; text-style: none; }}
DataTable > .datatable--hover {{ background: {theme.BACKGROUND}; }}
RichLog {{ background: {theme.BACKGROUND}; }}
ScrollPane {{ background: {theme.BACKGROUND}; }}
#panes {{ height: 1fr; }}
PlanScreen #panes {{ height: 3fr; }}
PlanScreen #vars-box {{ width: 40%; }}
PlanScreen #command-box {{ height: 2fr; padding: 0 1; }}
DeployScreen #main {{ width: 47; }}
DeployScreen #log-box {{ padding: 0 1; }}
#note {{ height: auto; padding: 0 1; }}
#summary {{ height: 1; padding: 0 1; }}
StackScreen #info-box {{ height: 6; padding: 0 1; }}
StackScreen #test-box {{ height: 7; padding: 0 1; }}
HelpScreen #main {{ padding: 0 1; }}
.dialog {{ align: center middle; background: {theme.BACKGROUND} 60%; }}
.dialog-box {{
    width: 90%;
    max-width: 110;
    height: auto;
    max-height: 90%;
    border: solid {theme.ACCENT};
    padding: 0 1;
    background: {theme.BACKGROUND};
}}
.dialog-body {{ height: auto; max-height: 22; }}
.dialog-intro {{ margin-bottom: 1; }}
.dialog-keys {{ margin-top: 1; height: 1; }}
.edit-box {{ max-width: 72; }}
#edit-input {{ margin-bottom: 1; background: {theme.BACKGROUND}; }}
"""


class LlmdupApp(App[None]):
    """The k9s-style terminal UI over the llmdup core."""

    CSS = CSS
    TITLE = "llmdup"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS: ClassVar[list[BindingType]] = keys.bindings(keys.Scope.APP)

    def __init__(self, core: Core, *, first: ViewRef | None = None) -> None:
        super().__init__()
        self.core = core
        self.first_view = first or ViewRef(View.GUIDES)
        self.facts: ClusterFacts | None = None
        self.facts_lock = asyncio.Lock()
        self.history = History()
        self.histories: dict[Mode, list[str]] = {}
        self.namespace: str | None = None
        self.show_header = True
        self.deploy_run: DeployRun | None = None
        self.run_task: Worker[None] | None = None
        self.run_listeners: list[Listener] = []
        self.guide_names: list[str] = []
        self.known_contexts: list[str] = []
        self.known_namespaces: list[str] = []

    def on_mount(self) -> None:
        """Apply the theme, open the first view, and read the kubeconfig's contexts."""
        self.register_theme(theme.THEME)
        self.theme = theme.THEME.name
        self.open(self.first_view)
        self.run_worker(self._read_contexts, group="contexts", exit_on_error=False)

    async def _read_contexts(self) -> None:
        self.known_contexts = await self.core.contexts()

    def view_screen(self) -> ViewScreen | None:
        """The top view, below any dialog."""
        return next((s for s in reversed(self.screen_stack) if isinstance(s, ViewScreen)), None)

    def open(self, ref: ViewRef, *, record: bool = True, message: Flash | None = None) -> None:
        """Show a view, recording it in the history."""
        if record:
            self.history.push(ref)
        while isinstance(self.screen, ModalScreen):
            self.pop_screen()
        screen = make_screen(ref, message)
        if isinstance(self.screen, ViewScreen):
            self.switch_screen(screen)
        else:
            self.push_screen(screen)

    def history_back(self) -> None:
        """Show the previous view of the history."""
        if (ref := self.history.back()) is None:
            self.flash("no view behind this one", Level.WARN)
            return
        self.open(ref, record=False)

    def history_forward(self) -> None:
        """Show the next view of the history."""
        if (ref := self.history.forward()) is None:
            self.flash("no view ahead of this one", Level.WARN)
            return
        self.open(ref, record=False)

    def last_view(self) -> None:
        """Toggle to the view shown before this one."""
        if (ref := self.history.last) is None:
            self.flash("no last view yet", Level.WARN)
            return
        self.open(ref)

    def flash(self, message: str, level: Level = Level.INFO) -> None:
        """Show a message on the breadcrumb line of the top view."""
        if (screen := self.view_screen()) is not None:
            screen.flash(message, level)

    def redraw_view(self) -> None:
        """Draw the top view's header and titles again."""
        if (screen := self.view_screen()) is not None:
            screen.redraw()

    async def ensure_facts(self, *, fresh: bool = False) -> ClusterFacts:
        """Detect the cluster once, or again when asked."""
        async with self.facts_lock:
            facts = self.facts if self.facts is not None and not fresh else await self.core.detect()
            self.facts = facts
        self.redraw_view()
        return facts

    def header_facts(self) -> list[tuple[str, str]]:
        """The label and value pairs of the header."""
        facts = self.facts
        if facts is None:
            cluster, accelerator = "detecting…", "detecting…"
        else:
            cluster = f"Kubernetes {facts.server_version}" if facts.reachable else "unreachable"
            accelerator = f"{facts.accelerator} x {facts.accelerator_count}" if facts.accelerator_count else facts.accelerator or "none"
        context = (facts.context if facts else None) or self.core.context or "(current)"
        return [
            ("Context", context),
            ("Cluster", cluster),
            ("llm-d", self.core.ref),
            ("Namespace", self.namespace or "all"),
            ("Accelerator", accelerator),
        ]

    def prompt_arguments(self, command: str) -> list[str]:
        """What a `:` command's argument can be, for the suggestion."""
        match command:
            case "plan" | "doctor" | "deploy":
                return self.guide_names
            case "ctx":
                return self.known_contexts
            case "ns":
                return self.known_namespaces
        return []

    def run_command(self, text: str) -> None:
        """Run one `:` command."""
        name, _, rest = text.strip().removeprefix(":").partition(" ")
        arg = rest.strip() or None
        if name in VIEW_COMMANDS:
            self.open(ViewRef(VIEW_COMMANDS[name], arg if name == "doctor" else None))
            return
        match name:
            case "plan":
                self._plan_command(arg)
            case "deploy":
                self._deploy_command(arg)
            case "logs":
                self.open(ViewRef(View.LOGS, DEPLOY_LOG))
            case "ctx":
                self.switch_context(arg)
            case "ns":
                self.switch_namespace(arg)
            case "q" | "quit":
                self.request_quit()
            case _:
                self.flash(f"unknown command :{name}; ctrl-a lists them", Level.ERROR)

    def _plan_command(self, guide: str | None) -> None:
        if guide is None:
            self.flash("usage: :plan <guide>", Level.WARN)
            return
        self.open(ViewRef(View.PLAN, guide))

    def _deploy_command(self, guide: str | None) -> None:
        if guide is None:
            self.open(ViewRef(View.DEPLOY))
            return
        self.deploy_guide(guide)

    def switch_context(self, name: str | None) -> None:
        """Talk to another kube context, or list them."""
        if not name:
            current = self.facts.context if self.facts else self.core.context
            body = "\n".join(f"{'*' if c == current else ' '} {c}" for c in self.known_contexts) or "no contexts in the kubeconfig"
            self.push_screen(TextDialog("contexts", body))
            return
        self.core.context = name
        self.facts = None
        self.flash(f"context {name}", Level.OK)
        self.reload()

    def switch_namespace(self, name: str | None) -> None:
        """Show one namespace, or all of them."""
        self.namespace = name if name and name != "all" else None
        self.flash(f"namespace {self.namespace or 'all'}", Level.OK)
        self.reload()

    def reload(self) -> None:
        """Load the top view again."""
        if (screen := self.view_screen()) is not None:
            screen.redraw()
            screen.load()

    def notify_run(self, event: RunEvent | None) -> None:
        """Tell the views that follow runs that the run changed."""
        for listener in list(self.run_listeners):
            listener(event)

    def busy(self) -> bool:
        """Whether a run is going; says so on the breadcrumb line."""
        run = self.deploy_run
        if run is not None and run.running:
            self.flash(f"a {run.verb} of {run.plan.guide} is running; wait, or press ctrl-k in the deploy view", Level.WARN)
            return True
        return False

    async def confirmed(self, prepared: Prepared, title: str, intro: str) -> bool:
        """Show the exact commands that need confirmation and wait for the answer."""
        if not prepared.confirm:
            return True
        answer = await self.push_screen_wait(ConfirmDialog(title, intro, prepared.confirm))
        if not answer:
            self.flash("cancelled; nothing ran", Level.WARN)
        return bool(answer)

    def deploy_guide(self, guide: str) -> None:
        """Deploy a guide with its starting choices."""
        self.deploy(guide, None)

    @work(group="prepare", exclusive=True, exit_on_error=False)
    async def deploy(self, guide: str, choices: Choices | None = None) -> None:
        """Gate on the preflight checks, confirm the cluster-scoped steps, then run the deploy."""
        if self.busy():
            return
        self.flash(f"running the preflight checks for {guide}…")
        try:
            choices = choices or self.core.choices(guide, await self.ensure_facts())
            prepared = await self.core.prepare_up(choices)
        except DoctorFailedError as error:
            self.open(ViewRef(View.DOCTOR, guide), message=(f"deploy blocked: {error}", Level.ERROR))
            return
        except LlmdupError as error:
            self.flash(str(error), Level.ERROR)
            return
        intro = "These steps act on the whole cluster, not only the namespace. llmdup runs exactly these commands:"
        if await self.confirmed(prepared, f"deploy {guide}", intro):
            self.start_run(prepared)

    @work(group="prepare", exclusive=True, exit_on_error=False)
    async def teardown(self, namespace: str) -> None:
        """Confirm the exact cleanup commands, then tear the stack down."""
        if self.busy():
            return
        try:
            prepared = await self.core.prepare_recorded(Action.DOWN, namespace)
        except LlmdupError as error:
            self.flash(str(error), Level.ERROR)
            return
        intro = f"Tear down {namespace}: llmdup deletes what the guide created, with exactly these commands:"
        if await self.confirmed(prepared, f"tear down {namespace}", intro):
            self.start_run(prepared)

    @work(group="prepare", exclusive=True, exit_on_error=False)
    async def test(self, namespace: str) -> None:
        """Send the guide's test requests again, from the stack view."""
        if self.busy():
            return
        try:
            prepared = await self.core.prepare_recorded(Action.TEST, namespace)
        except LlmdupError as error:
            self.flash(str(error), Level.ERROR)
            return
        intro = "The tests need these cluster-scoped steps. llmdup runs exactly these commands:"
        if await self.confirmed(prepared, f"test {namespace}", intro):
            self.start_run(prepared, show=False)
            self.flash(f"sending the test requests of {prepared.plan.guide}…")

    def start_run(self, prepared: Prepared, *, show: bool = True) -> None:
        """Run a confirmed plan in a background worker that outlives the view."""
        run = DeployRun.create(prepared, self.core.clock, self.core.now)
        self.deploy_run = run
        self.run_task = self.run_worker(partial(self._drive, run), name="run", group="run", exclusive=True, exit_on_error=False)
        if show:
            self.open(ViewRef(View.DEPLOY))
        self.notify_run(None)

    async def _drive(self, run: DeployRun) -> None:
        try:
            async with aclosing(self.core.execute(run.prepared, confirmed=True)) as events:
                async for event in events:
                    render(event, run)
                    self.notify_run(event)
        except asyncio.CancelledError:
            run.end(stopped=True, error=f"{run.verb} cancelled; :stacks shows what is left, ctrl-d there tears it down")
            self.notify_run(None)
            raise
        except LlmdupError as error:
            run.end(error=str(error))
        if run.running:
            run.end(error="the run ended without a result")
        self.notify_run(None)
        self.flash(*run_status(run))

    def cancel_run(self) -> None:
        """Stop the running deploy, test or tear down at its current step."""
        run, task = self.deploy_run, self.run_task
        if run is None or task is None or not run.running:
            self.flash("nothing is running", Level.WARN)
            return
        task.cancel()
        self.flash(f"cancelling the {run.verb}…", Level.WARN)

    @on(Worker.StateChanged)
    def _worker_changed(self, event: Worker.StateChanged) -> None:
        error = event.worker.error
        if event.state is not WorkerState.ERROR or error is None:
            return
        run = self.deploy_run
        if event.worker is self.run_task and run is not None and run.running:
            run.end(error=f"{type(error).__name__}: {error}")
            self.notify_run(None)
        self.flash(f"{type(error).__name__}: {error}", Level.ERROR)

    async def action_quit(self) -> None:
        """Quit; while a run is going, ask first."""
        self.request_quit()

    def request_quit(self) -> None:
        """Quit, or ask first when a deploy, test or tear down is running."""
        run = self.deploy_run
        if run is None or not run.running:
            self.exit()
            return
        current = run.current()
        commands = [
            f"# running now: {current.path if current else 'starting'}",
            f"# afterwards, see what is left:  llmdup status --namespace {run.plan.namespace}",
            f"# and clean it up:               llmdup down --namespace {run.plan.namespace}",
        ]
        intro = f"A {run.verb} of {run.plan.guide} is running. Quitting stops it at the current step."
        self.push_screen(ConfirmDialog(f"quit during the {run.verb}?", intro, commands), self._quit_answer)

    def _quit_answer(self, answer: object) -> None:
        if answer is True:
            self.cancel_run()
            self.exit()
