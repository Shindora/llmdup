# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""The keys, the help view, `render`, the run worker, confirmation and the token."""

import asyncio
import io
import subprocess
import sys
import typing

import pytest
from rich.console import Console
from textual.binding import Binding
from textual.pilot import Pilot
from textual.widgets import Static

from llmdup.core.models import Action, RunEvent
from llmdup.tui import keys
from llmdup.tui.app import LlmdupApp
from llmdup.tui.run import RowState, render
from llmdup.tui.screens.deploy import DeployScreen
from llmdup.tui.screens.dialogs import ConfirmDialog
from llmdup.tui.screens.guides import GuidesScreen
from llmdup.tui.screens.help import HelpScreen
from llmdup.tui.screens.plan import PlanScreen
from llmdup.tui.screens.stacks import StackScreen

from .fakecore import NAMESPACE, FakeCore
from .helpers import command, drawn, drive, run_done, settle, until, view

SENTINEL = "hf_sentinelTokenValue123"


def app_of(pilot: Pilot) -> LlmdupApp:
    app = pilot.app
    assert isinstance(app, LlmdupApp)
    return app


def test_the_cli_loads_without_textual() -> None:
    code = "import sys, llmdup.cli, llmdup.tui; sys.exit(int('textual' in sys.modules))"
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0


def test_tui_renders_every_run_event() -> None:
    for event_type in typing.get_args(RunEvent.__value__):
        assert render.dispatch(event_type) is not render.dispatch(object), event_type


def test_render_base_raises_for_unknown_events() -> None:
    with pytest.raises(NotImplementedError):
        render(object(), None)


def test_every_binding_in_keys_appears_in_the_help_view() -> None:
    async def scenario(pilot: Pilot) -> None:
        await pilot.press("question_mark")
        await settle(pilot)
        help_view = view(app_of(pilot))
        assert isinstance(help_view, HelpScreen)
        console = Console(width=help_view.help_width, file=io.StringIO(), record=True, color_system=None)
        console.print(help_view.query_one("#help", Static).content)
        lines = console.export_text().splitlines()
        for key in keys.KEYS:
            assert any(key.label in line and key.description in line for line in lines), key
        for entry in (*keys.SYNTAX, *keys.COMMANDS):
            assert any(entry.label in line and entry.description in line for line in lines), entry

    drive(scenario)


def test_every_binding_in_keys_is_bound_in_some_widget() -> None:
    bound = {
        (b.key, b.action) for scope in keys.Scope for v in (None, *keys.View) for b in keys.bindings(scope, v) if isinstance(b, Binding)
    }
    assert bound == {(k.keys, k.action) for k in keys.KEYS}


def test_header_hints_come_from_keys() -> None:
    for each in keys.View:
        for hint in keys.hints(each):
            assert any(hint == k.header_hint() for k in keys.KEYS), hint


def test_confirm_dialog_shows_the_exact_commands_and_cancel_runs_nothing() -> None:
    core = FakeCore()

    async def scenario(pilot: Pilot) -> None:
        await command(pilot, "plan optimized-baseline")
        plan_view = view(app_of(pilot))
        assert isinstance(plan_view, PlanScreen)
        assert plan_view.plan is not None
        gaie = next(s for s in plan_view.plan.steps if s.path == "prerequisites.gaie")
        await pilot.press("u")
        await settle(pilot)
        dialog = app_of(pilot).screen
        assert isinstance(dialog, ConfirmDialog)
        shown = str(dialog.query_one("#commands", Static).content)
        assert f"# prerequisites.gaie\n{gaie.command}" in shown
        await pilot.press("n")
        await settle(pilot)
        assert app_of(pilot).deploy_run is None
        assert core.executed == []
        message = view(app_of(pilot)).flash_message
        assert message is not None
        assert message[0] == "cancelled; nothing ran"

    drive(scenario, app=LlmdupApp(core))


def test_deploy_keeps_running_when_the_view_is_left() -> None:
    core = FakeCore()
    core.gate = asyncio.Event()

    async def scenario(pilot: Pilot) -> None:
        app = app_of(pilot)
        await command(pilot, "plan optimized-baseline")
        await pilot.press("u")
        await settle(pilot)
        await pilot.press("y")
        await until(pilot, lambda: app.deploy_run is not None and app.deploy_run.row("wait: pods ready") is not None)
        await until(pilot, lambda: app.deploy_run is not None and app.deploy_run.current() is not None)
        assert isinstance(view(app), DeployScreen)
        await command(pilot, "guides")
        assert isinstance(view(app), GuidesScreen)
        assert app.deploy_run is not None
        assert app.deploy_run.running
        assert core.gate is not None
        core.gate.set()
        await run_done(pilot)
        assert app.deploy_run.ok
        await command(pilot, "deploy")
        deploy_view = view(app)
        assert isinstance(deploy_view, DeployScreen)
        assert all(r.state is RowState.DONE for r in app.deploy_run.rows)
        assert "request 1: HTTP 200" in [e.text for e in app.deploy_run.log]
        assert deploy_view.title_text().plain == f"deploy(optimized-baseline)[{len(app.deploy_run.rows)}/{len(app.deploy_run.rows)}]"

    drive(scenario, app=LlmdupApp(core))


