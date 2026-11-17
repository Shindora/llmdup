# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0

import asyncio
import base64
import binascii
import os
import re
import shutil
import tempfile
import time
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator, Iterable
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
from llmdup.core.planner import GATEWAY_NAME, SECRET_NAMES, Detected, build_plan
from llmdup.core.status import FINISHED_PHASES, GUIDE_POD_LABEL, deployments_ready, gateway_programmed, pod_ready
from llmdup.core.upstream import DEFAULT_LLMD_REF

ENV_MARKER = "__LLMDUP_ENV__"
CAPTURE = f"\nprintf '%s ' {ENV_MARKER}; env -0 | base64 | tr -d '\\n'; printf '\\n'\n"
DROP_ENV = frozenset({"_", "SHLVL", "OLDPWD"})
TAIL_LINES = 40
POLL_SECONDS = 3.0
DEFAULT_TIMEOUT = 1800.0
VAR_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


class Prepared(BaseModel):
    """A plan ready to run, with its target context, the preflight report and the commands that need confirmation."""

    plan: Plan
    context: str | None = None
    doctor: DoctorReport | None = None
    confirm: list[str] = Field(default_factory=list)


class NotReadyError(Exception):
    """Pods did not become ready before the deadline."""

    def __init__(self, summary: list[str]) -> None:
        super().__init__("pods not ready before the timeout")
        self.summary = summary


def secret_names(plan: Plan | None = None) -> set[str]:
    """Return the variables whose values must never be shown: HF_TOKEN and the guide's sensitive ones."""
    return set(SECRET_NAMES) | {v.name for v in (plan.variables if plan else []) if v.sensitive}


def redact(line: str, names: Iterable[str] = SECRET_NAMES) -> str:
    """Replace the environment values of secret variables with `***`."""
    for name in names:
        if (value := os.environ.get(name)) and value in line:
            line = line.replace(value, "***")
    return line


def decode_env(blob: str) -> dict[str, str]:
    """Decode the base64 `env -0` dump a step prints after its marker."""
    out = {}
    for entry in base64.b64decode(blob.strip(), validate=True).decode(errors="replace").split("\0"):
        name, sep, value = entry.partition("=")
        if sep and name not in DROP_ENV and not name.startswith("BASH_FUNC_"):
            out[name] = value
    return out


class _Shell:
    """Exports and working directory carried from one step to the next."""

    def __init__(self, cwd: Path) -> None:
        self.env: dict[str, str] = {}
        self.cwd = cwd

    def absorb(self, blob: str) -> bool:
        try:
            env = decode_env(blob)
        except binascii.Error, ValueError:
            return False
        pwd = env.pop("PWD", "")
        self.env = env
        self.cwd = Path(pwd) if pwd else self.cwd
        return True


def _release(plan: Plan) -> str:
    return next((v.value for v in plan.variables if v.name == "GUIDE_NAME" and v.value), plan.guide)


def _name(item: dict[str, object]) -> str:
    meta = item.get("metadata")
    return str(meta.get("name", "")) if isinstance(meta, dict) else ""


def _labels(item: dict[str, object]) -> dict[str, object]:
    meta = item.get("metadata")
    labels = meta.get("labels") if isinstance(meta, dict) else None
    return labels if isinstance(labels, dict) else {}


async def _readiness(kube: Kube, plan: Plan) -> tuple[bool, WaitProgress, list[str]]:
    release = guide = _release(plan)
    pod_doc = await kube.json("kubectl", "get", "pods", "-n", plan.namespace, "-o", "json", timeout=30)
    dep_doc = await kube.json("kubectl", "get", "deployments", "-n", plan.namespace, "-o", "json", timeout=30)

    def ours(item: dict[str, object]) -> bool:
        return _labels(item).get(GUIDE_POD_LABEL) == guide or _name(item).startswith(f"{release}-")

    match pod_doc:
        case {"items": list(items)}:
            live = [p for p in items if isinstance(p, dict) and ours(p) and p.get("status", {}).get("phase") not in FINISHED_PHASES]
        case _:
            live = []
    match dep_doc:
        case {"items": list(items)}:
            deps_ready, deps = deployments_ready({"items": [d for d in items if isinstance(d, dict) and ours(d)]})
        case _:
            deps_ready, deps = 0, 0
    model = [p for p in live if _labels(p).get(GUIDE_POD_LABEL) == guide]
    ready = [p for p in live if pod_ready(p)]
    summary = [f"{_name(p)}: {p.get('status', {}).get('phase')}, ready={pod_ready(p)}" for p in live]
    done = bool(model) and len(ready) == len(live) and deps_ready == deps
    message = f"{len(ready)}/{len(live)} pods ready, {len(model)} model server pods, {deps_ready}/{deps} deployments available"
    if plan.mode == "gateway":
        gw_doc = await kube.json("kubectl", "get", "gateway", GATEWAY_NAME, "-n", plan.namespace, "-o", "json", timeout=30)
        programmed = gateway_programmed(gw_doc)
        state = {None: "not found", False: "not programmed yet", True: "programmed"}[programmed]
        done = done and programmed is not False
        message += f", gateway {state}"
        summary.append(f"gateway {GATEWAY_NAME}: {state}")
    return done, WaitProgress(message=message, ready=len(ready), total=len(live)), summary


