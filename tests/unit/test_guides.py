# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
import json

from typer.testing import CliRunner

from llmdup.cli import app
from llmdup.core.guides import catalog


def test_catalog_reads_fixture_guides() -> None:
    entries = catalog().entries
    assert set(entries) == {"optimized-baseline", "flow-control", "pd-disaggregation"}
    assert entries["flow-control"].section == "operations-traffic"


def test_info_has_variables_modes_and_support() -> None:
    info = catalog().info("optimized-baseline")
    assert info.title == "Deploy an Optimized Baseline"
    assert info.modes == ["standalone", "gateway"]
    accel = next(v for v in info.variables if v.name == "ACCELERATOR_TYPE")
    assert accel.default == "gpu"
    assert "tpu/v6" in (accel.values or [])
    assert info.support is not None
    assert info.support.status("gpu", "vllm") == "validated"
    assert info.support.status("gpu", "trtllm") == "community"
    assert info.support.status("cpu", "sglang") == "unsupported"


def test_guides_list_json_and_section_filter() -> None:
    result = CliRunner().invoke(app, ["guides", "list", "--section", "foundations", "--json"])
    assert result.exit_code == 0
    names = [g["name"] for g in json.loads(result.stdout)["data"]["guides"]]
    assert names == ["optimized-baseline", "pd-disaggregation"]


def test_guides_show_unknown_exits_2() -> None:
    result = CliRunner().invoke(app, ["guides", "show", "nope"])
    assert result.exit_code == 2
