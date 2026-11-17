# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0

import os
from pathlib import Path

import yaml

from llmdup.core import state as state_store
from llmdup.core.detect import ALLOCATABLE_ACCELERATORS, INFERENCE_POOL_CRD, detect
from llmdup.core.guides import catalog
from llmdup.core.kube import Kube
from llmdup.core.models import (
    CheckResult,
    CheckStatus,
    Choices,
    ClusterFacts,
    DoctorReport,
    GuideInfo,
    LlmdupError,
    Plan,
    Profile,
)
from llmdup.core.planner import GATEWAY_NAME, GKE_GATEWAY_CLASS, Detected, build_plan
from llmdup.core.upstream import DEFAULT_LLMD_REF

MIN_KUBERNETES = (1, 29)
DRA_KUBERNETES = (1, 34)


def _check(name: str, status: CheckStatus, found: str, fix: str | None = None) -> CheckResult:
    return CheckResult(name=name, status=status, found=found, fix=fix)


def check_tools(kube: Kube) -> CheckResult:
    """Fail when kubectl or helm is missing."""
    missing = [tool for tool in ("kubectl", "helm") if kube.which(tool) is None]
    if missing:
        return _check("Tools", CheckStatus.FAIL, f"missing: {', '.join(missing)}", "install kubectl and helm and put them on PATH")
    return _check("Tools", CheckStatus.OK, "kubectl and helm found")


def check_reachable(facts: ClusterFacts) -> CheckResult:
    """Fail when the current context does not answer."""
    if facts.reachable:
        return _check("Cluster reachable", CheckStatus.OK, f"context {facts.context} answers")
    found = f"context {facts.context or '(none)'}: {facts.error or 'no answer'}"
    return _check("Cluster reachable", CheckStatus.FAIL, found, "check `kubectl cluster-info`, or pass --context")


def _version(text: str | None) -> tuple[int, int] | None:
    parts = (text or "").lstrip("v").split(".")
    try:
        return int(parts[0]), int("".join(ch for ch in parts[1] if ch.isdigit()))
    except IndexError, ValueError:
        return None


def check_version(facts: ClusterFacts) -> CheckResult:
    """Fail when Kubernetes is older than 1.29."""
    found = _version(facts.server_version)
    if found is None:
        return _check("Kubernetes version", CheckStatus.SKIP, "unknown")
    if found < MIN_KUBERNETES:
        return _check("Kubernetes version", CheckStatus.FAIL, str(facts.server_version), "llm-d needs Kubernetes 1.29 or newer")
    note = "" if found >= DRA_KUBERNETES else " (overlays that use DRA need 1.34)"
    return _check("Kubernetes version", CheckStatus.OK, f"{facts.server_version}{note}")


def check_accelerators(facts: ClusterFacts, plan: Plan | None) -> CheckResult:
    """Report the detected accelerator; warn when there is none and the plan needs one."""
    if facts.accelerator:
        count = f"{facts.accelerator_count} x {facts.accelerator_resource}" if facts.accelerator_resource else "via DRA"
        return _check("Accelerators", CheckStatus.OK, f"{facts.accelerator}: {count}")
    if plan is not None and plan.profile is Profile.LOCAL:
        return _check("Accelerators", CheckStatus.OK, "none; the local profile uses the simulator")
    fix = "use a cluster with accelerator nodes, or try `llmdup local up`"
    return _check("Accelerators", CheckStatus.WARN, "no accelerator detected", fix)


def check_crds(facts: ClusterFacts) -> CheckResult:
    """Warn when the Gateway API Inference Extension CRDs are missing or cannot be listed."""
    if "crds" in facts.unknown:
        fix = "check your RBAC; the plan installs them if missing"
        return _check("Inference Extension CRDs", CheckStatus.WARN, "could not list CRDs", fix)
    if INFERENCE_POOL_CRD in facts.crds:
        return _check("Inference Extension CRDs", CheckStatus.OK, f"{INFERENCE_POOL_CRD} installed")
    return _check(
        "Inference Extension CRDs",
        CheckStatus.WARN,
        f"{INFERENCE_POOL_CRD} not installed",
        "the plan installs them in its cluster-scoped prerequisites step, after confirmation",
    )


def _value(plan: Plan, name: str) -> str | None:
    return next((v.value for v in plan.variables if v.name == name), None)


