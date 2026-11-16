# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
import asyncio
import sys
from collections.abc import AsyncGenerator, Callable, Generator, Sequence
from contextlib import aclosing, contextmanager
from functools import singledispatch
from typing import Annotated

import typer
from pydantic import BaseModel

from llmdup import __version__
from llmdup.core import local as local_core
from llmdup.core.doctor import doctor as run_doctor
from llmdup.core.guides import catalog
from llmdup.core.kube import Kube
from llmdup.core.models import (
    Action,
    CheckResult,
    CheckStatus,
    Choices,
    ConfirmationNeededError,
    Envelope,
    ErrorInfo,
    ExitCode,
    GuideSummary,
    LlmdupError,
    LogLine,
    Plan,
    RunEvent,
    RunFinished,
    StackStatus,
    StatusReport,
    StepFinished,
    StepStarted,
    UsageError,
    VersionInfo,
    WaitProgress,
)
from llmdup.core.planner import build_plan
from llmdup.core.runner import Prepared, execute, prepare_recorded, prepare_up
from llmdup.core.status import status as run_status
from llmdup.core.upstream import DEFAULT_LLMD_REF

app = typer.Typer(
    help="From a Kubernetes cluster to a working llm-d endpoint.",
    invoke_without_command=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
guides_app = typer.Typer(help="List and describe the guides of the pinned llm-d release.", no_args_is_help=True)
app.add_typer(guides_app, name="guides")
local_app = typer.Typer(help="A local kind cluster with the llm-d simulator, no GPU needed.", no_args_is_help=True)
app.add_typer(local_app, name="local")

JsonOpt = Annotated[bool, typer.Option("--json", help="Print one JSON document on stdout.")]
ContextOpt = Annotated[str | None, typer.Option("--context", help="Kube context to use.")]
RefOpt = Annotated[str | None, typer.Option("--llmd-ref", help=f"llm-d release tag (default {DEFAULT_LLMD_REF}).")]
GuideArg = Annotated[str, typer.Argument(help="Guide name, as listed by `llmdup guides list`.")]
AcceleratorOpt = Annotated[str | None, typer.Option("--accelerator", help="ACCELERATOR_TYPE, such as gpu or tpu/v6.")]
EngineOpt = Annotated[str | None, typer.Option("--engine", help="MODEL_SERVER, such as vllm or sglang.")]
ProviderOpt = Annotated[str | None, typer.Option("--provider", help="INFRA_PROVIDER, such as base or gke.")]
ModeOpt = Annotated[str | None, typer.Option("--mode", help="Endpoint mode: standalone or gateway.")]
NamespaceOpt = Annotated[str | None, typer.Option("--namespace", "-n", help="Namespace for the stack.")]
ModelOpt = Annotated[str | None, typer.Option("--model", help="MODEL to serve.")]
SetOpt = Annotated[list[str] | None, typer.Option("--set", help="NAME=VALUE for any other env.static variable.")]
OptionOpt = Annotated[
    list[str] | None,
    typer.Option("--option", help="GROUP=CHOICE for a manual option, such as modelserver=dynamic_slice."),
]


class GuideList(BaseModel):
    """Output of `llmdup guides list`."""

    guides: list[GuideSummary]


@singledispatch
def render(item: object) -> None:
    """Print one core result or event to the terminal.

    Raises:
        NotImplementedError: No renderer is registered for the item's type.
    """
    msg = f"cli cannot render {type(item).__name__}"
    raise NotImplementedError(msg)


CHECK_MARKS = {CheckStatus.OK: "ok  ", CheckStatus.WARN: "warn", CheckStatus.FAIL: "FAIL", CheckStatus.SKIP: "skip"}


@render.register
def _(item: CheckResult) -> None:
    typer.echo(f"[{CHECK_MARKS[item.status]}] {item.name}: {item.found}")
    if item.fix and item.status in {CheckStatus.WARN, CheckStatus.FAIL}:
        typer.echo(f"       fix: {item.fix}")


@render.register
def _(item: StepStarted) -> None:
    typer.echo(f"==> [{item.index}/{item.total}] {item.path}", err=True)


@render.register
def _(item: LogLine) -> None:
    typer.echo(f"    {item.line}", err=True)


@render.register
def _(item: StepFinished) -> None:
    result = "ok" if item.ok else f"FAILED (exit {item.returncode})"
    typer.echo(f"    {result} in {item.seconds}s", err=True)


@render.register
def _(item: WaitProgress) -> None:
    typer.echo(f"... {item.message}", err=True)


@render.register
def _(item: RunFinished) -> None:
    typer.echo(f"{'ok' if item.ok else 'FAILED'}: {item.message}")
    if not item.ok and item.command:
        typer.echo(f"failing command:\n{item.command}")
    if not item.ok and item.tail:
        typer.echo("last log lines:\n" + "\n".join(f"  {line}" for line in item.tail))


@render.register
def _(item: StackStatus) -> None:
    typer.echo(f"{item.guide}  namespace {item.namespace}  {item.health}  ({item.phase}, profile {item.profile}, mode {item.mode or '-'})")
    release = f"{item.release.name} {item.release.status}" if item.release else "none"
    typer.echo(f"  release: {release}   llm-d {item.llmd_ref}")
    typer.echo(f"  endpoint: {item.endpoint or '-'}")
    if item.port_forward:
        typer.echo(f"  reach it: {item.port_forward}")
    for pod in item.pods:
        typer.echo(f"  {'ready' if pod.ready else 'wait '}  {pod.role:<12}  {pod.name}  {pod.phase}  restarts {pod.restarts}")
    if item.failed_step:
        typer.echo(f"  failed step: {item.failed_step}")


@render.register
def _(item: StatusReport) -> None:
    typer.echo(f"context: {item.context or '(none)'}")
    if not item.stacks:
        typer.echo("nothing deployed by llmdup")
    for stack in item.stacks:
        render(stack)


def _kube(ctx: typer.Context, context: str | None) -> Kube:
    return Kube(context=context or (ctx.obj or {}).get("context"))


def _ref(ctx: typer.Context, value: str | None) -> str:
    return value or (ctx.obj or {}).get("llmd_ref") or DEFAULT_LLMD_REF


def _dump(data: BaseModel | None) -> object:
    return data.model_dump(mode="json") if data is not None else None


def _emit(command: str, data: BaseModel | None, *, json_out: bool, human: Callable[[], None]) -> None:
    if json_out:
        typer.echo(Envelope(ok=True, command=command, data=_dump(data)).model_dump_json())
    else:
        human()


@contextmanager
def _handled(command: str, *, json_out: bool) -> Generator[None]:
    try:
        yield
    except LlmdupError as error:
        if json_out:
            typer.echo(Envelope(ok=False, command=command, errors=[error.info()]).model_dump_json())
        else:
            typer.echo(f"error: {error}", err=True)
            if error.hint:
                typer.echo(f"hint: {error.hint}", err=True)
        raise typer.Exit(int(error.exit_code)) from error


def _pairs(items: Sequence[str] | None, flag: str) -> dict[str, str]:
    out = {}
    for item in items or []:
        name, sep, value = item.partition("=")
        if not sep or not name:
            msg = f"{flag} expects NAME=VALUE, got {item!r}"
            raise UsageError(msg)
        out[name] = value
    return out


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [max(len(str(c)) for c in col) for col in zip(headers, *rows, strict=False)]
    lines = ["  ".join(str(c).ljust(w) for c, w in zip(row, widths, strict=False)).rstrip() for row in [headers, *rows]]
    return "\n".join(lines)


@app.callback()
def main(ctx: typer.Context, context: ContextOpt = None, llmd_ref: RefOpt = None) -> None:
    """Open the terminal UI, or run a subcommand."""
    ctx.obj = {"context": context, "llmd_ref": llmd_ref or DEFAULT_LLMD_REF}
    if ctx.invoked_subcommand is not None:
        return
    typer.echo(ctx.get_help())
    raise typer.Exit(int(ExitCode.OK))


@app.command()
def version(ctx: typer.Context, json_out: JsonOpt = False, llmd_ref: RefOpt = None) -> None:
    """Print the llmdup version and the pinned llm-d ref."""
    info = VersionInfo(llmdup=__version__, llmd_ref=_ref(ctx, llmd_ref))
    _emit("version", info, json_out=json_out, human=lambda: typer.echo(f"llmdup {info.llmdup} (llm-d {info.llmd_ref})"))


@guides_app.command("list")
def guides_list(
    ctx: typer.Context,
    section: Annotated[str | None, typer.Option("--section", help="Only guides of this section.")] = None,
    json_out: JsonOpt = False,
    llmd_ref: RefOpt = None,
) -> None:
    """List the guides with their section, modes and support."""
    with _handled("guides list", json_out=json_out):
        rows = [r for r in catalog(_ref(ctx, llmd_ref)).summaries() if section in {None, r.section}]
        data = GuideList(guides=rows)

        def human() -> None:
            table = [
                [r.name, r.section, r.title, ",".join(r.modes) or "-", f"{r.accelerator}+{r.engine}: {r.support}" if r.support else "-"]
                for r in rows
            ]
            typer.echo(_table(["GUIDE", "SECTION", "WHAT IT DOES", "MODES", "SUPPORT"], table))

        _emit("guides list", data, json_out=json_out, human=human)


@guides_app.command("show")
def guides_show(ctx: typer.Context, guide: GuideArg, json_out: JsonOpt = False, llmd_ref: RefOpt = None) -> None:
    """Show a guide's variables, support matrix and step paths."""
    with _handled("guides show", json_out=json_out):
        info = catalog(_ref(ctx, llmd_ref)).info(guide)

        def human() -> None:
            typer.echo(f"{info.name}: {info.title}\nsection: {info.section}   dir: {info.dir}   modes: {', '.join(info.modes) or '-'}\n")
            rows = [[v.name, "<sensitive>" if v.sensitive else (v.default or ""), ", ".join(v.values or [])] for v in info.variables]
            typer.echo(_table(["VARIABLE", "DEFAULT", "ALLOWED"], rows))
            if info.support:
                typer.echo("")
                engines = list(info.support.engines)
                srows = [[a.name, *(a.engines.get(e, "-") for e in engines), ",".join(a.providers)] for a in info.support.accelerators]
                typer.echo(_table(["ACCELERATOR", *engines, "PROVIDERS"], srows))
            typer.echo("")
            typer.echo(_table(["STEP", "TAGS"], [[s.path, ", ".join(s.tags)] for s in info.steps]))

        _emit("guides show", info, json_out=json_out, human=human)


def _choices(guide: str, flags: dict[str, str | None], sets: list[str] | None, options: list[str] | None) -> Choices:
    return Choices(guide=guide, sets=_pairs(sets, "--set"), options=_pairs(options, "--option"), **flags)


def _print_plan(plan: Plan) -> None:
    typer.echo(f"plan: {plan.action} {plan.guide}  namespace {plan.namespace}  mode {plan.mode or '-'}  llm-d {plan.llmd_ref}\n")
    rows = [[str(i), s.path, ", ".join(s.tags)] for i, s in enumerate(plan.steps, 1)]
    typer.echo(_table(["#", "STEP", "TAGS"], rows))
    if plan.needs_confirmation:
        typer.echo(f"\nneeds confirmation: {', '.join(plan.needs_confirmation)}")
    if plan.skipped:
        typer.echo("\nskipped:")
        for s in plan.skipped:
            typer.echo(f"  {s.path}: {s.reason}")


@app.command()
def plan(
    ctx: typer.Context,
    guide: GuideArg,
    accelerator: AcceleratorOpt = None,
    engine: EngineOpt = None,
    provider: ProviderOpt = None,
    mode: ModeOpt = None,
    namespace: NamespaceOpt = None,
    model: ModelOpt = None,
    sets: SetOpt = None,
    options: OptionOpt = None,
    script: Annotated[bool, typer.Option("--script", help="Print the plan as one bash script.")] = False,
    json_out: JsonOpt = False,
    llmd_ref: RefOpt = None,
) -> None:
    """Print the ordered steps of a deploy; run nothing."""
    with _handled("plan", json_out=json_out):
        flags = {"accelerator": accelerator, "engine": engine, "provider": provider, "mode": mode, "namespace": namespace, "model": model}
        result = build_plan(_choices(guide, flags, sets, options), ref=_ref(ctx, llmd_ref))
        if script:
            typer.echo(result.script, nl=False)
            return
        _emit("plan", result, json_out=json_out, human=lambda: _print_plan(result))


@app.command()
def doctor(
    ctx: typer.Context,
    guide: Annotated[str | None, typer.Argument(help="Also check this guide's plan.")] = None,
    accelerator: AcceleratorOpt = None,
    engine: EngineOpt = None,
    provider: ProviderOpt = None,
    mode: ModeOpt = None,
    namespace: NamespaceOpt = None,
    model: ModelOpt = None,
    sets: SetOpt = None,
    options: OptionOpt = None,
    json_out: JsonOpt = False,
    context: ContextOpt = None,
    llmd_ref: RefOpt = None,
) -> None:
    """Run the preflight checks; exit 3 when any check fails."""
    with _handled("doctor", json_out=json_out):
        flags = {"accelerator": accelerator, "engine": engine, "provider": provider, "mode": mode, "namespace": namespace, "model": model}
        choices = _choices(guide, flags, sets, options) if guide else None
        report = asyncio.run(run_doctor(_kube(ctx, context), choices, ref=_ref(ctx, llmd_ref)))
    failed = [c for c in report.checks if c.status is CheckStatus.FAIL]
    if json_out:
        errors = [ErrorInfo(code="check", message=f"{c.name}: {c.found}", hint=c.fix) for c in failed]
        typer.echo(Envelope(ok=report.ok, command="doctor", data=_dump(report), errors=errors).model_dump_json())
    else:
        typer.echo(f"context: {report.facts.context or '(none)'}   guide: {report.guide or '-'}")
        for check in report.checks:
            render(check)
    if failed:
        raise typer.Exit(int(ExitCode.DOCTOR_FAILED))


def _confirm(prepared: Prepared, *, yes: bool) -> bool:
    if yes or not prepared.confirm:
        return True
    if not sys.stdin.isatty():
        return False
    typer.echo("These steps need confirmation:\n", err=True)
    for command in prepared.confirm:
        typer.echo(command + "\n", err=True)
    return typer.confirm("Run them?", default=False, err=True)


async def _drive(events: AsyncGenerator[RunEvent], *, json_out: bool) -> RunFinished:
    final: RunFinished | None = None
    async with aclosing(events) as stream:
        async for event in stream:
            if isinstance(event, RunFinished):
                final = event
            elif json_out:
                render(event) if not isinstance(event, LogLine) else typer.echo(event.line, err=True)
            else:
                render(event)
    if final is None:
        msg = "the run ended without a result"
        raise LlmdupError(msg)
    return final


def _finish(command: str, final: RunFinished, *, json_out: bool) -> None:
    if json_out:
        errors = [] if final.ok else [ErrorInfo(code="step-failed", message=final.message)]
        typer.echo(Envelope(ok=final.ok, command=command, data=_dump(final), errors=errors).model_dump_json())
    else:
        render(final)
    if not final.ok:
        raise typer.Exit(int(ExitCode.STEP_FAILED))


async def _run(kube: Kube, prepared: Prepared, *, yes: bool, json_out: bool, timeout: float) -> RunFinished:
    if not _confirm(prepared, yes=yes):
        hint = "\n\n".join(prepared.confirm)
        msg = f"confirmation needed for: {', '.join(prepared.plan.needs_confirmation)}"
        raise ConfirmationNeededError(msg, hint=f"pass --yes to run:\n{hint}")
    return await _drive(execute(kube, prepared, confirmed=True, timeout=timeout), json_out=json_out)


YesOpt = Annotated[bool, typer.Option("--yes", "-y", help="Confirm cluster-scoped steps and deletes.")]
TimeoutOpt = Annotated[float, typer.Option("--timeout", help="Seconds for the whole run.")]


@app.command()
def up(
    ctx: typer.Context,
    guide: GuideArg,
    accelerator: AcceleratorOpt = None,
    engine: EngineOpt = None,
    provider: ProviderOpt = None,
    mode: ModeOpt = None,
    namespace: NamespaceOpt = None,
    model: ModelOpt = None,
    sets: SetOpt = None,
    options: OptionOpt = None,
    yes: YesOpt = False,
    no_verify: Annotated[bool, typer.Option("--no-verify", help="Skip the smoke test.")] = False,
    timeout: TimeoutOpt = 1800,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print the commands; run nothing.")] = False,
    json_out: JsonOpt = False,
    context: ContextOpt = None,
    llmd_ref: RefOpt = None,
) -> None:
    """Deploy a guide: run the plan, wait for ready pods, then send one test request."""
    with _handled("up", json_out=json_out):
        flags = {"accelerator": accelerator, "engine": engine, "provider": provider, "mode": mode, "namespace": namespace, "model": model}
        kube = _kube(ctx, context)
        choices = _choices(guide, flags, sets, options)
        prepared = asyncio.run(prepare_up(kube, choices, ref=_ref(ctx, llmd_ref), verify=not no_verify, gate=not dry_run))
        if dry_run:
            if json_out:
                typer.echo(Envelope(ok=True, command="up", data=_dump(prepared)).model_dump_json())
            else:
                typer.echo(prepared.plan.script, nl=False)
            return
        final = asyncio.run(_run(kube, prepared, yes=yes, json_out=json_out, timeout=timeout))
    _finish("up", final, json_out=json_out)


@app.command()
def status(
    ctx: typer.Context,
    namespace: NamespaceOpt = None,
    watch: Annotated[bool, typer.Option("--watch", "-w", help="Refresh every 2 seconds until ctrl-c.")] = False,
    json_out: JsonOpt = False,
    context: ContextOpt = None,
) -> None:
    """Show deployed stacks, their pods, endpoint and readiness."""
    with _handled("status", json_out=json_out):
        if watch and json_out:
            msg = "--watch and --json cannot be combined"
            raise UsageError(msg)
        kube = _kube(ctx, context)
        report = asyncio.run(run_status(kube, namespace))
        _emit("status", report, json_out=json_out, human=lambda: render(report))
        while watch:
            asyncio.run(asyncio.sleep(2))
            report = asyncio.run(run_status(kube, namespace))
            typer.echo("\x1b[2J\x1b[H", nl=False)
            render(report)


@app.command("test")
def rerun_tests(
    ctx: typer.Context,
    namespace: NamespaceOpt = None,
    timeout: TimeoutOpt = 600,
    json_out: JsonOpt = False,
    context: ContextOpt = None,
) -> None:
    """Run the guide's verify.tests steps again against a deployed stack."""
    with _handled("test", json_out=json_out):
        kube = _kube(ctx, context)
        prepared = asyncio.run(prepare_recorded(kube, Action.TEST, namespace))
        final = asyncio.run(_run(kube, prepared, yes=True, json_out=json_out, timeout=timeout))
    _finish("test", final, json_out=json_out)


@app.command()
def down(
    ctx: typer.Context,
    namespace: NamespaceOpt = None,
    yes: YesOpt = False,
    keep_namespace: Annotated[bool, typer.Option("--keep-namespace", help="Leave the namespace in place.")] = False,
    timeout: TimeoutOpt = 900,
    json_out: JsonOpt = False,
    context: ContextOpt = None,
) -> None:
    """Tear a stack down with the guide's cleanup steps."""
    with _handled("down", json_out=json_out):
        kube = _kube(ctx, context)
        prepared = asyncio.run(prepare_recorded(kube, Action.DOWN, namespace, keep_namespace=keep_namespace))
        final = asyncio.run(_run(kube, prepared, yes=yes, json_out=json_out, timeout=timeout))
    _finish("down", final, json_out=json_out)


@local_app.command("up")
def local_up(
    yes: YesOpt = False,
    no_verify: Annotated[bool, typer.Option("--no-verify", help="Skip the smoke test.")] = False,
    timeout: TimeoutOpt = 1800,
    json_out: JsonOpt = False,
) -> None:
    """Create the kind cluster `llmdup` if needed and deploy optimized-baseline with the simulator."""
    with _handled("local up", json_out=json_out):
        kube = local_core.local_kube()

        async def flow() -> RunFinished:
            async for event in local_core.create_cluster(kube):
                render(event)
                if isinstance(event, StepFinished) and not event.ok:
                    msg = "kind create cluster failed"
                    raise LlmdupError(msg, hint="is Docker or Podman running? For Podman set KIND_EXPERIMENTAL_PROVIDER=podman")
            prepared = await prepare_up(kube, local_core.local_choices(), verify=not no_verify)
            return await _run(kube, prepared, yes=yes, json_out=json_out, timeout=timeout)

        final = asyncio.run(flow())
    _finish("local up", final, json_out=json_out)


@local_app.command("down")
def local_down(yes: YesOpt = False, json_out: JsonOpt = False) -> None:
    """Delete the kind cluster `llmdup`."""
    with _handled("local down", json_out=json_out):
        if not yes and sys.stdin.isatty():
            yes = typer.confirm(f"Delete the kind cluster {local_core.CLUSTER}?", default=False, err=True)
        final = asyncio.run(_drive(local_core.delete_cluster(local_core.local_kube(), confirmed=yes), json_out=json_out))
    _finish("local down", final, json_out=json_out)