async def wait_ready(kube: Kube, plan: Plan, deadline: float) -> AsyncIterator[WaitProgress]:
    """Poll until the guide's model-server pods and the router release's pods and Deployments are ready.

    In gateway mode, also wait until the Gateway is programmed and has an address, unless it does not exist.

    Yields:
        Progress whenever the counts change.

    Raises:
        NotReadyError: The deadline passed first.
    """
    last = ""
    while True:
        done, progress, summary = await _readiness(kube, plan)
        if progress.message != last:
            last = progress.message
            yield progress
        if done:
            return
        if time.monotonic() >= deadline:
            raise NotReadyError(summary)
        await asyncio.sleep(POLL_SECONDS)


class _Run:
    """One pass over a plan's steps; `events` yields what happens."""

    def __init__(self, plan: Plan, kube: Kube, deadline: float, work: Path) -> None:
        self.plan = plan
        self.kube = kube
        self.deadline = deadline
        self.shell = _Shell(work)
        self.secrets = secret_names(plan)
        self.results: list[StepFinished] = []
        self.failed: str | None = None
        self.stop: RunFinished | None = None

    def finished(self, *, ok: bool, message: str, command: str | None = None, tail: list[str] | None = None) -> RunFinished:
        return RunFinished(
            action=self.plan.action,
            guide=self.plan.guide,
            namespace=self.plan.namespace,
            ok=ok,
            message=message,
            failed_step=self.failed,
            command=command,
            tail=tail or [],
            steps=list(self.results),
        )

    async def lines(self, script: str) -> AsyncIterator[str]:
        remaining = max(self.deadline - time.monotonic(), 1.0)
        async for raw in self.kube.stream("bash", script + CAPTURE, timeout=remaining, env=self.shell.env, cwd=self.shell.cwd):
            before, marker, blob = raw.partition(ENV_MARKER)
            if marker and self.shell.absorb(blob):
                if before.strip():
                    yield redact(before, self.secrets)
                continue
            yield redact(raw, self.secrets)

    async def wait(self) -> AsyncIterator[RunEvent]:
        try:
            async for progress in wait_ready(self.kube, self.plan, self.deadline):
                yield progress
        except NotReadyError as exc:
            self.failed = "wait for ready pods"
            self.stop = self.finished(ok=False, message=str(exc), tail=exc.summary)

    async def step(self, index: int, total: int, path: str, command: str, script: str, *, keep_going: bool) -> AsyncIterator[RunEvent]:
        yield StepStarted(path=path, index=index, total=total, command=command)
        started = time.monotonic()
        tail: deque[str] = deque(maxlen=TAIL_LINES)
        try:
            async for line in self.lines(script):
                tail.append(line)
                yield LogLine(path=path, line=line)
        except CommandFailedError as exc:
            result = StepFinished(path=path, ok=False, returncode=exc.returncode, seconds=round(time.monotonic() - started, 1))
            self.results.append(result)
            yield result
            self.failed = self.failed or path
            if not (keep_going and path != "env"):
                message = f"step {path} failed with exit status {exc.returncode}"
                self.stop = self.finished(ok=False, message=message, command=command, tail=list(tail))
            return
        result = StepFinished(path=path, ok=True, seconds=round(time.monotonic() - started, 1))
        self.results.append(result)
        yield result

    async def events(self, *, keep_going: bool, wait: bool) -> AsyncIterator[RunEvent]:
        plan = self.plan
        steps = [("env", "export the guide's variables", plan.env_script), *((s.path, s.command, s.script) for s in plan.steps)]
        waited = not wait
        for index, (path, command, script) in enumerate(steps, 1):
            if not waited and path.startswith("verify."):
                waited = True
                async for event in self.wait():
                    yield event
            if self.stop is None:
                async for event in self.step(index, len(steps), path, command, script, keep_going=keep_going):
                    yield event
            if self.stop is not None:
                yield self.stop
                return
        if not waited and self.failed is None:
            async for event in self.wait():
                yield event
        if self.stop is not None:
            yield self.stop
            return
        outcome = "all steps ok" if self.failed is None else f"{self.failed} failed"
        yield self.finished(ok=self.failed is None, message=f"{plan.action} {plan.guide}: {outcome}")


