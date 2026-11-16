# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0

import asyncio
import base64
import os
import time
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path

from pydantic import BaseModel, Field

from llmdup.core import state as state_store
from llmdup.core.detect import detect
from llmdup.core.doctor import doctor
from llmdup.core.kube import CommandFailedError, Kube
from llmdup.core.models import (
    Action,
    CheckStatus,
    Choices,
    ConfirmationNeededError,
    DoctorFailedError,
    DoctorReport,
    LogLine,
    Plan,
    RunEvent,
    RunFinished,
    StackPhase,
    StackState,
    StepFinished,
    StepStarted,
    UsageError,
    WaitProgress,
)
from llmdup.core.planner import Detected, build_plan
from llmdup.core.status import FINISHED_PHASES, GUIDE_POD_LABEL, deployments_ready, pod_ready
from llmdup.core.upstream import DEFAULT_LLMD_REF

ENV_MARKER = "__LLMDUP_ENV__"
CAPTURE = f"\nprintf '%s ' {ENV_MARKER}; env -0 | base64 | tr -d '\\n'; printf '\\n'\n"
DROP_ENV = frozenset({"_", "SHLVL", "OLDPWD"})
SECRET_NAMES = ("HF_TOKEN",)
TAIL_LINES = 40
POLL_SECONDS = 3.0
DEFAULT_TIMEOUT = 1800.0


class Prepared(BaseModel):
    """A plan ready to run, with the preflight report and the commands that need confirmation."""

    plan: Plan
    doctor: DoctorReport | None = None
    confirm: list[str] = Field(default_factory=list)


class NotReadyError(Exception):
    """Pods did not become ready before the deadline."""

    def __init__(self, summary: list[str]) -> None:
        super().__init__("pods not ready before the timeout")
        self.summary = summary


def redact(line: str) -> str:
    """Replace secret values from the environment with `***`."""
    for name in SECRET_NAMES:
        if (value := os.environ.get(name)) and value in line:
            line = line.replace(value, "***")
    return line


def decode_env(blob: str) -> dict[str, str]:
    """Decode the base64 `env -0` dump a step prints after its marker."""
    out = {}
    for entry in base64.b64decode(blob.strip()).decode(errors="replace").split("\0"):
        name, sep, value = entry.partition("=")
        if sep and name not in DROP_ENV and not name.startswith("BASH_FUNC_"):
            out[name] = value
    return out


class _Shell:
    """Exports and working directory carried from one step to the next."""

    def __init__(self) -> None:
        self.env: dict[str, str] = {}
        self.cwd: Path | None = None

    def absorb(self, blob: str) -> None:
        self.env = decode_env(blob)
        pwd = self.env.pop("PWD", "")
        self.cwd = Path(pwd) if pwd else self.cwd


async def _step_lines(kube: Kube, script: str, shell: _Shell, deadline: float) -> AsyncIterator[str]:
    remaining = max(deadline - time.monotonic(), 1.0)
    async for raw in kube.stream("bash", script + CAPTURE, timeout=remaining, env=shell.env, cwd=shell.cwd):
        before, marker, blob = raw.partition(ENV_MARKER)
        if marker:
            shell.absorb(blob)
            if before.strip():
                yield redact(before)
            continue
        yield redact(raw)


async def _readiness(kube: Kube, namespace: str, guide: str) -> tuple[bool, WaitProgress, list[str]]:
    pod_doc = await kube.json("kubectl", "get", "pods", "-n", namespace, "-o", "json", timeout=30)
    dep_doc = await kube.json("kubectl", "get", "deployments", "-n", namespace, "-o", "json", timeout=30)
    match pod_doc:
        case {"items": list(items)}:
            live = [p for p in items if isinstance(p, dict) and p.get("status", {}).get("phase") not in FINISHED_PHASES]
        case _:
            live = []
    model = [p for p in live if p.get("metadata", {}).get("labels", {}).get(GUIDE_POD_LABEL) == guide]
    ready_pods = [p for p in live if pod_ready(p)]
    deps_ready, deps = deployments_ready(dep_doc)
    summary = [f"{p.get('metadata', {}).get('name')}: {p.get('status', {}).get('phase')}, ready={pod_ready(p)}" for p in live]
    done = bool(model) and len(ready_pods) == len(live) and deps_ready == deps
    message = f"{len(ready_pods)}/{len(live)} pods ready, {len(model)} model server pods, {deps_ready}/{deps} deployments available"
    return done, WaitProgress(message=message, ready=len(ready_pods), total=len(live)), summary


