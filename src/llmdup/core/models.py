# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
from enum import IntEnum, StrEnum
from typing import Any

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


class ExitCode(IntEnum):
    """Process exit codes shared by every front end."""

    OK = 0
    STEP_FAILED = 1
    USAGE = 2
    DOCTOR_FAILED = 3
    CONFIRMATION_NEEDED = 4


class ErrorInfo(BaseModel):
    """One error in the JSON envelope."""

    code: str
    message: str
    hint: str | None = None


class Envelope(BaseModel):
    """The single JSON document printed by every `--json` command."""

    schema_version: int = SCHEMA_VERSION
    ok: bool
    command: str
    data: Any = None
    errors: list[ErrorInfo] = Field(default_factory=list)


class LlmdupError(Exception):
    """An error a front end reports to the user with an exit code."""

    exit_code: ExitCode = ExitCode.STEP_FAILED
    code: str = "error"

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint

    def info(self) -> ErrorInfo:
        """Return the envelope form of this error."""
        return ErrorInfo(code=self.code, message=str(self), hint=self.hint)


class UsageError(LlmdupError):
    """Bad usage, or a value the guide does not allow."""

    exit_code = ExitCode.USAGE
    code = "usage"


class VersionInfo(BaseModel):
    """Output of `llmdup version`."""

    llmdup: str
    llmd_ref: str


class StepTag(StrEnum):
    """How llmdup treats a guide step path."""

    SKIPPED = "skipped"
    CLUSTER_SCOPED = "cluster-scoped"
    SENDS_TRAFFIC = "sends traffic"
    MODE = "mode"
    MANUAL_OPTION = "manual option"
    LOCAL = "local profile"


class Profile(StrEnum):
    """Which set of steps a plan uses."""

    DEFAULT = "default"
    LOCAL = "local"


class Action(StrEnum):
    """What a plan does to the cluster."""

    UP = "up"
    TEST = "test"
    DOWN = "down"


class VariableSource(StrEnum):
    """Where a plan variable's value came from."""

    FLAG = "flag"
    DETECTED = "detected"
    SUPPORT = "support matrix"
    PROFILE = "local profile"
    DEFAULT = "default"
    ENVIRONMENT = "environment"


class Variable(BaseModel):
    """One `env.static` variable of a guide."""

    name: str
    default: str | None = None
    values: list[str] | None = None
    sensitive: bool = False
    comment: str | None = None


class AcceleratorSupport(BaseModel):
    """One accelerator row of a guide's support matrix."""

    name: str
    label: str | None = None
    model: str | None = None
    notes: str | None = None
    engines: dict[str, str] = Field(default_factory=dict)
    providers: list[str] = Field(default_factory=list)


class Support(BaseModel):
    """A guide's accelerator by engine support matrix."""

    engines: dict[str, str] = Field(default_factory=dict)
    accelerators: list[AcceleratorSupport] = Field(default_factory=list)

    def status(self, accelerator: str, engine: str) -> str:
        """Return `validated`, `community` or `unsupported` for one pair."""
        row = next((a for a in self.accelerators if a.name == accelerator), None)
        return row.engines.get(engine, "unsupported") if row else "unsupported"

    def row(self, accelerator: str) -> AcceleratorSupport | None:
        """Return the matrix row of one accelerator."""
        return next((a for a in self.accelerators if a.name == accelerator), None)


class StepPath(BaseModel):
    """A step-list path of a guide with its static tags."""

    path: str
    tags: list[StepTag] = Field(default_factory=list)


class GuideSummary(BaseModel):
    """One row of `llmdup guides list`."""

    name: str
    title: str
    section: str
    modes: list[str]
    accelerator: str | None = None
    engine: str | None = None
    support: str | None = None
    deployed: bool | None = None


class GuideInfo(BaseModel):
    """Everything `llmdup guides show` knows about a guide."""

    name: str
    title: str
    section: str
    dir: str
    modes: list[str]
    variables: list[Variable]
    support: Support | None = None
    steps: list[StepPath]


class Choices(BaseModel):
    """What the user picked for a plan; unset fields come from detection, then defaults."""

    guide: str
    accelerator: str | None = None
    engine: str | None = None
    provider: str | None = None
    mode: str | None = None
    namespace: str | None = None
    model: str | None = None
    sets: dict[str, str] = Field(default_factory=dict)
    options: dict[str, str] = Field(default_factory=dict)
    profile: Profile = Profile.DEFAULT


class PlanVariable(BaseModel):
    """A resolved variable shown in a plan."""

    name: str
    value: str | None
    source: VariableSource
    values: list[str] | None = None
    sensitive: bool = False


class Step(BaseModel):
    """One step of a plan with the script that runs it."""

    path: str
    tags: list[StepTag] = Field(default_factory=list)
    command: str
    script: str


class SkippedStep(BaseModel):
    """A guide step a plan leaves out, and why."""

    path: str
    reason: str


class Plan(BaseModel):
    """An ordered list of steps for one guide, action and set of choices."""

    guide: str
    action: Action
    profile: Profile
    llmd_ref: str
    commit: str | None = None
    mode: str | None = None
    namespace: str
    options: dict[str, str] = Field(default_factory=dict)
    overrides: dict[str, str] = Field(default_factory=dict)
    variables: list[PlanVariable]
    env_script: str
    steps: list[Step]
    skipped: list[SkippedStep] = Field(default_factory=list)
    needs_confirmation: list[str] = Field(default_factory=list)

    @property
    def script(self) -> str:
        """The whole plan as one bash script."""
        parts = [self.env_script.rstrip()]
        parts.extend(f"\n# === {step.path} ===\n{step.command}" for step in self.steps)
        return "\n".join(parts) + "\n"