def check_support(info: GuideInfo, plan: Plan) -> CheckResult:
    """Fail on an unsupported accelerator and engine pair; warn on a community one."""
    accel, engine = _value(plan, "ACCELERATOR_TYPE"), _value(plan, "MODEL_SERVER")
    if info.support is None or not accel or not engine:
        return _check("Guide support", CheckStatus.SKIP, f"{info.name} has no support matrix")
    status = info.support.status(accel, engine)
    pair = f"{accel} + {engine}: {status}"
    supported = [f"{a.name}+{e}" for a in info.support.accelerators for e, s in a.engines.items() if s != "unsupported"]
    match status:
        case "validated":
            return _check("Guide support", CheckStatus.OK, pair)
        case "community":
            return _check("Guide support", CheckStatus.WARN, pair, "community pairs are not covered by upstream nightly tests")
    return _check("Guide support", CheckStatus.FAIL, pair, f"pick a supported pair: {', '.join(supported)}")


def overlay_dir(release_path: Path, info: GuideInfo, plan: Plan) -> Path | None:
    """Return the model-server overlay directory the plan applies, by the upstream layout convention."""
    accel, engine, provider = (_value(plan, n) for n in ("ACCELERATOR_TYPE", "MODEL_SERVER", "INFRA_PROVIDER"))
    if not accel or not engine:
        return None
    base = release_path / info.dir / "modelserver" / accel / engine
    for candidate in (base / provider if provider else base, base):
        if (candidate / "kustomization.yaml").is_file():
            return candidate
    return None


def accelerator_requests(rendered: str) -> dict[str, int]:
    """Sum accelerator requests over the workloads of a rendered overlay.

    Each pod template's request counts `replicas` times, and `size` times more for LeaderWorkerSets.

    Args:
        rendered: Output of `kubectl kustomize`.

    Returns:
        Devices wanted per extended resource name.
    """
    # ponytail: replicas x size x every container found in the object; exact per-role counts if a guide needs them
    totals: dict[str, int] = {}
    for doc in yaml.safe_load_all(rendered):
        if not isinstance(doc, dict):
            continue
        spec = doc.get("spec") if isinstance(doc.get("spec"), dict) else {}
        replicas = int(spec.get("replicas") or 1)
        lws = spec.get("leaderWorkerTemplate")
        size = int(lws.get("size") or 1) if isinstance(lws, dict) else 1
        for resources in _container_resources(doc):
            for key in ALLOCATABLE_ACCELERATORS:
                per_pod = max(_int(resources.get("requests"), key), _int(resources.get("limits"), key))
                if per_pod:
                    totals[key] = totals.get(key, 0) + per_pod * replicas * size
    return totals


def _int(section: object, key: str) -> int:
    if not isinstance(section, dict) or key not in section:
        return 0
    try:
        return int(str(section[key]))
    except ValueError:
        return 0


def _container_resources(node: object) -> list[dict[str, object]]:
    if isinstance(node, list):
        return [r for item in node for r in _container_resources(item)]
    if not isinstance(node, dict):
        return []
    found = [c["resources"] for c in node.get("containers") or [] if isinstance(c, dict) and isinstance(c.get("resources"), dict)]
    return found + [r for key, value in node.items() if key != "containers" for r in _container_resources(value)]


async def check_capacity(kube: Kube, facts: ClusterFacts, info: GuideInfo, plan: Plan, release_path: Path) -> CheckResult:
    """Warn when the rendered overlay asks for more accelerators than the nodes allocate."""
    if plan.profile is Profile.LOCAL:
        return _check("Capacity", CheckStatus.OK, "the simulator needs no accelerators")
    directory = overlay_dir(release_path, info, plan)
    if directory is None:
        return _check("Capacity", CheckStatus.SKIP, "no model-server overlay found for these choices")
    rendered = await kube.run("kubectl", "kustomize", str(directory), timeout=60)
    if not rendered.ok:
        return _check("Capacity", CheckStatus.WARN, f"kubectl kustomize failed: {rendered.stderr.strip()[:200]}")
    wanted = accelerator_requests(rendered.stdout)
    if not wanted:
        return _check("Capacity", CheckStatus.OK, "the overlay requests no accelerator devices (DRA or CPU)")
    short = [f"{n} {r} wanted, {facts.allocatable.get(r, 0)} allocatable" for r, n in wanted.items() if n > facts.allocatable.get(r, 0)]
    if short:
        return _check("Capacity", CheckStatus.WARN, "; ".join(short), "add accelerator nodes, or lower replicas in a copy of the overlay")
    return _check("Capacity", CheckStatus.OK, ", ".join(f"{n} {r} of {facts.allocatable.get(r, 0)}" for r, n in wanted.items()))


