# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0

import json
from datetime import UTC, datetime

from llmdup.core.kube import Kube
from llmdup.core.models import Plan, StackPhase, StackState

CONFIGMAP = "llmdup-state"
MANAGED_BY = "app.kubernetes.io/managed-by=llmdup"
GUIDE_LABEL = "llmdup.io/guide"


def from_plan(plan: Plan) -> StackState:
    """Return the state record for a plan that is about to deploy."""
    return StackState(
        guide=plan.guide,
        namespace=plan.namespace,
        profile=plan.profile,
        mode=plan.mode,
        options=plan.options,
        overrides=plan.overrides,
        llmd_ref=plan.llmd_ref,
        commit=plan.commit,
    )


def configmap(state: StackState) -> dict[str, object]:
    """Return the ConfigMap that stores `state`."""
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": CONFIGMAP,
            "namespace": state.namespace,
            "labels": {"app.kubernetes.io/managed-by": "llmdup", GUIDE_LABEL: state.guide},
        },
        "data": {"state": state.model_dump_json()},
    }


async def save(kube: Kube, state: StackState) -> bool:
    """Write the state ConfigMap; False when the namespace does not exist yet."""
    state.updated_at = datetime.now(UTC).isoformat(timespec="seconds")
    result = await kube.run("kubectl", "apply", "-f", "-", stdin=json.dumps(configmap(state)), timeout=30)
    return result.ok


def parse(doc: object) -> list[StackState]:
    """Read the stack states out of `kubectl get configmap -o json`."""
    match doc:
        case {"items": list(items)}:
            pass
        case _:
            return []
    out = []
    for item in items:
        match item:
            case {"data": {"state": str(raw)}}:
                out.append(StackState.model_validate_json(raw))
    return out


async def load(kube: Kube, namespace: str | None = None) -> list[StackState]:
    """Return every stack llmdup recorded, in one namespace or in all of them."""
    scope = ["-n", namespace] if namespace else ["-A"]
    return parse(await kube.json("kubectl", "get", "configmap", *scope, "-l", MANAGED_BY, "-o", "json", timeout=30))


async def delete(kube: Kube, namespace: str) -> None:
    """Remove the state ConfigMap of a namespace, if any."""
    await kube.run("kubectl", "delete", "configmap", CONFIGMAP, "-n", namespace, "--ignore-not-found", timeout=30)


def mark(state: StackState, phase: StackPhase, *, failed_step: str | None = None) -> StackState:
    """Set the phase and failing step of a state record."""
    state.phase = phase
    state.failed_step = failed_step
    return state
