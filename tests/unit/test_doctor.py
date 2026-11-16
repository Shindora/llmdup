# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
import asyncio
import json

import pytest
from typer.testing import CliRunner

from llmdup import cli
from llmdup.core.detect import detect
from llmdup.core.doctor import accelerator_requests, doctor
from llmdup.core.models import CheckStatus, Choices, DoctorReport

from .kubefakes import CLUSTERS, recorded, unreachable


def _status(report: DoctorReport) -> dict[str, CheckStatus]:
    return {c.name: c.status for c in report.checks}


def test_detect_gpu_cluster() -> None:
    facts = asyncio.run(detect(recorded("gpu-gke")))
    assert facts.reachable
    assert facts.server_version == "v1.34.2-gke.1100000"
    assert (facts.accelerator, facts.accelerator_count, facts.provider) == ("gpu", 8, "gke")
    assert "inferencepools.inference.networking.k8s.io" in facts.crds
    assert facts.gateway_classes == ["gke-l7-rilb"]


def test_detect_cpu_cluster() -> None:
    facts = asyncio.run(detect(recorded("cpu-kind")))
    assert facts.reachable
    assert facts.accelerator is None
    assert facts.provider == "base"
    assert facts.nodes == 1


def test_detect_unreachable_cluster() -> None:
    facts = asyncio.run(detect(unreachable()))
    assert not facts.reachable
    assert "refused" in (facts.error or "")


def test_doctor_gpu_cluster_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "hf_x")
    report = asyncio.run(doctor(recorded("gpu-gke"), Choices(guide="optimized-baseline")))
    status = _status(report)
    assert report.ok, report.checks
    assert status["Capacity"] is CheckStatus.OK
    assert status["Inference Extension CRDs"] is CheckStatus.OK
    assert status["Accelerators"] is CheckStatus.OK


def test_doctor_gpu_existing_release_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "hf_x")
    report = asyncio.run(doctor(recorded("gpu-gke"), Choices(guide="optimized-baseline", namespace="team-a")))
    assert _status(report)["Existing release"] is CheckStatus.FAIL


def test_doctor_cpu_cluster_warns_and_fails_on_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    kube = recorded("cpu-kind")
    kube.responses["kubectl", "kustomize"] = (CLUSTERS / "gpu-gke" / "kustomize-gpu-vllm.yaml").read_text()
    report = asyncio.run(doctor(kube, Choices(guide="optimized-baseline")))
    status = _status(report)
    assert status["Inference Extension CRDs"] is CheckStatus.WARN
    assert status["Accelerators"] is CheckStatus.WARN
    assert status["Capacity"] is CheckStatus.WARN
    assert status["HF_TOKEN"] is CheckStatus.FAIL
    assert not report.ok


def test_doctor_unsupported_pair_and_gateway() -> None:
    choices = Choices(guide="optimized-baseline", accelerator="cpu", engine="sglang", mode="gateway")
    report = asyncio.run(doctor(recorded("cpu-kind"), choices))
    status = _status(report)
    assert status["Guide support"] is CheckStatus.FAIL
    assert status["Gateway provider"] is CheckStatus.WARN


def test_doctor_community_pair_warns() -> None:
    report = asyncio.run(doctor(recorded("gpu-gke"), Choices(guide="optimized-baseline", engine="trtllm")))
    assert _status(report)["Guide support"] is CheckStatus.WARN


def test_doctor_unreachable_fails() -> None:
    report = asyncio.run(doctor(unreachable()))
    assert _status(report) == {"Tools": CheckStatus.OK, "Cluster reachable": CheckStatus.FAIL}


def test_doctor_missing_tools() -> None:
    kube = recorded("cpu-kind")
    kube.tools = frozenset({"kubectl"})
    report = asyncio.run(doctor(kube))
    assert _status(report)["Tools"] is CheckStatus.FAIL


def test_accelerator_requests_counts_replicas() -> None:
    rendered = (CLUSTERS / "gpu-gke" / "kustomize-gpu-vllm.yaml").read_text()
    assert accelerator_requests(rendered) == {"nvidia.com/gpu": 4}


def test_doctor_cli_exit_3_and_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "Kube", lambda **_: unreachable())
    result = CliRunner().invoke(cli.app, ["doctor", "--json"])
    assert result.exit_code == 3
    doc = json.loads(result.stdout)
    assert doc["ok"] is False
    assert doc["errors"][0]["message"].startswith("Cluster reachable")


def test_cli_render_rejects_unknown_types() -> None:
    with pytest.raises(NotImplementedError):
        cli.render(object())
