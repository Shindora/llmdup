# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""The one object the terminal UI talks to. Every method calls the core function the matching CLI command calls."""

import time
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

from llmdup.core import local
from llmdup.core import status as status_core
from llmdup.core.detect import detect
from llmdup.core.doctor import doctor
from llmdup.core.guides import catalog
from llmdup.core.kube import Kube
from llmdup.core.models import (
    Action,
    Choices,
    ClusterFacts,
    DoctorReport,
    GuideInfo,
    GuideSummary,
    LlmdupError,
    MetricsReport,
    Plan,
    PodLog,
    RunEvent,
    StatusReport,
)
from llmdup.core.planner import LOCAL_GUIDE, Detected, build_plan
from llmdup.core.runner import Prepared, execute, prepare_recorded, prepare_up
from llmdup.core.upstream import DEFAULT_LLMD_REF

TIMEOUTS = {Action.UP: 1800.0, Action.TEST: 600.0, Action.DOWN: 900.0}


class Core:
    """Facade over the core library, pinned to one kube context and one llm-d release."""

    def __init__(self, context: str | None = None, ref: str = DEFAULT_LLMD_REF) -> None:
        self.context = context
        self.ref = ref

    @property
    def kube(self) -> Kube:
        """A command runner pinned to the current context."""
        return Kube(context=self.context)

    def now(self) -> datetime:
        """The wall clock, for ages and log timestamps."""
        return datetime.now(UTC)

    def clock(self) -> float:
        """A monotonic clock, for step timers."""
        return time.monotonic()

    async def detect(self) -> ClusterFacts:
        """Detect the cluster, as `llmdup doctor` does."""
        return await detect(self.kube)

    async def guides(self, facts: ClusterFacts) -> list[GuideSummary]:
        """List the guides with support for the detected accelerator and whether each is deployed."""
        rows = catalog(self.ref).summaries(facts.accelerator)
        deployed: set[str] | None = None
        if facts.reachable:
            try:
                deployed = {s.guide for s in (await self.status()).stacks}
            except LlmdupError:
                deployed = None
        return [r.model_copy(update={"deployed": None if deployed is None else r.name in deployed}) for r in rows]

    def info(self, guide: str) -> GuideInfo:
        """Describe one guide, as `llmdup guides show` does."""
        return catalog(self.ref).info(guide)

    def choices(self, guide: str, facts: ClusterFacts | None) -> Choices:
        """Return the starting choices for a guide: the local profile on the local kind cluster."""
        if guide == LOCAL_GUIDE and facts is not None and facts.context == local.CONTEXT:
            return local.local_choices()
        return Choices(guide=guide)

    def plan(self, choices: Choices, facts: ClusterFacts | None) -> Plan:
        """Build a deploy plan, as `llmdup plan` does."""
        detected = Detected.from_facts(facts) if facts else None
        return build_plan(choices, ref=self.ref, detected=detected)

    async def doctor(self, choices: Choices | None, facts: ClusterFacts | None = None) -> DoctorReport:
        """Run the preflight checks, as `llmdup doctor` does."""
        return await doctor(self.kube, choices, ref=self.ref, facts=facts)

    async def prepare_up(self, choices: Choices) -> Prepared:
        """Gate on the preflight checks and plan a deploy, as `llmdup up` does."""
        return await prepare_up(self.kube, choices, ref=self.ref)

    async def prepare_recorded(self, action: Action, namespace: str) -> Prepared:
        """Plan a test or tear down of a recorded stack, as `llmdup test` and `llmdup down` do."""
        return await prepare_recorded(self.kube, action, namespace)

    def execute(self, prepared: Prepared, *, confirmed: bool) -> AsyncGenerator[RunEvent]:
        """Run a prepared plan as a stream of runner events."""
        return execute(self.kube, prepared, confirmed=confirmed, timeout=TIMEOUTS[prepared.plan.action])

    async def status(self, namespace: str | None = None) -> StatusReport:
        """Report the deployed stacks, as `llmdup status` does."""
        return await status_core.status(self.kube, namespace)

    async def pod_log(self, namespace: str, pod: str, *, timestamps: bool = False) -> PodLog:
        """Read the end of one pod's log."""
        return await status_core.pod_log(self.kube, namespace, pod, timestamps=timestamps)

    async def metrics(self, namespace: str) -> MetricsReport:
        """Read the model-server metrics of a stack."""
        return await status_core.model_metrics(self.kube, namespace)

    async def contexts(self) -> list[str]:
        """List the contexts of the kubeconfig."""
        return await status_core.contexts(self.kube)

    def save(self, path: Path, text: str) -> Path:
        """Write a script or a log the user asked to save, and return where it went."""
        path.write_text(text)
        return path.resolve()
