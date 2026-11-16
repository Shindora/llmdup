# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
"""A Core for UI tests: guides and plans from the fixture release, canned cluster answers, scripted runs."""

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path

import yaml

from llmdup.core.models import (
    Action,
    CheckResult,
    CheckStatus,
    Choices,
    ClusterFacts,
    DoctorFailedError,
    DoctorReport,
    GuideSummary,
    HelmRelease,
    LogLine,
    MetricsReport,
    PodLog,
    PodMetrics,
    PodStatus,
    Profile,
    RunEvent,
    RunFinished,
    StackPhase,
    StackStatus,
    StatusReport,
    StepFinished,
    StepStarted,
    WaitProgress,
)
from llmdup.core.planner import build_plan
from llmdup.core.runner import Prepared, confirm_commands, require_confirmation
from llmdup.tui.core import Core

FIXTURE = Path(__file__).parents[1] / "fixtures" / "llm-d-v1.0.0"
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
CONTEXT = "fake-gpu"
NAMESPACE = "llm-d-optimized-baseline"
INDEXED_SECTIONS = ("foundations", "models")
SUPPORT = {"p2p-kv-cache-sharing": "community", "omni-serving": "unsupported"}
OUTPUT = {
    "prerequisites.gaie": ["customresourcedefinition.apiextensions.k8s.io/inferencepools.inference.networking.k8s.io created"],
    "prerequisites.namespace": [f"namespace/{NAMESPACE} created"],
    "prerequisites.secrets": ["secret/llm-d-hf-token created"],
    "deploy.standalone": ["NAME: optimized-baseline", f"NAMESPACE: {NAMESPACE}", "STATUS: deployed"],
    "deploy.modelserver.standard": ["deployment.apps/optimized-baseline-nvidia-gpu-vllm-decode created"],
    "verify.tests.request": ['{"choices":[{"text":"I am fine, thank you."}]}', "request 1: HTTP 200"],
}


def index_guides() -> list[tuple[str, str, str]]:
    """Section, name and title of the fixture index's foundations and models guides."""
    data = yaml.safe_load((FIXTURE / "docs" / "well-lit-paths" / "guides.yaml").read_text())
    return [
        (section, Path(entry["dir"]).name, entry["title"]) for section in INDEXED_SECTIONS for entry in data["sections"][section]["guides"]
    ]


def pod(name: str, role: str) -> PodStatus:
    return PodStatus(name=name, role=role, phase="Running", ready=True)


def stack() -> StackStatus:
    return StackStatus(
        guide="optimized-baseline",
        namespace=NAMESPACE,
        profile=Profile.DEFAULT,
        mode="standalone",
        phase=StackPhase.DEPLOYED,
        health="ready",
        release=HelmRelease(name="optimized-baseline", namespace=NAMESPACE, status="deployed", chart="llm-d-router-standalone-v1.0.0"),
        pods=[
            pod("optimized-baseline-epp-6c9f7d5b8-x2kqp", "router"),
            pod("optimized-baseline-nvidia-gpu-vllm-decode-7d4b9c6f5-8hjzn", "model server"),
            pod("optimized-baseline-nvidia-gpu-vllm-decode-7d4b9c6f5-tm4wd", "model server"),
        ],
        endpoint=f"http://optimized-baseline-epp.{NAMESPACE}.svc.cluster.local:80",
        port_forward=f"kubectl port-forward -n {NAMESPACE} svc/optimized-baseline-epp 8000:80",
        llmd_ref="v1.0.0",
        updated_at="2026-11-16T11:54:00+00:00",
    )


def check(name: str, status: CheckStatus, found: str, fix: str | None = None) -> CheckResult:
    return CheckResult(name=name, status=status, found=found, fix=fix)


def doctor_report(guide: str | None, *, blocking: bool) -> DoctorReport:
    checks = [
        check("Tools", CheckStatus.OK, "kubectl and helm found"),
        check("Cluster reachable", CheckStatus.OK, f"context {CONTEXT} answers"),
        check("Kubernetes version", CheckStatus.OK, "v1.34.2"),
        check("Accelerators", CheckStatus.OK, "gpu: 8 x nvidia.com/gpu"),
        check(
            "Inference Extension CRDs",
            CheckStatus.WARN,
            "inferencepools.inference.networking.k8s.io not installed",
            "the plan installs them in its cluster-scoped prerequisites step, after confirmation",
        ),
    ]
    if guide is not None:
        checks += [
            check("Guide support", CheckStatus.OK, "gpu + vllm: validated"),
            check("Capacity", CheckStatus.OK, "4 nvidia.com/gpu of 8"),
            check("HF_TOKEN", CheckStatus.FAIL, "not set", "export HF_TOKEN=<your Hugging Face token>")
            if blocking
            else check("HF_TOKEN", CheckStatus.OK, "set"),
            check("Existing release", CheckStatus.OK, f"no release {guide} in {NAMESPACE}"),
            check("Gateway provider", CheckStatus.SKIP, "mode standalone"),
        ]
    return DoctorReport(guide=guide, facts=FACTS, checks=checks)


FACTS = ClusterFacts(
    context=CONTEXT,
    reachable=True,
    server_version="v1.34.2",
    nodes=3,
    accelerator="gpu",
    accelerator_resource="nvidia.com/gpu",
    accelerator_count=8,
    allocatable={"nvidia.com/gpu": 8},
)


