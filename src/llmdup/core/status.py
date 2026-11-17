# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0

import asyncio

from llmdup.core import state as state_store
from llmdup.core.detect import current_context, read_helm
from llmdup.core.kube import Kube
from llmdup.core.models import MetricsReport, PodLog, PodMetrics, PodStatus, StackPhase, StackState, StackStatus, StatusReport
from llmdup.core.planner import GATEWAY_NAME

GUIDE_POD_LABEL = "llm-d.ai/guide"
FINISHED_PHASES = frozenset({"Succeeded", "Failed"})
LOG_TAIL = 500
MODEL_PORT = 8000
METRIC_PREFIXES = ("vllm:", "sglang:")


def pod_ready(pod: object) -> bool:
    """Whether a pod's Ready condition is True."""
    match pod:
        case {"status": {"conditions": list(conditions)}}:
            return any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions if isinstance(c, dict))
    return False


def _restarts(pod: object) -> int:
    match pod:
        case {"status": {"containerStatuses": list(containers)}}:
            return sum(int(c.get("restartCount", 0)) for c in containers if isinstance(c, dict))
    return 0


def pods(doc: object, release: str) -> list[PodStatus]:
    """Read the live pods of a stack out of `kubectl get pods -o json`."""
    match doc:
        case {"items": list(items)}:
            pass
        case _:
            return []
    out = []
    for pod in items:
        match pod:
            case {"metadata": {"name": str(name), **meta}, "status": {"phase": str(phase)}} if phase not in FINISHED_PHASES:
                labels = meta.get("labels") or {}
                if GUIDE_POD_LABEL in labels:
                    role = "model server"
                elif name.startswith(f"{release}-") or labels.get("app.kubernetes.io/instance") == release:
                    role = "router"
                else:
                    role = "other"
                out.append(PodStatus(name=name, role=role, phase=phase, ready=pod_ready(pod), restarts=_restarts(pod)))
    return out


def gateway_programmed(doc: object) -> bool | None:
    """Whether a Gateway is programmed and has an address; None when there is no Gateway."""
    match doc:
        case {"status": {"conditions": list(conditions), "addresses": [_, *_]}}:
            return any(c.get("type") == "Programmed" and c.get("status") == "True" for c in conditions if isinstance(c, dict))
        case {"metadata": dict()}:
            return False
    return None


def deployments_ready(doc: object) -> tuple[int, int]:
    """Return how many Deployments are fully available, and how many there are."""
    match doc:
        case {"items": list(items)}:
            pass
        case _:
            return 0, 0
    ready = 0
    for item in items:
        match item:
            case {"spec": {"replicas": int(want)}, "status": {**st}} if int(st.get("readyReplicas") or 0) >= want:
                ready += 1
    return ready, len(items)


def endpoint(state: StackState, service: object, gateway: object) -> tuple[str | None, str | None]:
    """Return the in-cluster URL of a stack and a port-forward command to reach it."""
    release = state.overrides.get("GUIDE_NAME", state.guide)
    if state.mode == "gateway":
        match gateway:
            case {"status": {"addresses": [{"value": str(address)}, *_]}}:
                return f"http://{address}", None
        return None, None
    match service:
        case {"spec": {"clusterIP": str(_)}}:
            url = f"http://{release}-epp.{state.namespace}.svc.cluster.local:80"
            return url, f"kubectl port-forward -n {state.namespace} svc/{release}-epp 8000:80"
    return None, None


def health(state: StackState, live: list[PodStatus], release_status: str | None) -> str:
    """Summarise a stack: ready, not ready, partial or failed."""
    if state.phase in {StackPhase.DEPLOYING, StackPhase.TEARING_DOWN}:
        return "partial"
    if state.phase is StackPhase.FAILED:
        return "failed"
    model = [p for p in live if p.role == "model server"]
    if release_status == "deployed" and model and all(p.ready for p in live):
        return "ready"
    return "not ready"


