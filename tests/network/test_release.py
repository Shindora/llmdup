# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import pytest

from llmdup.core.guides import catalog
from llmdup.core.models import Action, Choices
from llmdup.core.planner import build_plan

pytestmark = pytest.mark.network


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    monkeypatch.delenv("LLMDUP_LLMD_PATH", raising=False)
    monkeypatch.setenv("LLMDUP_CACHE_DIR", str(tmp_path_factory.getbasetemp() / "llmd-cache"))


def test_real_release_plans_all_27_guides() -> None:
    found = catalog("v1.0.0")
    assert found.release.commit
    assert Path(found.release.path, "scripts", "guide.py").is_file()
    assert len(found.entries) == 27
    for name in found.entries:
        for action in Action:
            plan = build_plan(Choices(guide=name), action=action, ref="v1.0.0")
            if action is Action.UP:
                assert any(s.path.startswith("deploy.") for s in plan.steps), name
