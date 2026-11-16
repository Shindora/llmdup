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


class HelmRelease(BaseModel):
    """A Helm release found on the cluster."""

    name: str
    namespace: str
    status: str | None = None
    chart: str | None = None


class ClusterFacts(BaseModel):
    """What detection found about the current cluster."""

    context: str | None = None
    reachable: bool = False
    error: str | None = None
    server_version: str | None = None
    nodes: int = 0
    provider: str = "base"
    accelerator: str | None = None
    accelerator_resource: str | None = None
    accelerator_count: int = 0
    allocatable: dict[str, int] = Field(default_factory=dict)
    device_classes: list[str] = Field(default_factory=list)
    gateway_classes: list[str] = Field(default_factory=list)
    crds: list[str] = Field(default_factory=list)
    helm_releases: list[HelmRelease] = Field(default_factory=list)


class CheckStatus(StrEnum):
    """Outcome of one preflight check."""

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


class CheckResult(BaseModel):
    """One preflight check with what was found and how to fix it."""

    name: str
    status: CheckStatus
    found: str
    fix: str | None = None


class DoctorReport(BaseModel):
    """Output of `llmdup doctor`."""

    guide: str | None = None
    facts: ClusterFacts
    checks: list[CheckResult]

    @property
    def ok(self) -> bool:
        """Whether no check failed."""
        return all(c.status is not CheckStatus.FAIL for c in self.checks)


class StepStarted(BaseModel):
    """A step began; `index` counts from 1 and `total` includes the env step."""

    path: str
    index: int
    total: int
    command: str


class LogLine(BaseModel):
    """One line of output from the running step."""

    path: str
    line: str


class StepFinished(BaseModel):
    """A step ended."""

    path: str
    ok: bool
    returncode: int = 0
    seconds: float = 0.0


class WaitProgress(BaseModel):
    """Progress while waiting for pods to become ready."""

    message: str
    ready: int = 0
    total: int = 0


class RunFinished(BaseModel):
    """The run ended; on failure it names the step, its command and its last log lines."""

    action: Action
    guide: str
    namespace: str
    ok: bool
    message: str
    failed_step: str | None = None
    command: str | None = None
    tail: list[str] = Field(default_factory=list)
    steps: list[StepFinished] = Field(default_factory=list)


type RunEvent = StepStarted | LogLine | StepFinished | WaitProgress | RunFinished


class ConfirmationNeededError(LlmdupError):
    """A cluster-scoped step or a delete needs confirmation that was not given."""

    exit_code = ExitCode.CONFIRMATION_NEEDED
    code = "confirmation-needed"


class DoctorFailedError(LlmdupError):
    """A blocking preflight check failed."""

    exit_code = ExitCode.DOCTOR_FAILED
    code = "doctor"


class StackPhase(StrEnum):
    """Where a stack's last deploy or teardown got to."""

    DEPLOYING = "deploying"
    DEPLOYED = "deployed"
    FAILED = "failed"
    TEARING_DOWN = "tearing-down"


class StackState(BaseModel):
    """What llmdup records in the `llmdup-state` ConfigMap of a stack's namespace."""

    guide: str
    namespace: str
    profile: Profile = Profile.DEFAULT
    mode: str | None = None
    options: dict[str, str] = Field(default_factory=dict)
    overrides: dict[str, str] = Field(default_factory=dict)
    llmd_ref: str
    commit: str | None = None
    phase: StackPhase = StackPhase.DEPLOYING
    steps_done: list[str] = Field(default_factory=list)
    failed_step: str | None = None
    updated_at: str | None = None

    def choices(self) -> Choices:
        """Return the choices that rebuild this stack's plan."""
        return Choices(guide=self.guide, mode=self.mode, options=self.options, sets=self.overrides, profile=self.profile)


class PodStatus(BaseModel):
    """One pod of a stack."""

    name: str
    role: str
    phase: str
    ready: bool
    restarts: int = 0


class StackStatus(BaseModel):
    """One deployed stack as `llmdup status` reports it."""

    guide: str
    namespace: str
    profile: Profile
    mode: str | None
    phase: StackPhase
    health: str
    release: HelmRelease | None = None
    pods: list[PodStatus] = Field(default_factory=list)
    endpoint: str | None = None
    port_forward: str | None = None
    llmd_ref: str
    commit: str | None = None
    steps_done: list[str] = Field(default_factory=list)
    failed_step: str | None = None
    updated_at: str | None = None


class StatusReport(BaseModel):
    """Output of `llmdup status`."""

    context: str | None
    stacks: list[StackStatus]


class PodLog(BaseModel):
    """The last lines of one pod's log."""

    namespace: str
    pod: str
    lines: list[str] = Field(default_factory=list)
    error: str | None = None


class PodMetrics(BaseModel):
    """Model-server metric lines of one pod, read through the API server proxy."""

    pod: str
    lines: list[str] = Field(default_factory=list)
    error: str | None = None


class MetricsReport(BaseModel):
    """Model-server metrics of every model-server pod in a namespace."""

    namespace: str
    pods: list[PodMetrics] = Field(default_factory=list)