class FakeCore(Core):
    """Answers like a GPU cluster with one stack deployed; runs are scripted and instant unless `gate` holds them."""

    def __init__(self, *, block_deploy: bool = False) -> None:
        super().__init__(context=CONTEXT)
        self.stacks = [stack()]
        self.block_deploy = block_deploy
        self.gate: asyncio.Event | None = None
        self.saved: dict[Path, str] = {}
        self.executed: list[Action] = []

    def now(self) -> datetime:
        return NOW

    def clock(self) -> float:
        return 100.0

    async def detect(self) -> ClusterFacts:
        return FACTS

    async def guides(self, facts: ClusterFacts) -> list[GuideSummary]:
        real = await super().guides(facts)
        known = {r.name for r in real}
        deployed = {s.guide for s in self.stacks}
        extra = [
            GuideSummary(
                name=name,
                title=title,
                section=section,
                modes=["standalone"],
                accelerator="gpu",
                engine="vllm",
                support=SUPPORT.get(name, "validated"),
                deployed=name in deployed,
            )
            for section, name, title in index_guides()
            if name not in known
        ]
        order = {name: i for i, (_, name, _) in enumerate(index_guides())}
        return sorted([*real, *extra], key=lambda r: order.get(r.name, len(order)))

    async def doctor(self, choices: Choices | None, facts: ClusterFacts | None = None) -> DoctorReport:
        del facts
        return doctor_report(choices.guide if choices else None, blocking=True)

    async def prepare_up(self, choices: Choices) -> Prepared:
        if self.block_deploy:
            msg = "HF_TOKEN: not set"
            raise DoctorFailedError(msg)
        plan = self.plan(choices, FACTS)
        return Prepared(plan=plan, doctor=doctor_report(choices.guide, blocking=False), confirm=confirm_commands(plan))

    async def prepare_recorded(self, action: Action, namespace: str) -> Prepared:
        del namespace
        plan = build_plan(Choices(guide="optimized-baseline"), action=action, ref=self.ref)
        return Prepared(plan=plan, confirm=confirm_commands(plan))

    def execute(self, prepared: Prepared, *, confirmed: bool) -> AsyncGenerator[RunEvent]:
        require_confirmation(prepared, confirmed=confirmed)
        return self._events(prepared)

    async def _events(self, prepared: Prepared) -> AsyncGenerator[RunEvent]:
        plan = prepared.plan
        self.executed.append(plan.action)
        steps = [("env", "export the guide's variables"), *((s.path, s.command) for s in plan.steps)]
        results: list[StepFinished] = []
        waited = plan.action is not Action.UP
        for index, (path, command) in enumerate(steps, 1):
            if not waited and path.startswith("verify."):
                waited = True
                yield WaitProgress(message="1/3 pods ready, 2 model server pods, 1/2 deployments available", ready=1, total=3)
                if self.gate is not None:
                    await self.gate.wait()
                yield WaitProgress(message="3/3 pods ready, 2 model server pods, 2/2 deployments available", ready=3, total=3)
            yield StepStarted(path=path, index=index, total=len(steps), command=command)
            for line in OUTPUT.get(path, []):
                yield LogLine(path=path, line=line)
            result = StepFinished(path=path, ok=True, seconds=float(index))
            results.append(result)
            yield result
        self._apply(plan.action)
        message = "nothing left deployed" if plan.action is Action.DOWN else f"{plan.action} {plan.guide}: all steps ok"
        yield RunFinished(action=plan.action, guide=plan.guide, namespace=plan.namespace, ok=True, message=message, steps=results)

    def _apply(self, action: Action) -> None:
        if action is Action.DOWN:
            self.stacks = []
        elif action is Action.UP and not self.stacks:
            self.stacks = [stack()]

    async def status(self, namespace: str | None = None) -> StatusReport:
        return StatusReport(context=CONTEXT, stacks=[s for s in self.stacks if namespace in {None, s.namespace}])

    async def pod_log(self, namespace: str, pod: str, *, timestamps: bool = False) -> PodLog:
        stamp = "2026-11-16T11:58:0{}Z " if timestamps else ""
        lines = [
            "INFO llm-d-inference-sim starting on port 8000",
            "INFO model llmdup-sim loaded",
            'INFO POST /v1/completions 200 "How are you today?"',
            "WARN queue depth 3 above target 2",
            "INFO POST /v1/completions 200",
        ]
        return PodLog(namespace=namespace, pod=pod, lines=[stamp.format(i) + line for i, line in enumerate(lines)])

    async def metrics(self, namespace: str) -> MetricsReport:
        lines = [
            'vllm:request_success_total{model_name="Qwen/Qwen3-32B"} 42',
            'vllm:prefix_cache_hits_total{model_name="Qwen/Qwen3-32B"} 17',
        ]
        pods = [PodMetrics(pod=p.name, lines=lines) for s in self.stacks for p in s.pods if p.role == "model server"]
        return MetricsReport(namespace=namespace, pods=pods)

    async def contexts(self) -> list[str]:
        return [CONTEXT, "kind-llmdup"]

    def save(self, path: Path, text: str) -> Path:
        self.saved[path] = text
        return path
