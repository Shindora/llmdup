# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""MCP tools over the llmdup core. Import this module only from `llmdup mcp`; it loads the MCP SDK."""

import functools
import inspect
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from functools import singledispatch
from typing import cast

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel

from llmdup import __version__
from llmdup.core.detect import detect, pinned
from llmdup.core.doctor import doctor as core_doctor
from llmdup.core.guides import catalog
from llmdup.core.kube import Kube
from llmdup.core.models import (
    Action,
    Choices,
    DoctorReport,
    GuideInfo,
    GuideSummary,
    LlmdupError,
    LogLine,
    Plan,
    RunEvent,
    RunFinished,
    StatusReport,
    StepFinished,
    StepStarted,
    UsageError,
    WaitProgress,
)
from llmdup.core.planner import Detected, build_plan
from llmdup.core.runner import Prepared, confirm_commands, execute, prepare_recorded, prepare_up
from llmdup.core.status import status as core_status
from llmdup.core.upstream import DEFAULT_LLMD_REF

INSTRUCTIONS = """\
llmdup deploys llm-d guides (pinned llm-d release) to Kubernetes.
Typical flow: list_guides, show_guide, doctor, plan, up, status, test, down.
`up` and `down` change the cluster. Call them without `confirm` first: they return the plan, the kube `context`
and the exact commands, and change nothing. Show those to the user, and call again with `confirm: true` and
`kube_context` set to that `context` only after the user agrees. `test` returns the same preview when its tests
create cluster-scoped objects. HF_TOKEN is read from the server's environment, never passed in.
"""


class GuideList(BaseModel):
    """Output of `list_guides`, the same shape as `llmdup guides list --json` data."""

    guides: list[GuideSummary]


@singledispatch
def render(event: object) -> str | None:
    """Turn a runner event into one log message for the MCP client.

    Raises:
        NotImplementedError: No renderer is registered for the event's type.
    """
    msg = f"mcp cannot render {type(event).__name__}"
    raise NotImplementedError(msg)


@render.register
def _(event: StepStarted) -> str | None:
    return f"[{event.index}/{event.total}] {event.path}"


@render.register
def _(event: LogLine) -> str | None:
    return f"{event.path}: {event.line}"


@render.register
def _(event: StepFinished) -> str | None:
    return f"{event.path}: {'ok' if event.ok else f'failed with exit status {event.returncode}'} in {event.seconds}s"


@render.register
def _(event: WaitProgress) -> str | None:
    return event.message


@render.register
def _(event: RunFinished) -> str | None:
    return event.message


def _choices(
    guide: str,
    accelerator: str | None,
    engine: str | None,
    provider: str | None,
    mode: str | None,
    namespace: str | None,
    model: str | None,
    sets: dict[str, str] | None,
    options: dict[str, str] | None,
) -> Choices:
    return Choices(
        guide=guide,
        accelerator=accelerator,
        engine=engine,
        provider=provider,
        mode=mode,
        namespace=namespace,
        model=model,
        sets=sets or {},
        options=options or {},
    )


