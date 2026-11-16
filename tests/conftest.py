# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "llm-d-v1.0.0"


@pytest.fixture(autouse=True)
def _offline(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    markers = {m.name for m in request.node.iter_markers()}
    if "e2e" not in markers:
        monkeypatch.setenv("KUBECONFIG", str(tmp_path / "no-kubeconfig"))
    if not markers & {"network", "e2e"}:
        monkeypatch.setenv("LLMDUP_LLMD_PATH", str(FIXTURE))
        monkeypatch.setenv("LLMDUP_CACHE_DIR", str(tmp_path / "cache"))