async def wait_ready(kube: Kube, plan: Plan, deadline: float) -> AsyncIterator[WaitProgress]:
    """Poll until the guide's model-server pods and every Deployment in the namespace are ready.

    Yields:
        Progress whenever the counts change.

    Raises:
        NotReadyError: The deadline passed first.
    """
    guide = next((v.value for v in plan.variables if v.name == "GUIDE_NAME" and v.value), plan.guide)
    last = ""
    while True:
        done, progress, summary = await _readiness(kube, plan.namespace, guide)
        if progress.message != last:
            last = progress.message
            yield progress
        if done:
            return
        if time.monotonic() >= deadline:
            raise NotReadyError(summary)
        await asyncio.sleep(POLL_SECONDS)


async def run_plan(
    plan: Plan,
    kube: Kube,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    keep_going: bool = False,
    wait: bool = False,
) -> AsyncGenerator[RunEvent]:
    """Run the env step, then each plan step in order, as an async stream of events.

    Args:
        plan: The plan to run.
        kube: Command runner pinned to the target context.
        timeout: Seconds for the whole run.
        keep_going: Run every step even after one fails (used by teardown).
        wait: Before the first verify step, wait for ready pods.

    Yields:
        Step starts, log lines, readiness progress, step ends, then one `RunFinished`.
    """
    deadline = time.monotonic() + timeout
    shell = _Shell()
    steps = [("env", "export the guide's variables", plan.env_script), *((s.path, s.command, s.script) for s in plan.steps)]
    results: list[StepFinished] = []
    failed: str | None = None
    waited = not wait
    for index, (path, command, script) in enumerate(steps, 1):
        if not waited and path.startswith("verify."):
            waited = True
            try:
                async for progress in wait_ready(kube, plan, deadline):
                    yield progress
            except NotReadyError as exc:
                yield _finished(plan, results, ok=False, message=str(exc), failed="wait for ready pods", tail=exc.summary)
                return
        yield StepStarted(path=path, index=index, total=len(steps), command=command)
        started = time.monotonic()
        tail: deque[str] = deque(maxlen=TAIL_LINES)
        try:
            async for line in _step_lines(kube, script, shell, deadline):
                tail.append(line)
                yield LogLine(path=path, line=line)
        except CommandFailedError as exc:
            result = StepFinished(path=path, ok=False, returncode=exc.returncode, seconds=round(time.monotonic() - started, 1))
            results.append(result)
            yield result
            failed = failed or path
            if keep_going and path != "env":
                continue
            message = f"step {path} failed with exit status {exc.returncode}"
            yield _finished(plan, results, ok=False, message=message, failed=path, command=command, tail=list(tail))
            return
        result = StepFinished(path=path, ok=True, seconds=round(time.monotonic() - started, 1))
        results.append(result)
        yield result
    message = f"{plan.action} {plan.guide}: {'all steps ok' if failed is None else f'{failed} failed'}"
    yield _finished(plan, results, ok=failed is None, message=message, failed=failed)


def _finished(
    plan: Plan,
    results: list[StepFinished],
    *,
    ok: bool,
    message: str,
    failed: str | None = None,
    command: str | None = None,
    tail: list[str] | None = None,
) -> RunFinished:
    return RunFinished(
        action=plan.action,
        guide=plan.guide,
        namespace=plan.namespace,
        ok=ok,
        message=message,
        failed_step=failed,
        command=command,
        tail=tail or [],
        steps=list(results),
    )


def confirm_commands(plan: Plan) -> list[str]:
    """Return the exact commands of the steps that need confirmation."""
    by_path = {s.path: s.command for s in plan.steps}
    return [f"# {path}\n{by_path[path]}" for path in plan.needs_confirmation]


def require_confirmation(prepared: Prepared, *, confirmed: bool) -> None:
    """Refuse to run a plan with unconfirmed cluster-scoped or delete steps.

    Raises:
        ConfirmationNeededError: Confirmation is needed and was not given.
    """
    if prepared.confirm and not confirmed:
        what = "deletes resources" if prepared.plan.action is Action.DOWN else "has cluster-scoped steps"
        msg = f"the plan {what}: {', '.join(prepared.plan.needs_confirmation)}"
        raise ConfirmationNeededError(msg, hint="review the commands, then pass --yes (CLI) or confirm: true (MCP)")


async def prepare_up(
    kube: Kube,
    choices: Choices,
    *,
    ref: str = DEFAULT_LLMD_REF,
    verify: bool = True,
    gate: bool = True,
) -> Prepared:
    """Detect the cluster, run the preflight checks and build the deploy plan.

    With `gate` False (dry runs), failed checks are reported in the result instead of raised.

    Returns:
        The plan with its preflight report and the commands that need confirmation.

    Raises:
        DoctorFailedError: A blocking check failed.
    """
    facts = await detect(kube)
    report = await doctor(kube, choices, ref=ref, facts=facts)
    if gate and not report.ok:
        failed = [f"{c.name}: {c.found}" for c in report.checks if c.status is CheckStatus.FAIL]
        raise DoctorFailedError("; ".join(failed), hint="run `llmdup doctor` for fixes")
    plan = build_plan(choices, ref=ref, detected=Detected(accelerator=facts.accelerator, provider=facts.provider), verify=verify)
    return Prepared(plan=plan, doctor=report, confirm=confirm_commands(plan))