def test_ctrl_k_cancels_the_deploy() -> None:
    core = FakeCore()
    core.gate = asyncio.Event()

    async def scenario(pilot: Pilot) -> None:
        app = app_of(pilot)
        await command(pilot, "plan optimized-baseline")
        await pilot.press("u")
        await settle(pilot)
        await pilot.press("y")
        await until(pilot, lambda: app.deploy_run is not None and app.deploy_run.current() is not None)
        await pilot.press("ctrl+k")
        await run_done(pilot)
        run = app.deploy_run
        assert run is not None
        assert not run.ok
        assert run.error is not None
        assert "cancelled" in run.error
        assert any(r.state is RowState.STOPPED for r in run.rows)

    drive(scenario, app=LlmdupApp(core))


def test_quitting_during_a_deploy_asks_first() -> None:
    core = FakeCore()
    core.gate = asyncio.Event()

    async def scenario(pilot: Pilot) -> None:
        app = app_of(pilot)
        await command(pilot, "plan optimized-baseline")
        await pilot.press("u")
        await settle(pilot)
        await pilot.press("y")
        await until(pilot, lambda: app.deploy_run is not None and app.deploy_run.current() is not None)
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmDialog)
        await pilot.press("n")
        await pilot.pause()
        assert app.deploy_run is not None
        assert app.deploy_run.running
        await pilot.press("ctrl+c")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert app.deploy_run.error is not None
        assert "cancelled" in app.deploy_run.error

    drive(scenario, app=LlmdupApp(core))


def test_quit_without_a_run_exits_at_once() -> None:
    async def scenario(pilot: Pilot) -> None:
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert not isinstance(app_of(pilot).screen, ConfirmDialog)

    app = drive(scenario)
    assert app.return_code == 0


def test_stack_test_request_and_teardown_from_the_stack_view() -> None:
    core = FakeCore()

    async def scenario(pilot: Pilot) -> None:
        app = app_of(pilot)
        await command(pilot, "stacks")
        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(view(app), StackScreen)
        await pilot.press("t")
        await run_done(pilot)
        assert isinstance(view(app), StackScreen)
        assert app.deploy_run is not None
        assert app.deploy_run.plan.action is Action.TEST
        assert app.deploy_run.ok
        assert "passed" in str(view(app).query_one("#test", Static).content)
        await pilot.press("ctrl+d")
        await settle(pilot)
        dialog = app.screen
        assert isinstance(dialog, ConfirmDialog)
        assert any("helm uninstall" in c for c in dialog.commands)
        await pilot.press("y")
        await run_done(pilot)
        assert isinstance(view(app), DeployScreen)
        assert app.deploy_run.plan.action is Action.DOWN
        assert app.deploy_run.ok
        assert core.stacks == []
        await command(pilot, "stacks")
        assert view(app).flash_message is not None
        assert view(app).flash_message[0] == "nothing deployed by llmdup"

    drive(scenario, app=LlmdupApp(core))


def test_port_forward_metrics_and_describe() -> None:
    async def scenario(pilot: Pilot) -> None:
        app = app_of(pilot)
        await command(pilot, "stacks")
        await pilot.press("F")
        assert app.clipboard == f"kubectl port-forward -n {NAMESPACE} svc/optimized-baseline-epp 8000:80"
        await pilot.press("m")
        await settle(pilot)
        assert "vllm:request_success_total" in str(app.screen.query_one("#text", Static).content)
        await pilot.press("escape", "d")
        await settle(pilot)
        assert "guide: optimized-baseline" in str(app.screen.query_one("#text", Static).content)

    drive(scenario)


def test_the_token_value_is_never_drawn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", SENTINEL)

    async def scenario(pilot: Pilot) -> None:
        app = app_of(pilot)
        await command(pilot, "plan optimized-baseline")
        screen_text = drawn(app)
        assert "HF_TOKEN" in screen_text
        assert "set (never shown)" in screen_text
        assert SENTINEL not in screen_text
        await pilot.press("u")
        await settle(pilot)
        await pilot.press("y")
        await run_done(pilot)
        assert SENTINEL not in drawn(app)
        assert app.deploy_run is not None
        assert all(SENTINEL not in e.text for e in app.deploy_run.log)

    drive(scenario)


def test_secret_variables_cannot_be_edited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", SENTINEL)

    async def scenario(pilot: Pilot) -> None:
        app = app_of(pilot)
        await command(pilot, "plan optimized-baseline")
        plan_view = view(app)
        assert isinstance(plan_view, PlanScreen)
        await pilot.press("tab")
        plan_view.variables.select_key("HF_TOKEN")
        await pilot.press("e", "c")
        await settle(pilot)
        assert not isinstance(app.screen, ConfirmDialog)
        assert app.clipboard != SENTINEL

    drive(scenario)