async def run_plan(
    plan: Plan,
    kube: Kube,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    keep_going: bool = False,
    wait: bool = False,
) -> AsyncGenerator[RunEvent]:
    """Run the env step, then each plan step in order, as an async stream of events.

    Steps start in a fresh temporary directory, never in the user's working directory.

    Args:
        plan: The plan to run.
        kube: Command runner pinned to the target context.
        timeout: Seconds for the whole run.
        keep_going: Run every step even after one fails (used by teardown).
        wait: Wait for ready pods before the first verify step, or at the end when there is none.

    Yields:
        Step starts, log lines, readiness progress, step ends, then one `RunFinished`.
    """
    work = Path(tempfile.mkdtemp(prefix="llmdup-run-"))
    try:
        run = _Run(plan, kube, time.monotonic() + timeout, work)
        async for event in run.events(keep_going=keep_going, wait=wait):
            yield event
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _expand(command: str, values: dict[str, str]) -> str:
    for _ in range(2):
        command = VAR_REF.sub(lambda m: values.get(m.group(1) or m.group(2), m.group(0)), command)
    return command


def confirm_commands(plan: Plan, context: str | None = None, paths: list[str] | None = None) -> list[str]:
    """Return the target and the commands of the steps that need confirmation, with known variables filled in.

    Args:
        plan: The plan.
        context: The kube context the plan will run against.
        paths: Steps to list instead of `plan.needs_confirmation`.

    Returns:
        A header naming the context and namespace, then one entry per step; empty when nothing needs confirmation.
    """
    wanted = plan.needs_confirmation if paths is None else paths
    if not wanted:
        return []
    values = {v.name: v.value for v in plan.variables if v.value is not None and not v.sensitive}
    by_path = {s.path: s.command for s in plan.steps}
    header = f"# kube context: {context or '(kubeconfig current context)'}   namespace: {plan.namespace}"
    return [header, *(f"# {path}\n{_expand(by_path[path], values)}" for path in wanted)]


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
    plan = build_plan(choices, ref=ref, detected=Detected.from_facts(facts), verify=verify)
    return Prepared(plan=plan, context=kube.context, doctor=report, confirm=confirm_commands(plan, kube.context))


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
    return Prepared(plan=plan, context=kube.context, confirm=confirm_commands(plan, kube.context))


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
    recorded = warned = False
    async for event in run_plan(plan, kube, timeout=timeout, wait=True):
        match event:
            case StepFinished(ok=True, path=path) if path != "env":
                record.steps_done.append(path)
                saved = await state_store.save(kube, record)
                if not saved and (recorded or path == "prerequisites.namespace") and not warned:
                    warned = True
                    yield LogLine(
                        path=path, line="warning: could not write the llmdup-state ConfigMap; status and down will not see this stack"
                    )
                recorded = recorded or saved
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
        left = await leftovers(kube, plan)
        ok = not left
        if ok:
            await state_store.delete(kube, plan.namespace)
        elif records:
            await state_store.save(kube, state_store.mark(records[0], StackPhase.TEARING_DOWN, failed_step=event.failed_step))
        message = "nothing left deployed" if ok else f"left behind: {', '.join(left)}"
        yield event.model_copy(update={"ok": ok, "message": message, "tail": event.tail or left})


def _not_found(stderr: str) -> bool:
    return "(NotFound)" in stderr or " not found" in stderr


async def leftovers(kube: Kube, plan: Plan) -> list[str]:
    """Return what a teardown left behind; anything llmdup could not check counts as left.

    Returns:
        Human-readable leftovers; empty when the stack is gone.
    """
    release = _release(plan)
    if not any(s.reason == "--keep-namespace" for s in plan.skipped):
        found = await kube.run("kubectl", "get", "namespace", plan.namespace, "-o", "name", timeout=30)
        if not found.ok:
            return [] if _not_found(found.stderr) else [f"namespace {plan.namespace} could not be checked: {found.stderr.strip()[:200]}"]
        left = [f"namespace {plan.namespace}"]
    else:
        left = []
    helm = await kube.run("helm", "list", "-n", plan.namespace, "-o", "json", timeout=30)
    if not helm.ok:
        left.append(f"helm releases could not be listed: {helm.stderr.strip()[:200]}")
    elif f'"name":"{release}"' in helm.stdout.replace(" ", ""):
        left.append(f"helm release {release}")
    pods = await kube.json("kubectl", "get", "pods", "-n", plan.namespace, "-l", GUIDE_POD_LABEL, "-o", "json", timeout=30)
    match pods:
        case {"items": list(items)}:
            if alive := [p for p in items if isinstance(p, dict) and not p.get("metadata", {}).get("deletionTimestamp")]:
                left.append(f"{len(alive)} model server pods")
        case _:
            left.append("model server pods could not be listed")
    return left
