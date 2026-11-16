# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0

import json
from datetime import UTC, datetime

from pydantic import ValidationError

from llmdup.core.kube import Kube
from llmdup.core.models import LlmdupError, Plan, StackPhase, StackState

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
    """Read the stack states out of `kubectl get configmap -o json`.

    Returns:
        One state per llmdup ConfigMap.

    Raises:
        LlmdupError: The document is not a ConfigMap list, or a state record is corrupt.
    """
    match doc:
        case {"items": list(items)}:
            pass
        case _:
            msg = "unexpected answer when listing llmdup state ConfigMaps"
            raise LlmdupError(msg)
    out = []
    for item in items:
        match item:
            case {"data": {"state": str(raw)}}:
                try:
                    out.append(StackState.model_validate_json(raw))
                except ValidationError as exc:
                    namespace = (item.get("metadata") or {}).get("namespace", "?")
                    msg = f"the {CONFIGMAP} ConfigMap in namespace {namespace} is not valid llmdup state"
                    raise LlmdupError(msg, hint=f"inspect or delete it: kubectl get configmap {CONFIGMAP} -n {namespace} -o yaml") from exc
    return out


async def load(kube: Kube, namespace: str | None = None) -> list[StackState]:
    """Return every stack llmdup recorded, in one namespace or in all of them.

    Returns:
        The recorded states.

    Raises:
        LlmdupError: The query failed, so llmdup cannot tell what is deployed.
    """
    scope = ["-n", namespace] if namespace else ["-A"]
    result = await kube.run("kubectl", "get", "configmap", *scope, "-l", MANAGED_BY, "-o", "json", timeout=30)
    if not result.ok:
        reason = (result.stderr.strip().splitlines() or ["no answer"])[-1]
        msg = f"cannot read llmdup state from the cluster: {reason}"
        raise LlmdupError(msg, hint="check the context; without rights to list ConfigMaps in all namespaces, pass --namespace")
    try:
        return parse(json.loads(result.stdout))
    except json.JSONDecodeError as exc:
        msg = "kubectl returned no JSON for the llmdup state query"
        raise LlmdupError(msg) from exc


async def delete(kube: Kube, namespace: str) -> None:
    """Remove the state ConfigMap of a namespace, if any."""
    await kube.run("kubectl", "delete", "configmap", CONFIGMAP, "-n", namespace, "--ignore-not-found", timeout=30)


def mark(state: StackState, phase: StackPhase, *, failed_step: str | None = None) -> StackState:
    """Set the phase and failing step of a state record."""
    state.phase = phase
    state.failed_step = failed_step
    return state
