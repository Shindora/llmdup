# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
import pytest


@pytest.fixture(autouse=True)
def _no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
