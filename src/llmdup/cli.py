# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
import sys
from collections.abc import Callable
from typing import Annotated

import typer
from pydantic import BaseModel

from llmdup import __version__
from llmdup.core.models import Envelope, ExitCode, LlmdupError, VersionInfo
from llmdup.core.upstream import DEFAULT_LLMD_REF

app = typer.Typer(
    help="From a Kubernetes cluster to a working llm-d endpoint.",
    invoke_without_command=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)

JsonOpt = Annotated[bool, typer.Option("--json", help="Print one JSON document on stdout.")]
ContextOpt = Annotated[str | None, typer.Option("--context", help="Kube context to use.")]
RefOpt = Annotated[str, typer.Option("--llmd-ref", help="llm-d release tag.")]


def _emit(command: str, data: BaseModel | None, *, json_out: bool, human: Callable[[], None]) -> None:
    if json_out:
        payload = data.model_dump(mode="json") if data is not None else None
        typer.echo(Envelope(ok=True, command=command, data=payload).model_dump_json())
    else:
        human()


def _fail(command: str, error: LlmdupError, *, json_out: bool, data: BaseModel | None = None) -> typer.Exit:
    if json_out:
        payload = data.model_dump(mode="json") if data is not None else None
        typer.echo(Envelope(ok=False, command=command, data=payload, errors=[error.info()]).model_dump_json())
    else:
        typer.echo(f"error: {error}", err=True)
        if error.hint:
            typer.echo(f"hint: {error.hint}", err=True)
    return typer.Exit(int(error.exit_code))


@app.callback()
def main(ctx: typer.Context, context: ContextOpt = None, llmd_ref: RefOpt = DEFAULT_LLMD_REF) -> None:
    """Open the terminal UI, or run a subcommand."""
    ctx.obj = {"context": context, "llmd_ref": llmd_ref}
    if ctx.invoked_subcommand is not None:
        return
    if not sys.stdout.isatty():
        typer.echo(ctx.get_help())
        raise typer.Exit(int(ExitCode.OK))
    typer.echo(ctx.get_help())


@app.command()
def version(json_out: JsonOpt = False, llmd_ref: RefOpt = DEFAULT_LLMD_REF) -> None:
    """Print the llmdup version and the pinned llm-d ref."""
    info = VersionInfo(llmdup=__version__, llmd_ref=llmd_ref)
    _emit("version", info, json_out=json_out, human=lambda: typer.echo(f"llmdup {info.llmdup} (llm-d {info.llmd_ref})"))