async def stack_status(kube: Kube, state: StackState) -> StackStatus:
    """Collect release, pods and endpoint of one recorded stack."""
    release = state.overrides.get("GUIDE_NAME", state.guide)
    ns = state.namespace
    async with asyncio.TaskGroup() as tg:
        pod_doc = tg.create_task(kube.json("kubectl", "get", "pods", "-n", ns, "-o", "json", timeout=30))
        helm_doc = tg.create_task(kube.json("helm", "list", "-n", ns, "-o", "json", timeout=30))
        svc_doc = tg.create_task(kube.json("kubectl", "get", "service", f"{release}-epp", "-n", ns, "-o", "json", timeout=30))
        gw_doc = tg.create_task(kube.json("kubectl", "get", "gateway", GATEWAY_NAME, "-n", ns, "-o", "json", timeout=30))
    found = next((r for r in read_helm(helm_doc.result()) if r.name == release), None)
    live = pods(pod_doc.result(), release)
    url, forward = endpoint(state, svc_doc.result(), gw_doc.result())
    return StackStatus(
        guide=state.guide,
        namespace=ns,
        profile=state.profile,
        mode=state.mode,
        phase=state.phase,
        health=health(state, live, found.status if found else None),
        release=found,
        pods=live,
        endpoint=url,
        port_forward=forward,
        llmd_ref=state.llmd_ref,
        commit=state.commit,
        steps_done=state.steps_done,
        failed_step=state.failed_step,
        updated_at=state.updated_at,
    )


async def status(kube: Kube, namespace: str | None = None) -> StatusReport:
    """Report every stack llmdup deployed, in one namespace or all of them."""
    states = await state_store.load(kube, namespace)
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(stack_status(kube, s)) for s in states]
    return StatusReport(context=await current_context(kube), stacks=[t.result() for t in tasks])


def _redact(lines: list[str]) -> list[str]:
    from llmdup.core.runner import redact  # runner imports this module, so import late

    return [redact(line) for line in lines]


async def pod_log(kube: Kube, namespace: str, pod: str, *, tail: int = LOG_TAIL, timestamps: bool = False) -> PodLog:
    """Read the last lines of every container's log in one pod, with secret values redacted."""
    args = ["logs", pod, "-n", namespace, "--all-containers", f"--tail={tail}", *(["--timestamps"] if timestamps else [])]
    result = await kube.run("kubectl", *args, timeout=30)
    if not result.ok:
        return PodLog(namespace=namespace, pod=pod, error=result.stderr.strip() or f"kubectl logs exited {result.returncode}")
    return PodLog(namespace=namespace, pod=pod, lines=_redact(result.stdout.splitlines()))


def metric_lines(text: str) -> list[str]:
    """Keep the vLLM and SGLang sample lines of a Prometheus text exposition."""
    return [line for line in text.splitlines() if line.startswith(METRIC_PREFIXES)]


async def _pod_metrics(kube: Kube, namespace: str, pod: str) -> PodMetrics:
    path = f"/api/v1/namespaces/{namespace}/pods/{pod}:{MODEL_PORT}/proxy/metrics"
    result = await kube.run("kubectl", "get", "--raw", path, timeout=30)
    if not result.ok:
        return PodMetrics(pod=pod, error=result.stderr.strip() or f"kubectl get --raw exited {result.returncode}")
    return PodMetrics(pod=pod, lines=metric_lines(result.stdout))


async def model_metrics(kube: Kube, namespace: str) -> MetricsReport:
    """Read the model-server metrics of every model-server pod, the way the guides' `pod_metrics` test does."""
    doc = await kube.json("kubectl", "get", "pods", "-n", namespace, "-l", GUIDE_POD_LABEL, "-o", "json", timeout=30)
    names = [p.name for p in pods(doc, "") if p.role == "model server"]
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(_pod_metrics(kube, namespace, name)) for name in names]
    return MetricsReport(namespace=namespace, pods=[t.result() for t in tasks])


async def contexts(kube: Kube) -> list[str]:
    """Return the context names in the kubeconfig."""
    result = await kube.run("kubectl", "config", "get-contexts", "-o", "name", timeout=10)
    return result.stdout.split() if result.ok else []