def check_hf_token(plan: Plan) -> CheckResult:
    """Fail when the plan creates the Hugging Face secret and HF_TOKEN is not set."""
    if not any(s.path == "prerequisites.secrets" for s in plan.steps):
        return _check("HF_TOKEN", CheckStatus.SKIP, "the plan creates no secret")
    if os.environ.get("HF_TOKEN"):
        return _check("HF_TOKEN", CheckStatus.OK, "set")
    return _check("HF_TOKEN", CheckStatus.FAIL, "not set", "export HF_TOKEN=<your Hugging Face token>")


def check_existing_release(facts: ClusterFacts, plan: Plan) -> CheckResult:
    """Fail when a Helm release named after the guide exists in the namespace."""
    name = _value(plan, "GUIDE_NAME") or plan.guide
    if "helm" in facts.unknown:
        return _check("Existing release", CheckStatus.WARN, "could not list Helm releases", "check that helm can reach the cluster")
    if any(r.name == name and r.namespace == plan.namespace for r in facts.helm_releases):
        return _check(
            "Existing release",
            CheckStatus.FAIL,
            f"release {name} already in namespace {plan.namespace}",
            f"tear it down with `llmdup down --namespace {plan.namespace}`, or pass --namespace",
        )
    return _check("Existing release", CheckStatus.OK, f"no release {name} in {plan.namespace}")


async def check_existing_stack(kube: Kube, plan: Plan) -> CheckResult:
    """Fail when llmdup already recorded a stack in the namespace; guides do not stack."""
    try:
        found = await state_store.load(kube, plan.namespace)
    except LlmdupError as error:
        return _check("Existing stack", CheckStatus.WARN, str(error))
    if found:
        stack = found[0]
        return _check(
            "Existing stack",
            CheckStatus.FAIL,
            f"namespace {plan.namespace} already holds {stack.guide} ({stack.phase})",
            f"tear it down with `llmdup down --namespace {plan.namespace}`, or pass --namespace",
        )
    return _check("Existing stack", CheckStatus.OK, f"no llmdup stack in {plan.namespace}")


def check_gateway(facts: ClusterFacts, plan: Plan) -> CheckResult:
    """Warn when gateway mode is chosen and no GatewayClass is installed."""
    if plan.mode != "gateway":
        return _check("Gateway provider", CheckStatus.SKIP, f"mode {plan.mode or '-'}")
    if gateway_class := facts.gateways.get(f"{plan.namespace}/{GATEWAY_NAME}"):
        extra = "; llmdup sets provider.name=gke for it" if gateway_class.startswith(GKE_GATEWAY_CLASS) else ""
        return _check("Gateway provider", CheckStatus.OK, f"{GATEWAY_NAME} uses {gateway_class}{extra}")
    if facts.gateway_classes:
        found = f"no Gateway {GATEWAY_NAME} in {plan.namespace}; classes: {', '.join(facts.gateway_classes)}"
        fix = f"create the Gateway in {plan.namespace} before deploying; see docs/infrastructure/gateway in llm-d"
        return _check("Gateway provider", CheckStatus.WARN, found, fix)
    fix = "install a Gateway provider; see guides/recipes/gateway in llm-d"
    return _check("Gateway provider", CheckStatus.WARN, "no GatewayClass installed", fix)


async def doctor(
    kube: Kube,
    choices: Choices | None = None,
    *,
    ref: str = DEFAULT_LLMD_REF,
    facts: ClusterFacts | None = None,
) -> DoctorReport:
    """Run the preflight checks, for the cluster alone or for a guide.

    Args:
        kube: Command runner for the target context.
        choices: The guide and choices to check, if any.
        ref: llm-d release tag.
        facts: Detection results to reuse instead of detecting again.

    Returns:
        The report; `report.ok` is False when any check failed.
    """
    tools = check_tools(kube)
    facts = facts or (await detect(kube) if tools.status is CheckStatus.OK else ClusterFacts())
    checks = [tools, check_reachable(facts)]
    if not facts.reachable:
        return DoctorReport(guide=choices.guide if choices else None, facts=facts, checks=checks)
    plan = info = None
    if choices is not None:
        plan = build_plan(choices, ref=ref, detected=Detected.from_facts(facts))
        info = catalog(ref).info(choices.guide)
    checks += [check_version(facts), check_accelerators(facts, plan), check_crds(facts)]
    if plan is not None and info is not None:
        checks += [
            check_support(info, plan),
            await check_capacity(kube, facts, info, plan, catalog(ref).release.path),
            check_hf_token(plan),
            check_existing_release(facts, plan),
            await check_existing_stack(kube, plan),
            check_gateway(facts, plan),
        ]
    return DoctorReport(guide=choices.guide if choices else None, facts=facts, checks=checks)
