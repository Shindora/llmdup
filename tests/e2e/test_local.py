# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
import json
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e
CONTEXT = "kind-llmdup"
NAMESPACE = "llm-d-optimized-baseline"
LLMDUP = str(Path(sys.executable).parent / "llmdup")


def _llmdup(*args: str, timeout: float = 1200) -> subprocess.CompletedProcess[str]:
    return subprocess.run([LLMDUP, *args], capture_output=True, text=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL)


def _status() -> dict:
    result = _llmdup("status", "--context", CONTEXT, "--json", timeout=120)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["data"]


@pytest.fixture(scope="module", autouse=True)
def _guard() -> None:
    if not (shutil.which("docker") or shutil.which("podman")) or not shutil.which("kind"):
        pytest.skip("e2e needs Docker or Podman and kind")
    current = subprocess.run(["kubectl", "config", "current-context"], capture_output=True, text=True, check=False).stdout.strip()
    if not current.startswith("kind-llmdup"):
        pytest.fail(f"refusing to run e2e tests: current kube context is {current!r}, not kind-llmdup*")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _completion_status() -> int:
    port = _free_port()
    forward = subprocess.Popen(
        ["kubectl", "--context", CONTEXT, "port-forward", "-n", NAMESPACE, "svc/optimized-baseline-epp", f"{port}:80"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        body = json.dumps({"model": "llmdup-sim", "prompt": "How are you today?", "max_tokens": 8}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/completions", data=body, headers={"Content-Type": "application/json"})
        for _ in range(30):
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    assert "choices" in json.loads(response.read())
                    return response.status
            except OSError:
                time.sleep(1)
        return 0
    finally:
        forward.terminate()
        forward.wait()


def test_local_up_ends_with_http_200() -> None:
    result = _llmdup("local", "up", "--yes", "--json")
    assert result.returncode == 0, result.stderr[-4000:]
    doc = json.loads(result.stdout)
    assert doc["ok"] is True
    assert '"choices"' in result.stderr
    assert _completion_status() == 200


def test_status_shows_router_two_sim_pods_and_endpoint() -> None:
    (stack,) = _status()["stacks"]
    assert stack["health"] == "ready"
    roles = sorted(p["role"] for p in stack["pods"] if p["ready"])
    assert roles == ["model server", "model server", "router"]
    assert stack["endpoint"] == f"http://optimized-baseline-epp.{NAMESPACE}.svc.cluster.local:80"


def test_rerun_tests() -> None:
    result = _llmdup("test", "--context", CONTEXT)
    assert result.returncode == 0, result.stderr[-4000:]
    assert "HTTP 200" in result.stderr


def test_down_leaves_nothing() -> None:
    result = _llmdup("down", "--context", CONTEXT, "--yes")
    assert result.returncode == 0, result.stdout + result.stderr[-4000:]
    assert _status()["stacks"] == []


def test_interrupted_deploy_is_partial_and_down_cleans_it() -> None:
    proc = subprocess.Popen([LLMDUP, "local", "up", "--yes"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.stdout is not None
    for line in proc.stdout:
        if "local.router_small" in line:
            break
    proc.send_signal(signal.SIGTERM)
    rest = proc.communicate(timeout=60)[0]
    assert proc.returncode == 1, rest
    assert "interrupted" in rest
    (stack,) = _status()["stacks"]
    assert stack["health"] == "partial"
    assert stack["phase"] == "deploying"
    result = _llmdup("down", "--context", CONTEXT, "--yes")
    assert result.returncode == 0, result.stdout + result.stderr[-4000:]
    assert _status()["stacks"] == []
    gone = subprocess.run(["kubectl", "--context", CONTEXT, "get", "namespace", NAMESPACE], capture_output=True, check=False)
    assert gone.returncode != 0
