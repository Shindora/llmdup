# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""The only module in llmdup that starts subprocesses."""

import asyncio
import contextlib
import json
import os
import shutil
import signal
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

TOOLS = frozenset({"kubectl", "helm", "kind", "bash"})
NOT_FOUND = 127
TIMED_OUT = 124
LINE_LIMIT = 4 * 1024 * 1024
CONTEXT_PRELUDE = """\
if [[ -n "${LLMDUP_CONTEXT:-}" ]]; then
  kubectl() { command kubectl --context "$LLMDUP_CONTEXT" "$@"; }
  helm() { command helm --kube-context "$LLMDUP_CONTEXT" "$@"; }
  export -f kubectl helm
fi
"""


@dataclass(frozen=True)
class CommandResult:
    """Exit status and captured output of one command."""

    args: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        """Whether the command exited with status 0."""
        return self.returncode == 0


class CommandFailedError(Exception):
    """A streamed command exited with a non-zero status or timed out."""

    def __init__(self, args: tuple[str, ...], returncode: int) -> None:
        super().__init__(f"{args[0]} exited with status {returncode}")
        self.args_ = args
        self.returncode = returncode


@dataclass
class Kube:
    """Runs kubectl, helm, kind and bash, pinned to one kube context when given.

    Bash scripts get shell functions that add the context to every `kubectl` and `helm` they call.
    """

    context: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)

    def which(self, tool: str) -> str | None:
        """Return the path of a tool on PATH, if any."""
        return shutil.which(tool)

    def argv(self, tool: str, args: tuple[str, ...]) -> tuple[str, ...]:
        """Return the full command line for a tool, with the context flag added.

        Raises:
            ValueError: `tool` is not one llmdup may run.
        """
        if tool not in TOOLS:
            msg = f"llmdup does not run {tool!r}"
            raise ValueError(msg)
        match tool:
            case "kubectl" if self.context:
                return (tool, "--context", self.context, *args)
            case "helm" if self.context:
                return (tool, "--kube-context", self.context, *args)
            case "bash" if args:
                return (tool, "-c", CONTEXT_PRELUDE + args[0], *args[1:])
        return (tool, *args)

    def process_env(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return the environment for a child process."""
        env = {**os.environ, **self.env, **(extra or {})}
        env.pop("LLMDUP_CONTEXT", None)
        if self.context:
            env["LLMDUP_CONTEXT"] = self.context
        return env

    async def run(
        self,
        tool: str,
        *args: str,
        stdin: str | None = None,
        timeout: float = 60,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """Run a command to completion and capture its output."""
        argv = self.argv(tool, args)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self.process_env(env),
                start_new_session=True,
            )
        except FileNotFoundError:
            return CommandResult(argv, NOT_FOUND, stderr=f"{tool}: command not found")
        try:
            async with asyncio.timeout(timeout):
                out, err = await proc.communicate(stdin.encode() if stdin is not None else None)
        except TimeoutError:
            await _kill(proc)
            return CommandResult(argv, TIMED_OUT, stderr=f"timed out after {timeout:g}s")
        finally:
            await _kill(proc)
        return CommandResult(argv, proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace"))

    async def json(self, tool: str, *args: str, timeout: float = 60) -> object:
        """Run a command that prints JSON and parse it; `None` when it fails."""
        result = await self.run(tool, *args, timeout=timeout)
        if not result.ok:
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return None

    async def stream(
        self,
        tool: str,
        *args: str,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
    ) -> AsyncIterator[str]:
        """Yield the merged stdout and stderr lines of a command as they arrive.

        Raises:
            CommandFailedError: The command exited non-zero, timed out, or is missing.
            RuntimeError: The output pipe was not created.
        """
        argv = self.argv(tool, args)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self.process_env(env),
                cwd=cwd,
                start_new_session=True,
                limit=LINE_LIMIT,
            )
        except FileNotFoundError as exc:
            raise CommandFailedError(argv, NOT_FOUND) from exc
        if proc.stdout is None:
            msg = "stdout pipe missing"
            raise RuntimeError(msg)
        deadline = asyncio.get_running_loop().time() + timeout if timeout else None
        try:
            while True:
                try:
                    async with asyncio.timeout_at(deadline):
                        raw = await proc.stdout.readline()
                except TimeoutError as exc:
                    raise CommandFailedError(argv, TIMED_OUT) from exc
                if not raw:
                    break
                yield raw.decode(errors="replace").rstrip("\r\n")
            code = await proc.wait()
        finally:
            await _kill(proc)
        if code != 0:
            raise CommandFailedError(argv, code)


def _signal(proc: asyncio.subprocess.Process, sig: signal.Signals) -> None:
    try:
        os.killpg(proc.pid, sig)
    except PermissionError:
        # macOS answers EPERM, not ESRCH, when only zombies are left in the group
        with contextlib.suppress(ProcessLookupError):
            proc.send_signal(sig)
    except ProcessLookupError:
        pass


async def _kill(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    _signal(proc, signal.SIGTERM)
    try:
        async with asyncio.timeout(5):
            await proc.wait()
    except TimeoutError:
        _signal(proc, signal.SIGKILL)
        await proc.wait()


@dataclass
class FakeKube(Kube):
    """A `Kube` that answers from a table instead of starting processes.

    Keys are argument prefixes without the context flag, such as `("kubectl", "get", "nodes")`.
    The longest matching prefix wins; unmatched commands fail with status 1.
    """

    responses: dict[tuple[str, ...], CommandResult | str] = field(default_factory=dict)
    calls: list[tuple[str, ...]] = field(default_factory=list)
    tools: frozenset[str] = TOOLS

    def which(self, tool: str) -> str | None:
        """Return a fake path for tools listed in `tools`."""
        return f"/usr/bin/{tool}" if tool in self.tools else None

    def answer(self, key: tuple[str, ...]) -> CommandResult:
        """Return the canned result for a command."""
        self.calls.append(key)
        best = max((k for k in self.responses if key[: len(k)] == k), key=len, default=None)
        if best is None:
            return CommandResult(key, 1, stderr="fake: no response")
        found = self.responses[best]
        return CommandResult(key, 0, found) if isinstance(found, str) else found

    async def run(
        self,
        tool: str,
        *args: str,
        stdin: str | None = None,
        timeout: float = 60,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        """Answer from the table."""
        del stdin, timeout, env
        return self.answer((tool, *args))

    async def stream(
        self,
        tool: str,
        *args: str,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
    ) -> AsyncIterator[str]:
        """Yield the canned stdout line by line.

        Raises:
            CommandFailedError: The canned result has a non-zero status.
        """
        del timeout, env, cwd
        result = self.answer((tool, *args))
        for line in result.stdout.splitlines():
            yield line
        if not result.ok:
            raise CommandFailedError(result.args, result.returncode)
