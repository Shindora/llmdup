# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""Plan, deploy, open the stack, send a test request and tear down, all inside the UI, on the local kind cluster."""

import asyncio
import shutil
import subprocess
import time
from collections.abc import Callable

import pytest
from textual.pilot import Pilot

from llmdup.core.models import Action, Profile
from llmdup.tui.app import LlmdupApp
from llmdup.tui.core import Core
from llmdup.tui.run import RowState
from llmdup.tui.screens.dialogs import ConfirmDialog
from llmdup.tui.screens.plan import PlanScreen
from llmdup.tui.screens.stacks import StackScreen, StacksScreen

pytestmark = pytest.mark.e2e
CONTEXT = "kind-llmdup"
GUIDE = "optimized-baseline"
NAMESPACE = f"llm-d-{GUIDE}"


@pytest.fixture(scope="module", autouse=True)
def _guard() -> None:
    if not (shutil.which("docker") or shutil.which("podman")) or not shutil.which("kind"):
        pytest.skip("e2e needs Docker or Podman and kind")
    current = subprocess.run(["kubectl", "config", "current-context"], capture_output=True, text=True, check=False).stdout.strip()
    if not current.startswith("kind-llmdup"):
        pytest.fail(f"refusing to run e2e tests: current kube context is {current!r}, not kind-llmdup*")


async def until(pilot: Pilot, done: Callable[[], bool], what: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while not done():
        if time.monotonic() > deadline:
            pytest.fail(f"timed out after {timeout:g}s waiting for {what}")
        await pilot.pause(0.5)


async def type_command(pilot: Pilot, text: str) -> None:
    await pilot.press(":", *text, "enter")
    await pilot.pause()


async def confirm_if_asked(pilot: Pilot, app: LlmdupApp, started: Callable[[], bool], what: str) -> list[str]:
    await until(pilot, lambda: isinstance(app.screen, ConfirmDialog) or started(), what, 300)
    if not isinstance(app.screen, ConfirmDialog):
        return []
    commands = list(app.screen.commands)
    await pilot.press("y")
    return commands


async def finished(pilot: Pilot, app: LlmdupApp, action: Action, timeout: float) -> None:
    await until(
        pilot,
        lambda: app.deploy_run is not None and app.deploy_run.plan.action is action and not app.deploy_run.running,
        f"the {action} run to end",
        timeout,
    )
    run = app.deploy_run
    assert run is not None
    tail = "\n".join(e.text for e in run.log[-40:])
    assert run.ok, f"{action} failed: {run.error or (run.result.message if run.result else '')}\n{tail}"


def test_plan_deploy_open_stack_test_and_tear_down_without_leaving_the_ui() -> None:
    assert asyncio.run(Core(context=CONTEXT).status()).stacks == [], "the kind cluster must start empty"
    app = LlmdupApp(Core(context=CONTEXT))

    async def scenario(pilot: Pilot) -> None:
        await until(pilot, lambda: app.facts is not None and app.facts.context == CONTEXT, "cluster detection", 120)

        await type_command(pilot, f"plan {GUIDE}")
        plan_view = app.screen
        assert isinstance(plan_view, PlanScreen)
        await until(pilot, lambda: plan_view.plan is not None, "the plan", 300)
        assert plan_view.plan is not None
        assert plan_view.plan.profile is Profile.LOCAL

        await pilot.press("u")
        asked = await confirm_if_asked(pilot, app, lambda: app.deploy_run is not None, "the deploy confirmation")
        assert any("prerequisites.gaie" in c for c in asked)
        await finished(pilot, app, Action.UP, 1500)

        await type_command(pilot, "stacks")
        stacks_view = app.screen
        assert isinstance(stacks_view, StacksScreen)
        await until(pilot, lambda: stacks_view.report is not None and bool(stacks_view.report.stacks), "the stacks", 120)
        await pilot.press("enter")
        await pilot.pause()
        stack_view = app.screen
        assert isinstance(stack_view, StackScreen)
        await until(pilot, lambda: (s := stack_view.selected_stack()) is not None and s.health == "ready", "a ready stack", 300)

        await pilot.press("t")
        await confirm_if_asked(pilot, app, lambda: app.deploy_run is not None and app.deploy_run.plan.action is Action.TEST, "the test")
        await finished(pilot, app, Action.TEST, 600)
        assert isinstance(app.screen, StackScreen)
        assert app.deploy_run is not None
        # kubectl run --rm -i can drop the output of a pod that exits before attach, so check the step results, not the curl output
        assert [r.path for r in app.deploy_run.rows if r.state is not RowState.DONE] == []

        await pilot.press("ctrl+d")
        asked = await confirm_if_asked(
            pilot, app, lambda: app.deploy_run is not None and app.deploy_run.plan.action is Action.DOWN, "tear down"
        )
        assert any("helm uninstall" in c for c in asked)
        await finished(pilot, app, Action.DOWN, 900)

    async def main() -> None:
        async with app.run_test(size=(120, 36)) as pilot:
            await scenario(pilot)

    asyncio.run(main())
    assert asyncio.run(Core(context=CONTEXT).status()).stacks == []
