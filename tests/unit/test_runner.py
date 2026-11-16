# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
import asyncio
import typing

import pytest

from llmdup import cli
from llmdup.core.kube import Kube
from llmdup.core.models import (
    Action,
    ConfirmationNeededError,
    LogLine,
    Plan,
    Profile,
    RunEvent,
    RunFinished,
    StepFinished,
    StepStarted,
)
from llmdup.core.planner import _local_step
from llmdup.core.runner import Prepared, execute, run_plan

ENV = "#!/usr/bin/env bash\nset -euo pipefail\nexport GREETING=hello\n"


def _plan(*commands: str, action: Action = Action.TEST) -> Plan:
    steps = [_local_step(f"step{i}", command) for i, command in enumerate(commands, 1)]
    return Plan(
        guide="g",
        action=action,
        profile=Profile.DEFAULT,
        llmd_ref="v1.0.0",
        namespace="ns",
        variables=[],
        env_script=ENV,
        steps=steps,
        needs_confirmation=[s.path for s in steps] if action is Action.DOWN else [],
    )


def _events(plan: Plan, **kwargs: typing.Any) -> list[RunEvent]:
    async def collect() -> list[RunEvent]:
        return [event async for event in run_plan(plan, Kube(), **kwargs)]

    return asyncio.run(collect())


def test_exports_and_cwd_carry_between_steps(tmp_path: typing.Any) -> None:
    events = _events(_plan(f'export NEXT="$GREETING world"; cd {tmp_path}', 'echo "$NEXT from $(pwd)"'))
    lines = [e.line for e in events if isinstance(e, LogLine)]
    assert lines == [f"hello world from {tmp_path.resolve()}"]
    assert isinstance(events[-1], RunFinished)
    assert events[-1].ok


def test_failure_stops_and_reports_last_40_lines() -> None:
    events = _events(_plan("for i in $(seq 1 50); do echo line$i; done; exit 3", "echo never"))
    final = events[-1]
    assert isinstance(final, RunFinished)
    assert not final.ok
    assert final.failed_step == "step1"
    assert final.command is not None
    assert "exit 3" in final.command
    assert final.tail == [f"line{i}" for i in range(11, 51)]
    assert not any(isinstance(e, StepStarted) and e.path == "step2" for e in events)


def test_keep_going_runs_every_step() -> None:
    events = _events(_plan("false", "echo after"), keep_going=True)
    assert [e.ok for e in events if isinstance(e, StepFinished)] == [True, False, True]
    assert any(isinstance(e, LogLine) and e.line == "after" for e in events)


def test_secret_values_are_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "hf_supersecret")
    events = _events(_plan('echo "token=$HF_TOKEN"'))
    lines = [e.line for e in events if isinstance(e, LogLine)]
    assert lines == ["token=***"]
    assert "hf_supersecret" not in "".join(e.model_dump_json() for e in events)


def test_execute_refuses_unconfirmed_down() -> None:
    plan = _plan("echo rm", action=Action.DOWN)
    prepared = Prepared(plan=plan, confirm=["# step1\necho rm"])

    async def first() -> None:
        async for _ in execute(Kube(), prepared, confirmed=False):
            pass

    with pytest.raises(ConfirmationNeededError):
        asyncio.run(first())


def test_cli_renders_every_run_event() -> None:
    for event_type in typing.get_args(RunEvent.__value__):
        assert cli.render.dispatch(event_type) is not cli.render.dispatch(object), event_type
