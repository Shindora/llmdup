# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""A deploy, test or tear down while it runs, and the TUI's `render` of runner events."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from functools import singledispatch

from llmdup.core.models import Action, LogLine, Plan, RunEvent, RunFinished, StepFinished, StepStarted, WaitProgress
from llmdup.core.runner import Prepared

WAIT_ROW = "wait: pods ready"
RESULT = "result"
VERBS = {Action.UP: "deploy", Action.TEST: "test", Action.DOWN: "down"}

type Listener = Callable[[RunEvent | None], None]


class RowState(StrEnum):
    """Where one step of a run is."""

    NEXT = "○ next"
    NOW = "▶ now"
    DONE = "✓ done"
    FAILED = "✗ fail"
    STOPPED = "■ stop"


class LogKind(StrEnum):
    """What a log line is, so the views can colour it."""

    COMMAND = "command"
    OUTPUT = "output"
    LLMDUP = "llmdup"
    ERROR = "error"


@dataclass
class RunRow:
    """One step of a run as the deploy view lists it."""

    path: str
    state: RowState = RowState.NEXT
    seconds: float | None = None
    started: float | None = None


@dataclass(frozen=True)
class LogEntry:
    """One line of the run's log."""

    path: str
    text: str
    kind: LogKind
    at: datetime


@dataclass
class DeployRun:
    """The state of one run, fed by `render` and read by the deploy, logs and stack views."""

    prepared: Prepared
    clock: Callable[[], float]
    now: Callable[[], datetime]
    rows: list[RunRow]
    started: float
    ended: float | None = None
    log: list[LogEntry] = field(default_factory=list)
    result: RunFinished | None = None
    error: str | None = None

    @classmethod
    def create(cls, prepared: Prepared, clock: Callable[[], float], now: Callable[[], datetime]) -> DeployRun:
        """Lay out the rows of a run: the plan's steps, with the readiness wait before the first verify step."""
        rows: list[RunRow] = []
        waited = prepared.plan.action is not Action.UP
        for step in prepared.plan.steps:
            if not waited and step.path.startswith("verify."):
                waited = True
                rows.append(RunRow(WAIT_ROW))
            rows.append(RunRow(step.path))
        return cls(prepared=prepared, clock=clock, now=now, rows=rows, started=clock())

    @property
    def plan(self) -> Plan:
        """The plan being run."""
        return self.prepared.plan

    @property
    def verb(self) -> str:
        """`deploy`, `test` or `down`."""
        return VERBS[self.plan.action]

    @property
    def running(self) -> bool:
        """Whether the run has not ended."""
        return self.ended is None

    @property
    def ok(self) -> bool:
        """Whether the run ended and succeeded."""
        return self.result is not None and self.result.ok

    @property
    def done(self) -> int:
        """How many rows have ended."""
        return sum(r.state in {RowState.DONE, RowState.FAILED} for r in self.rows)

    def row(self, path: str) -> RunRow | None:
        """Return the row of a step path."""
        return next((r for r in self.rows if r.path == path), None)

    def current(self) -> RunRow | None:
        """Return the row that is running now."""
        return next((r for r in self.rows if r.state is RowState.NOW), None)

    def write(self, path: str, text: str, kind: LogKind) -> None:
        """Append one line to the log."""
        self.log.append(LogEntry(path=path, text=text, kind=kind, at=self.now()))

    def lines_of(self, path: str) -> list[LogEntry]:
        """Return the log lines of one step."""
        return [e for e in self.log if e.path == path]

    def elapsed(self) -> float:
        """Seconds since the run started, frozen when it ends."""
        return (self.ended if self.ended is not None else self.clock()) - self.started

    def row_seconds(self, row: RunRow) -> float | None:
        """Seconds a row took, or has taken so far."""
        if row.seconds is not None:
            return row.seconds
        return self.clock() - row.started if row.started is not None else None

    def end(self, *, error: str | None = None, stopped: bool = False) -> None:
        """Mark the run as ended, by its result, an error or a cancel."""
        self.ended = self.clock()
        self.error = error
        for row in self.rows:
            if row.state is RowState.NOW:
                row.state = RowState.STOPPED if stopped else RowState.FAILED
                row.seconds = self.row_seconds(row)
        if error:
            self.write(RESULT, error, LogKind.ERROR)


def _close_wait(run: DeployRun, *, ok: bool) -> None:
    row = run.row(WAIT_ROW)
    if row is not None and row.state is RowState.NOW:
        row.state = RowState.DONE if ok else RowState.FAILED
        row.seconds = run.row_seconds(row)


def first_line(command: str) -> str:
    """Return the first line of a command that is not a comment, marked when more lines follow."""
    lines = [line.strip() for line in command.splitlines() if line.strip() and not line.strip().startswith("#")]
    if not lines:
        return command.strip()
    return lines[0] + (" …" if len(lines) > 1 else "")


@singledispatch
def render(event: object, run: DeployRun) -> None:
    """Fold one runner event into the run the views draw.

    Raises:
        NotImplementedError: No renderer is registered for the event's type.
    """
    del run
    msg = f"tui cannot render {type(event).__name__}"
    raise NotImplementedError(msg)


@render.register
def _(event: StepStarted, run: DeployRun) -> None:
    _close_wait(run, ok=True)
    if (row := run.row(event.path)) is not None:
        row.state, row.started = RowState.NOW, run.clock()
    run.write(event.path, first_line(event.command), LogKind.COMMAND if event.path != "env" else LogKind.LLMDUP)


@render.register
def _(event: LogLine, run: DeployRun) -> None:
    run.write(event.path, event.line, LogKind.OUTPUT)


@render.register
def _(event: StepFinished, run: DeployRun) -> None:
    if (row := run.row(event.path)) is not None:
        row.state = RowState.DONE if event.ok else RowState.FAILED
        row.seconds = event.seconds
    if not event.ok:
        run.write(event.path, f"{event.path} exited with status {event.returncode}", LogKind.ERROR)


@render.register
def _(event: WaitProgress, run: DeployRun) -> None:
    row = run.row(WAIT_ROW)
    if row is not None and row.state is RowState.NEXT:
        row.state, row.started = RowState.NOW, run.clock()
    run.write(WAIT_ROW, event.message, LogKind.LLMDUP)


@render.register
def _(event: RunFinished, run: DeployRun) -> None:
    _close_wait(run, ok=event.ok)
    run.result = event
    run.write(RESULT, event.message, LogKind.LLMDUP if event.ok else LogKind.ERROR)
    if not event.ok and event.command:
        run.write(RESULT, f"failing command: {first_line(event.command)}", LogKind.ERROR)
    if not event.ok:
        for line in event.tail[-5:]:
            run.write(RESULT, line, LogKind.ERROR)
    run.end()
