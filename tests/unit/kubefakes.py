# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

from llmdup.core.kube import CommandResult, FakeKube

CLUSTERS = Path(__file__).parents[1] / "fixtures" / "clusters"
QUERIES = {
    "version.json": ("kubectl", "version", "-o", "json"),
    "nodes.json": ("kubectl", "get", "nodes", "-o", "json"),
    "crds.json": ("kubectl", "get", "crd", "-o", "json"),
    "helm.json": ("helm", "list", "-A", "-o", "json"),
    "deviceclasses.json": ("kubectl", "get", "deviceclasses.resource.k8s.io", "-o", "json"),
    "gatewayclasses.json": ("kubectl", "get", "gatewayclasses.gateway.networking.k8s.io", "-o", "json"),
    "gateways.json": ("kubectl", "get", "gateways.gateway.networking.k8s.io", "-A", "-o", "json"),
}


def recorded(name: str, context: str = "test-ctx") -> FakeKube:
    """A FakeKube that answers detection queries with a recorded cluster."""
    responses: dict[tuple[str, ...], CommandResult | str] = {}
    for file, key in QUERIES.items():
        path = CLUSTERS / name / file
        if path.is_file():
            responses[key] = path.read_text()
    kustomize = CLUSTERS / name / "kustomize-gpu-vllm.yaml"
    if kustomize.is_file():
        responses["kubectl", "kustomize"] = kustomize.read_text()
    return FakeKube(context=context, responses=responses)


def unreachable() -> FakeKube:
    """A FakeKube whose API server refuses connections."""
    version = CommandResult(
        ("kubectl", "version"),
        1,
        (CLUSTERS / "unreachable-version.json").read_text(),
        (CLUSTERS / "unreachable-version.err").read_text(),
    )
    return FakeKube(context="gone", responses={QUERIES["version.json"]: version})
