# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
import asyncio
import json
import os
import sys
import typing
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from llmdup import mcp_server
from llmdup.core.kube import FakeKube
from llmdup.core.models import RunEvent

from .kubefakes import recorded

READ_ONLY = {("kubectl", "version"), ("kubectl", "get"), ("kubectl", "config"), ("kubectl", "kustomize"), ("helm", "list")}
EXPECTED_TOOLS = {"list_guides", "show_guide", "doctor", "plan", "up", "status", "test", "down"}


def _server(kube: FakeKube | None = None) -> typing.Any:
    fake = kube or recorded("cpu-kind")
    return mcp_server.build_server(lambda **_: fake)


def test_client_lists_eight_tools_and_calls_plan() -> None:
    async def run() -> tuple[set[str], dict]:
        async with Client(_server()) as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            result = await client.call_tool("plan", {"guide": "optimized-baseline", "accelerator": "gpu"})
            assert not result.is_error, result.content
            return tools, result.structured_content or {}

    tools, plan = asyncio.run(run())
    assert tools == EXPECTED_TOOLS
    assert [s["path"] for s in plan["steps"]][:2] == ["prerequisites.gaie", "prerequisites.namespace"]


def test_plan_error_reaches_the_agent() -> None:
    async def run() -> str:
        async with Client(_server()) as client:
            result = await client.call_tool("plan", {"guide": "optimized-baseline", "accelerator": "bogus"})
            assert result.is_error
            return json.dumps([c.model_dump() for c in result.content])

    text = asyncio.run(run())
    assert "allowed values" in text
    assert "gpu" in text


def test_up_without_confirm_changes_nothing() -> None:
    kube = recorded("cpu-kind")

    async def run() -> dict:
        async with Client(_server(kube)) as client:
            result = await client.call_tool("up", {"guide": "optimized-baseline"})
            assert not result.is_error, result.content
            return (result.structured_content or {})["result"]

    prepared = asyncio.run(run())
    assert prepared["plan"]["guide"] == "optimized-baseline"
    assert prepared["confirm"]
    assert all(call[:2] in READ_ONLY for call in kube.calls), kube.calls
    assert not any(call[0] == "bash" for call in kube.calls)


def test_down_without_confirm_changes_nothing() -> None:
    kube = recorded("cpu-kind")
    state = {"guide": "optimized-baseline", "namespace": "llm-d-optimized-baseline", "llmd_ref": "v1.0.0"}
    kube.responses["kubectl", "get", "configmap"] = json.dumps({"items": [{"data": {"state": json.dumps(state)}}]})

    async def run() -> dict:
        async with Client(_server(kube)) as client:
            result = await client.call_tool("down", {})
            assert not result.is_error, result.content
            return (result.structured_content or {})["result"]

    prepared = asyncio.run(run())
    assert prepared["plan"]["action"] == "down"
    assert prepared["confirm"]
    assert all(call[:2] in READ_ONLY for call in kube.calls), kube.calls


def test_mcp_renders_every_run_event() -> None:
    for event_type in typing.get_args(RunEvent.__value__):
        assert mcp_server.render.dispatch(event_type) is not mcp_server.render.dispatch(object), event_type


def test_llmdup_mcp_serves_stdio(tmp_path: Path) -> None:
    llmdup = str(Path(sys.executable).parent / "llmdup")
    env = {**os.environ, "KUBECONFIG": str(tmp_path / "none")}

    async def run() -> set[str]:
        async with Client(StdioServerParameters(command=llmdup, args=["mcp"], env=env)) as client:
            return {t.name for t in (await client.list_tools()).tools}

    assert asyncio.run(run()) == EXPECTED_TOOLS


@pytest.mark.parametrize("tool", sorted(EXPECTED_TOOLS))
def test_tools_have_descriptions(tool: str) -> None:
    async def run() -> str:
        async with Client(_server()) as client:
            listed = {t.name: t for t in (await client.list_tools()).tools}
            return listed[tool].description or ""

    assert asyncio.run(run())
