# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0

import os
import time
from collections.abc import AsyncGenerator

from llmdup.core.kube import CommandFailedError, Kube
from llmdup.core.models import (
    Action,
    Choices,
    ConfirmationNeededError,
    LlmdupError,
    LogLine,
    Profile,
    RunEvent,
    RunFinished,
    StepFinished,
    StepStarted,
)
from llmdup.core.planner import LOCAL_GUIDE, SIM_MODEL
from llmdup.core.runner import Prepared, prepare_up

CLUSTER = "llmdup"
CONTEXT = f"kind-{CLUSTER}"
NAMESPACE = f"llm-d-{LOCAL_GUIDE}"


def local_choices() -> Choices:
    """Return the choices of the local profile: optimized-baseline, standalone, simulator."""
    return Choices(guide=LOCAL_GUIDE, mode="standalone", model=SIM_MODEL, profile=Profile.LOCAL)


def kind_env(kube: Kube) -> dict[str, str]:
    """Pick Podman for kind when Docker is absent and Podman is present."""
    if os.environ.get("KIND_EXPERIMENTAL_PROVIDER") or kube.which("docker") or not kube.which("podman"):
        return {}
    return {"KIND_EXPERIMENTAL_PROVIDER": "podman"}


def local_kube() -> Kube:
    """Return a `Kube` pinned to the local kind cluster."""
    return Kube(context=CONTEXT)


async def cluster_exists(kube: Kube) -> bool:
    """Whether the kind cluster `llmdup` exists."""
    result = await kube.run("kind", "get", "clusters", env=kind_env(kube), timeout=60)
    return result.ok and CLUSTER in result.stdout.split()


async def _kind(kube: Kube, path: str, *args: str) -> AsyncGenerator[RunEvent]:
    yield StepStarted(path=path, index=1, total=1, command=" ".join(("kind", *args)))
    started = time.monotonic()
    try:
        async for line in kube.stream("kind", *args, env=kind_env(kube), timeout=600):
            yield LogLine(path=path, line=line)
    except CommandFailedError as exc:
        yield StepFinished(path=path, ok=False, returncode=exc.returncode, seconds=round(time.monotonic() - started, 1))
        return
    yield StepFinished(path=path, ok=True, seconds=round(time.monotonic() - started, 1))


async def create_cluster(kube: Kube) -> AsyncGenerator[RunEvent]:
    """Create the kind cluster `llmdup` unless it exists.

    Yields:
        Step events for the `kind create cluster` run; nothing when the cluster exists.

    Raises:
        LlmdupError: kind could not create the cluster.
    """
    if await cluster_exists(kube):
        return
    async for event in _kind(kube, "local.cluster", "create", "cluster", "--name", CLUSTER, "--wait", "120s"):
        yield event
        if isinstance(event, StepFinished) and not event.ok:
            msg = "kind create cluster failed"
            raise LlmdupError(msg, hint="is Docker or Podman running? For Podman set KIND_EXPERIMENTAL_PROVIDER=podman")


async def prepare_local(kube: Kube, *, verify: bool = True) -> Prepared:
    """Run the preflight checks and build the local profile's deploy plan on the kind cluster."""
    return await prepare_up(kube, local_choices(), verify=verify)


async def delete_cluster(kube: Kube, *, confirmed: bool) -> AsyncGenerator[RunEvent]:
    """Delete the kind cluster `llmdup`.

    Yields:
        Step events, then a `RunFinished`.

    Raises:
        ConfirmationNeededError: `confirmed` is False.
    """
    if not confirmed:
        msg = f"deleting the kind cluster {CLUSTER} needs confirmation"
        raise ConfirmationNeededError(msg, hint=f"command: kind delete cluster --name {CLUSTER}; pass --yes")
    ok = True
    if await cluster_exists(kube):
        async for event in _kind(kube, "local.cluster", "delete", "cluster", "--name", CLUSTER):
            if isinstance(event, StepFinished):
                ok = event.ok
            yield event
    message = f"kind cluster {CLUSTER} deleted" if ok else f"could not delete kind cluster {CLUSTER}"
    yield RunFinished(action=Action.DOWN, guide=LOCAL_GUIDE, namespace=NAMESPACE, ok=ok, message=message)