async def find_stack(kube: Kube, namespace: str | None) -> StackState:
    """Return the one recorded stack in `namespace`, or the only one on the cluster.

    Returns:
        The stack's state record.

    Raises:
        UsageError: No stack, or several and no namespace was given.
    """
    states = await state_store.load(kube, namespace)
    if not states:
        where = f"namespace {namespace}" if namespace else "this cluster"
        msg = f"llmdup has no stack recorded in {where}"
        raise UsageError(msg, hint="see `llmdup status`")
    if len(states) > 1:
        msg = "several stacks are deployed"
        raise UsageError(msg, hint=f"pass --namespace: {', '.join(s.namespace for s in states)}")
    return states[0]


async def prepare_recorded(
    kube: Kube,
    action: Action,
    namespace: str | None,
    *,
    keep_namespace: bool = False,
) -> Prepared:
    """Build the test or teardown plan of a recorded stack from its state ConfigMap."""
    found = await find_stack(kube, namespace)
    plan = build_plan(found.choices(), action=action, ref=found.llmd_ref, keep_namespace=keep_namespace)
    return Prepared(plan=plan, confirm=confirm_commands(plan))


async def execute(kube: Kube, prepared: Prepared, *, confirmed: bool, timeout: float = DEFAULT_TIMEOUT) -> AsyncGenerator[RunEvent]:
    """Run a prepared plan and keep the stack's state ConfigMap up to date.

    Yields:
        The runner's events; for teardown the final event says whether anything is left.
    """
    require_confirmation(prepared, confirmed=confirmed)
    plan = prepared.plan
    match plan.action:
        case Action.UP:
            stream = _deploy(kube, plan, timeout)
        case Action.DOWN:
            stream = _teardown(kube, plan, timeout)
        case _:
            stream = run_plan(plan, kube, timeout=timeout)
    async for event in stream:
        yield event


async def _deploy(kube: Kube, plan: Plan, timeout: float) -> AsyncGenerator[RunEvent]:
    record = state_store.from_plan(plan)
    async for event in run_plan(plan, kube, timeout=timeout, wait=True):
        match event:
            case StepFinished(ok=True, path=path) if path != "env":
                record.steps_done.append(path)
                await state_store.save(kube, record)
            case RunFinished(ok=ok, failed_step=failed):
                await state_store.save(kube, state_store.mark(record, StackPhase.DEPLOYED if ok else StackPhase.FAILED, failed_step=failed))
        yield event


async def _teardown(kube: Kube, plan: Plan, timeout: float) -> AsyncGenerator[RunEvent]:
    records = await state_store.load(kube, plan.namespace)
    if records:
        await state_store.save(kube, state_store.mark(records[0], StackPhase.TEARING_DOWN))
    async for event in run_plan(plan, kube, timeout=timeout, keep_going=True):
        if not isinstance(event, RunFinished):
            yield event
            continue
        left = await _leftovers(kube, plan)
        ok = not left
        if ok:
            await state_store.delete(kube, plan.namespace)
        elif records:
            await state_store.save(kube, state_store.mark(records[0], StackPhase.TEARING_DOWN, failed_step=event.failed_step))
        message = "nothing left deployed" if ok else f"left behind: {', '.join(left)}"
        yield event.model_copy(update={"ok": ok, "message": message, "tail": event.tail or left})


async def _leftovers(kube: Kube, plan: Plan) -> list[str]:
    release = next((v.value for v in plan.variables if v.name == "GUIDE_NAME" and v.value), plan.guide)
    namespace = await kube.run("kubectl", "get", "namespace", plan.namespace, "-o", "name", timeout=30)
    if not namespace.ok:
        return []
    left = []
    helm = await kube.json("helm", "list", "-n", plan.namespace, "-o", "json", timeout=30)
    if isinstance(helm, list) and any(isinstance(r, dict) and r.get("name") == release for r in helm):
        left.append(f"helm release {release}")
    pods = await kube.json("kubectl", "get", "pods", "-n", plan.namespace, "-l", GUIDE_POD_LABEL, "-o", "json", timeout=30)
    match pods:
        case {"items": list(items)} if alive := [p for p in items if not p.get("metadata", {}).get("deletionTimestamp")]:
            left.append(f"{len(alive)} model server pods")
    if not any(s.reason == "--keep-namespace" for s in plan.skipped):
        left.append(f"namespace {plan.namespace}")
    return left