def _reported[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Turn llmdup errors into MCP tool errors, whose text reaches the agent."""

    def convert(error: LlmdupError) -> ToolError:
        hint = f" ({error.hint})" if error.hint else ""
        return ToolError(f"{error.code}: {error}{hint}")

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def run_async(*args: P.args, **kwargs: P.kwargs) -> object:
            try:
                return await fn(*args, **kwargs)
            except LlmdupError as error:
                raise convert(error) from error

        return cast("Callable[P, R]", run_async)

    @functools.wraps(fn)
    def run(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except LlmdupError as error:
            raise convert(error) from error

    return run


async def _drive(events: AsyncGenerator[RunEvent], ctx: Context) -> RunFinished:
    final: RunFinished | None = None
    done, total = 0, 1
    async with aclosing(events) as stream:
        async for event in stream:
            match event:
                case StepStarted(index=index, total=steps):
                    done, total = index - 1, steps
                case RunFinished():
                    final, done = event, total
            if (text := render(event)) is not None:
                await ctx.report_progress(done, total, text)
    if final is None:
        msg = "the run ended without a result"
        raise ValueError(msg)
    return final


class Tools:
    """The eight llmdup tools; `kube_factory` builds the command runner from a kube context name."""

    def __init__(self, kube_factory: Callable[..., Kube] = Kube) -> None:
        self.kube_factory = kube_factory

    async def kube(self, kube_context: str | None, *, confirm: bool = False) -> Kube:
        """Return a runner pinned to `kube_context`, or to the current context resolved once.

        Raises:
            UsageError: A confirmed change names no context, so it could land on a different cluster than its preview.
        """
        if confirm and not kube_context:
            msg = "confirm: true needs kube_context"
            raise UsageError(msg, hint="pass the `context` field of the preview you showed the user as kube_context")
        return await pinned(self.kube_factory(context=kube_context))

    def list_guides(self, *, section: str | None = None, llmd_ref: str = DEFAULT_LLMD_REF) -> GuideList:
        """List the guides of the pinned llm-d release with section, modes and support."""
        rows = catalog(llmd_ref).summaries()
        return GuideList(guides=[r for r in rows if section in {None, r.section}])

    def show_guide(self, guide: str, *, llmd_ref: str = DEFAULT_LLMD_REF) -> GuideInfo:
        """Show a guide's variables, defaults, allowed values, support matrix and step paths."""
        return catalog(llmd_ref).info(guide)

    async def doctor(
        self,
        *,
        guide: str | None = None,
        accelerator: str | None = None,
        engine: str | None = None,
        provider: str | None = None,
        mode: str | None = None,
        namespace: str | None = None,
        model: str | None = None,
        sets: dict[str, str] | None = None,
        options: dict[str, str] | None = None,
        kube_context: str | None = None,
        llmd_ref: str = DEFAULT_LLMD_REF,
    ) -> DoctorReport:
        """Run the preflight checks for the cluster, and for a guide with the same choices `up` takes. Read-only."""
        choices = _choices(guide, accelerator, engine, provider, mode, namespace, model, sets, options) if guide else None
        return await core_doctor(await self.kube(kube_context), choices, ref=llmd_ref)

    async def plan(
        self,
        guide: str,
        *,
        accelerator: str | None = None,
        engine: str | None = None,
        provider: str | None = None,
        mode: str | None = None,
        namespace: str | None = None,
        model: str | None = None,
        sets: dict[str, str] | None = None,
        options: dict[str, str] | None = None,
        kube_context: str | None = None,
        llmd_ref: str = DEFAULT_LLMD_REF,
    ) -> Plan:
        """Return the ordered steps and exact commands of a deploy; runs nothing. With kube_context, unset choices come from detection."""
        detected = None
        if kube_context:
            facts = await detect(await self.kube(kube_context), timeout=10)
            detected = Detected.from_facts(facts)
        choices = _choices(guide, accelerator, engine, provider, mode, namespace, model, sets, options)
        return build_plan(choices, ref=llmd_ref, detected=detected)

    async def up(
        self,
        guide: str,
        ctx: Context,
        *,
        accelerator: str | None = None,
        engine: str | None = None,
        provider: str | None = None,
        mode: str | None = None,
        namespace: str | None = None,
        model: str | None = None,
        sets: dict[str, str] | None = None,
        options: dict[str, str] | None = None,
        verify: bool = True,
        confirm: bool = False,
        timeout: float = 1800,
        kube_context: str | None = None,
        llmd_ref: str = DEFAULT_LLMD_REF,
    ) -> Prepared | RunFinished:
        """Deploy a guide. Without `confirm: true` it changes nothing and returns the plan, context, preflight report and commands."""
        kube = await self.kube(kube_context, confirm=confirm)
        choices = _choices(guide, accelerator, engine, provider, mode, namespace, model, sets, options)
        prepared = await prepare_up(kube, choices, ref=llmd_ref, verify=verify, gate=confirm)
        if not confirm:
            every = confirm_commands(prepared.plan, kube.context, [s.path for s in prepared.plan.steps])
            return prepared.model_copy(update={"confirm": every})
        return await _drive(execute(kube, prepared, confirmed=True, timeout=timeout), ctx)

    async def status(self, *, namespace: str | None = None, kube_context: str | None = None) -> StatusReport:
        """Show deployed stacks with their pods, endpoint and readiness. Read-only."""
        return await core_status(await self.kube(kube_context), namespace)

    async def test(
        self,
        ctx: Context,
        *,
        namespace: str | None = None,
        confirm: bool = False,
        timeout: float = 600,
        kube_context: str | None = None,
    ) -> Prepared | RunFinished:
        """Run the guide's verify.tests steps. Sends inference requests; tests with cluster-scoped steps need `confirm: true`."""
        kube = await self.kube(kube_context, confirm=confirm)
        prepared = await prepare_recorded(kube, Action.TEST, namespace)
        if prepared.confirm and not confirm:
            return prepared
        return await _drive(execute(kube, prepared, confirmed=True, timeout=timeout), ctx)

    async def down(
        self,
        ctx: Context,
        *,
        namespace: str | None = None,
        keep_namespace: bool = False,
        confirm: bool = False,
        timeout: float = 900,
        kube_context: str | None = None,
    ) -> Prepared | RunFinished:
        """Tear a stack down. Without `confirm: true` it deletes nothing and returns the plan, context and commands to confirm."""
        kube = await self.kube(kube_context, confirm=confirm)
        prepared = await prepare_recorded(kube, Action.DOWN, namespace, keep_namespace=keep_namespace)
        if not confirm:
            return prepared
        return await _drive(execute(kube, prepared, confirmed=True, timeout=timeout), ctx)


TOOL_NAMES = ("list_guides", "show_guide", "doctor", "plan", "up", "status", "test", "down")


def build_server(kube_factory: Callable[..., Kube] = Kube) -> MCPServer:
    """Return the llmdup MCP server with its eight tools.

    Args:
        kube_factory: Builds the command runner from a kube context name; tests pass a fake.

    Returns:
        The server; call `.run()` to serve it on stdio.
    """
    server = MCPServer("llmdup", instructions=INSTRUCTIONS, version=__version__)
    tools = Tools(kube_factory)
    for name in TOOL_NAMES:
        server.add_tool(_reported(getattr(tools, name)), name=name)
    return server
