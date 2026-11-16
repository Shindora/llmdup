# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
import json

from typer.testing import CliRunner

from llmdup import __version__
from llmdup.cli import app


def test_version_json_prints_envelope() -> None:
    result = CliRunner().invoke(app, ["version", "--json"])
    assert result.exit_code == 0
    doc = json.loads(result.stdout)
    assert doc == {
        "schema_version": 1,
        "ok": True,
        "command": "version",
        "data": {"llmdup": __version__, "llmd_ref": "v1.0.0"},
        "errors": [],
    }


def test_version_human() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert f"llmdup {__version__} (llm-d v1.0.0)" in result.stdout
