# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0

import asyncio
import json

from llmdup.core.kube import Kube
from llmdup.core.models import ClusterFacts, HelmRelease

GKE_NODEPOOL = "cloud.google.com/gke-nodepool"
TPU_LABEL = "cloud.google.com/gke-tpu-accelerator"
ALLOCATABLE_ACCELERATORS = {
    "nvidia.com/gpu": "gpu",
    "amd.com/gpu": "amd",
    "google.com/tpu": "tpu",
    "iluvatar.com/gpu": "iluvatar",
    "metax-tech.com/gpu": "metax",
    "birentech.com/gpu": "biren",
    "mthreads.com/gpu": "mthreads",
}
DEVICE_CLASS_ACCELERATORS = {
    "gpu.nvidia.com": "gpu",
    "gpu.intel.com": "xpu",
    "npu.rebellions.ai": "npu",
}
TPU_TYPES = {
    "tpu-v5p-slice": "tpu/v5p",
    "tpu-v6e-slice": "tpu/v6",
    "tpu7x": "tpu/v7",
}
INFERENCE_POOL_CRD = "inferencepools.inference.networking.k8s.io"


def _names(doc: object) -> list[str]:
    match doc:
        case {"items": list(items)}:
            return [name for item in items if isinstance(name := _get(item, "metadata", "name"), str)]
    return []


def _get(doc: object, *keys: str) -> object:
    for key in keys:
        match doc:
            case {**fields} if key in fields:
                doc = fields[key]
            case _:
                return None
    return doc


def _quantity(value: object) -> int:
    try:
        return int(str(value))
    except ValueError:
        return 0


def read_nodes(doc: object, facts: ClusterFacts) -> None:
    """Fill node count, provider and accelerator facts from `kubectl get nodes -o json`."""
    match doc:
        case {"items": list(nodes)}:
            pass
        case _:
            return
    facts.nodes = len(nodes)
    tpu_types: set[str] = set()
    for node in nodes:
        labels = _get(node, "metadata", "labels")
        labels = labels if isinstance(labels, dict) else {}
        if GKE_NODEPOOL in labels:
            facts.provider = "gke"
        if TPU_LABEL in labels:
            tpu_types.add(str(labels[TPU_LABEL]))
        match _get(node, "status", "allocatable"):
            case {**allocatable}:
                for resource in ALLOCATABLE_ACCELERATORS.keys() & allocatable.keys():
                    facts.allocatable[resource] = facts.allocatable.get(resource, 0) + _quantity(allocatable[resource])
    present = {k: v for k, v in facts.allocatable.items() if v > 0}
    if present:
        resource = max(present, key=present.__getitem__)
        accelerator = ALLOCATABLE_ACCELERATORS[resource]
        if accelerator == "tpu":
            accelerator = next((TPU_TYPES[t] for t in sorted(tpu_types) if t in TPU_TYPES), "tpu/v6")
        facts.accelerator, facts.accelerator_resource, facts.accelerator_count = accelerator, resource, present[resource]


def read_device_classes(doc: object, facts: ClusterFacts) -> None:
    """Record DRA DeviceClasses and map them to an accelerator when allocatable showed none."""
    facts.device_classes = _names(doc)
    if facts.accelerator:
        return
    for name in facts.device_classes:
        if name in DEVICE_CLASS_ACCELERATORS:
            facts.accelerator = DEVICE_CLASS_ACCELERATORS[name]
            return


def read_helm(doc: object) -> list[HelmRelease]:
    """Parse `helm list -A -o json`."""
    match doc:
        case list(rows):
            return [
                HelmRelease(name=str(r["name"]), namespace=str(r["namespace"]), status=r.get("status"), chart=r.get("chart"))
                for r in rows
                if isinstance(r, dict) and "name" in r and "namespace" in r
            ]
    return []


async def current_context(kube: Kube) -> str | None:
    """Return the context `kube` talks to: its pinned one, else the kubeconfig's current one."""
    if kube.context:
        return kube.context
    result = await kube.run("kubectl", "config", "current-context", timeout=10)
    return (result.stdout.strip() or None) if result.ok else None


async def detect(kube: Kube, *, timeout: float = 20) -> ClusterFacts:
    """Collect context, version, CRDs, Helm releases, provider and accelerators.

    Args:
        kube: The command runner, pinned to a context or not.
        timeout: Seconds allowed for each query.

    Returns:
        The facts; `reachable` is False when the API server does not answer.
    """
    facts = ClusterFacts(context=await current_context(kube))
    version = await kube.run("kubectl", "version", "-o", "json", timeout=timeout)
    match _parse(version.stdout):
        case {"serverVersion": {"gitVersion": str(git_version)}}:
            facts.reachable, facts.server_version = True, git_version
        case _:
            facts.error = (version.stderr.strip().splitlines() or ["no answer from the API server"])[-1]
            return facts
    async with asyncio.TaskGroup() as tg:
        nodes = tg.create_task(kube.json("kubectl", "get", "nodes", "-o", "json", timeout=timeout))
        crds = tg.create_task(kube.json("kubectl", "get", "crd", "-o", "json", timeout=timeout))
        helm = tg.create_task(kube.json("helm", "list", "-A", "-o", "json", timeout=timeout))
        classes = tg.create_task(kube.json("kubectl", "get", "deviceclasses.resource.k8s.io", "-o", "json", timeout=timeout))
        gateways = tg.create_task(kube.json("kubectl", "get", "gatewayclasses.gateway.networking.k8s.io", "-o", "json", timeout=timeout))
    read_nodes(nodes.result(), facts)
    read_device_classes(classes.result(), facts)
    facts.crds = _names(crds.result())
    facts.helm_releases = read_helm(helm.result())
    facts.gateway_classes = _names(gateways.result())
    return facts


def _parse(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
