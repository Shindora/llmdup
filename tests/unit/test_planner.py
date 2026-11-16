# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
import json
import subprocess

import pytest
from typer.testing import CliRunner

from llmdup.cli import app
from llmdup.core.models import Action, Choices, StepTag, UsageError
from llmdup.core.planner import Detected, build_plan

EXPECTED = [
    "prerequisites.gaie",
    "prerequisites.namespace",
    "prerequisites.secrets",
    "deploy.router_values",
    "deploy.standalone",
    "deploy.modelserver.standard",
    "verify.endpoint.standalone",
    "verify.tests.request",
]


def test_plan_json_lists_expected_steps_in_order() -> None:
    result = CliRunner().invoke(app, ["plan", "optimized-baseline", "--accelerator", "gpu", "--json"])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert doc["ok"] is True
    assert [s["path"] for s in doc["data"]["steps"]] == EXPECTED


def test_plan_bogus_accelerator_exits_2_with_allowed_values() -> None:
    result = CliRunner().invoke(app, ["plan", "optimized-baseline", "--accelerator", "bogus"])
    assert result.exit_code == 2
    assert "gpu, amd, xpu" in result.stderr


def test_plan_bogus_accelerator_json_envelope() -> None:
    result = CliRunner().invoke(app, ["plan", "optimized-baseline", "--accelerator", "bogus", "--json"])
    assert result.exit_code == 2
    doc = json.loads(result.stdout)
    assert doc["ok"] is False
    assert "gpu" in doc["errors"][0]["hint"]


def test_plan_script_passes_bash_n() -> None:
    result = CliRunner().invoke(app, ["plan", "optimized-baseline", "--accelerator", "gpu", "--script"])
    assert result.exit_code == 0
    check = subprocess.run(["bash", "-n"], input=result.stdout, text=True, capture_output=True, check=False)
    assert check.returncode == 0, check.stderr
    assert "# === deploy.modelserver.standard ===" in result.stdout


def test_plan_tags_and_confirmation() -> None:
    plan = build_plan(Choices(guide="optimized-baseline"))
    tags = {s.path: s.tags for s in plan.steps}
    assert StepTag.CLUSTER_SCOPED in tags["prerequisites.gaie"]
    assert StepTag.SENDS_TRAFFIC in tags["verify.tests.request"]
    assert plan.needs_confirmation == ["prerequisites.gaie"]
    skipped = {s.path for s in plan.skipped}
    assert {"prerequisites.clone", "deploy.gateway", "deploy.modelserver.dynamic_slice"} <= skipped


def test_plan_gateway_mode_and_manual_option() -> None:
    plan = build_plan(
        Choices(guide="optimized-baseline", mode="gateway", accelerator="tpu/v7-dynamic-slice", options={"modelserver": "dynamic_slice"}),
    )
    paths = [s.path for s in plan.steps]
    assert "deploy.gateway" in paths
    assert "deploy.standalone" not in paths
    assert "deploy.modelserver.dynamic_slice" in paths
    assert "deploy.modelserver.standard" not in paths


def test_plan_no_verify_drops_tests() -> None:
    plan = build_plan(Choices(guide="optimized-baseline"), verify=False)
    assert not any(StepTag.SENDS_TRAFFIC in s.tags for s in plan.steps)


def test_plan_detection_sets_provider_and_model() -> None:
    plan = build_plan(Choices(guide="optimized-baseline"), detected=Detected(accelerator="cpu", provider="gke"))
    values = {v.name: (v.value, v.source) for v in plan.variables}
    assert values["ACCELERATOR_TYPE"] == ("cpu", "detected")
    assert values["INFRA_PROVIDER"] == ("gke", "detected")
    assert values["MODEL"] == ("meta-llama/Llama-3.2-3B-Instruct", "support matrix")


def test_plan_flag_beats_detection() -> None:
    plan = build_plan(Choices(guide="optimized-baseline", accelerator="amd"), detected=Detected(accelerator="gpu"))
    assert plan.overrides["ACCELERATOR_TYPE"] == "amd"


@pytest.mark.parametrize(
    ("choices", "needle"),
    [
        (Choices(guide="optimized-baseline", mode="bogus"), "no mode"),
        (Choices(guide="optimized-baseline", sets={"NOPE": "1"}), "declares no variable"),
        (Choices(guide="flow-control", accelerator="gpu"), "no ACCELERATOR_TYPE"),
        (Choices(guide="nope"), "unknown guide"),
    ],
)
def test_plan_rejects_bad_choices(choices: Choices, needle: str) -> None:
    with pytest.raises(UsageError, match=needle):
        build_plan(choices)


def test_down_plan_needs_confirmation_for_every_step() -> None:
    plan = build_plan(Choices(guide="optimized-baseline"), action=Action.DOWN)
    assert [s.path for s in plan.steps] == ["cleanup.modelserver.standard", "cleanup.rest"]
    assert plan.needs_confirmation == ["cleanup.modelserver.standard", "cleanup.rest"]


def test_plan_never_contains_hf_token_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "hf_secret_value")
    plan = build_plan(Choices(guide="optimized-baseline"))
    assert "hf_secret_value" not in plan.model_dump_json()
