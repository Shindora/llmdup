# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0
import asyncio
import json
import urllib.error
from email.message import Message
from pathlib import Path

import pytest
from typer.testing import CliRunner

from llmdup import cli
from llmdup.core import upstream
from llmdup.core.detect import pinned
from llmdup.core.doctor import doctor
from llmdup.core.kube import CommandResult, FakeKube, Kube
from llmdup.core.models import Action, CheckStatus, Choices, LlmdupError, LogLine, StepTag, UsageError
from llmdup.core.planner import _command_lines, build_plan
from llmdup.core.runner import _readiness, confirm_commands, leftovers, run_plan
from llmdup.core.state import load

from .kubefakes import recorded

STATE = {"guide": "flow-control", "namespace": "llm-d-flow-control", "llmd_ref": "v1.0.0", "phase": "deployed"}
CONFIGMAPS = json.dumps({"items": [{"metadata": {"namespace": "llm-d-flow-control"}, "data": {"state": json.dumps(STATE)}}]})


def _with_state(kube: FakeKube) -> FakeKube:
    kube.responses["kubectl", "get", "configmap"] = CONFIGMAPS
    return kube


def test_tests_that_create_cluster_rbac_need_confirmation() -> None:
    plan = build_plan(Choices(guide="flow-control"), action=Action.TEST)
    assert "verify.tests.metrics_rbac" in plan.needs_confirmation
    tags = {s.path: s.tags for s in plan.steps}
    assert StepTag.CLUSTER_SCOPED in tags["verify.tests.metrics_rbac"]


def test_cli_test_without_yes_and_no_tty_exits_4(monkeypatch: pytest.MonkeyPatch) -> None:
    kube = _with_state(recorded("cpu-kind"))
    monkeypatch.setattr(cli, "Kube", lambda **_: kube)
    result = CliRunner().invoke(cli.app, ["test", "--json"])
    assert result.exit_code == 4, result.output
    assert "metrics_rbac" in json.loads(result.stdout)["errors"][0]["message"]
    assert not any(call[0] == "bash" for call in kube.calls)


def test_context_is_resolved_once_and_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLMDUP_CONTEXT", "inherited")
    kube = FakeKube(responses={("kubectl", "config", "current-context"): "ctx-a\n"})
    found = asyncio.run(pinned(kube))
    assert found.context == "ctx-a"
    assert found.argv("kubectl", ("get", "pods"))[:3] == ("kubectl", "--context", "ctx-a")
    assert found.argv("helm", ("list",))[:3] == ("helm", "--kube-context", "ctx-a")
    assert found.process_env()["LLMDUP_CONTEXT"] == "ctx-a"
    assert "LLMDUP_CONTEXT" not in Kube().process_env()


def test_leftovers_namespace_not_found_is_clean() -> None:
    plan = build_plan(Choices(guide="optimized-baseline"), action=Action.DOWN)
    gone = CommandResult(("kubectl",), 1, stderr='Error from server (NotFound): namespaces "x" not found')
    assert asyncio.run(leftovers(FakeKube(responses={("kubectl", "get", "namespace"): gone}), plan)) == []


def test_leftovers_forbidden_namespace_lookup_is_not_clean() -> None:
    plan = build_plan(Choices(guide="optimized-baseline"), action=Action.DOWN)
    forbidden = CommandResult(("kubectl",), 1, stderr="Error from server (Forbidden): namespaces is forbidden")
    left = asyncio.run(leftovers(FakeKube(responses={("kubectl", "get", "namespace"): forbidden}), plan))
    assert left
    assert "could not be checked" in left[0]


def test_leftovers_keep_namespace_checks_helm_and_pods() -> None:
    plan = build_plan(Choices(guide="optimized-baseline"), action=Action.DOWN, keep_namespace=True)
    kube = FakeKube(
        responses={
            ("helm", "list"): json.dumps([{"name": "optimized-baseline", "namespace": "llm-d-optimized-baseline"}]),
            ("kubectl", "get", "pods"): json.dumps({"items": []}),
        },
    )
    assert asyncio.run(leftovers(kube, plan)) == ["helm release optimized-baseline"]
    assert not any(call[:3] == ("kubectl", "get", "namespace") for call in kube.calls)


def test_state_query_failure_raises() -> None:
    with pytest.raises(LlmdupError, match="cannot read llmdup state"):
        asyncio.run(load(FakeKube()))


def test_status_on_unreachable_cluster_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "Kube", lambda **_: FakeKube(context="gone"))
    result = CliRunner().invoke(cli.app, ["status", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["ok"] is False


def test_keep_namespace_skips_only_the_namespace_delete() -> None:
    plan = build_plan(Choices(guide="flow-control"), action=Action.DOWN, keep_namespace=True)
    assert "cleanup[5]" not in [s.path for s in plan.steps]
    assert "cleanup[0]" in [s.path for s in plan.steps]
    assert _command_lines({"run": "kubectl delete namespace x"}) == 1
    assert _command_lines({"run": "helm uninstall a\nkubectl delete namespace x"}) == 2
    assert _command_lines({"run": "kubectl delete \\\n  namespace x"}) == 1


@pytest.mark.parametrize(("options", "needle"), [({"modelserver": "bogus"}, "not one of"), ({"nope": "x"}, "no manual option group")])
def test_bad_options_are_refused(options: dict[str, str], needle: str) -> None:
    with pytest.raises(UsageError, match=needle):
        build_plan(Choices(guide="optimized-baseline", options=options))


def test_steps_run_in_a_fresh_directory_not_the_cwd() -> None:
    plan = build_plan(Choices(guide="optimized-baseline"), action=Action.UP)
    plan = plan.model_copy(update={"steps": [], "env_script": "#!/usr/bin/env bash\npwd\n"})

    async def collect() -> list[str]:
        return [e.line async for e in run_plan(plan, Kube()) if isinstance(e, LogLine)]

    (cwd,) = asyncio.run(collect())
    assert Path(cwd).name.startswith("llmdup-run-")
    assert not Path(cwd).exists()


def test_unexpected_errors_still_print_one_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: object, **__: object) -> None:
        msg = "kaput"
        raise RuntimeError(msg)

    monkeypatch.setattr(cli, "build_plan", boom)
    result = CliRunner().invoke(cli.app, ["plan", "optimized-baseline", "--json"])
    assert result.exit_code == 1
    doc = json.loads(result.stdout)
    assert doc["errors"][0]["code"] == "internal"


def test_unknown_ref_is_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLMDUP_LLMD_PATH")

    def missing(*_: object, **__: object) -> None:
        reason = "Not Found"
        url = "https://example.invalid/x.tar.gz"
        raise urllib.error.HTTPError(url, 404, reason, Message(), None)

    monkeypatch.setattr(upstream.urllib.request, "urlopen", missing)
    with pytest.raises(UsageError, match="no tag"):
        upstream.release("v9.9.8")


def test_confirmation_names_context_namespace_and_expands_commands() -> None:
    plan = build_plan(Choices(guide="optimized-baseline", namespace="team-a"), action=Action.DOWN)
    header, *commands = confirm_commands(plan, "prod-east")
    assert "prod-east" in header
    assert "team-a" in header
    assert any("helm uninstall optimized-baseline -n team-a" in c for c in commands)


def test_doctor_fails_when_namespace_holds_a_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "hf_x")
    kube = _with_state(recorded("gpu-gke"))
    report = asyncio.run(doctor(kube, Choices(guide="optimized-baseline", namespace="llm-d-flow-control")))
    status = {c.name: c.status for c in report.checks}
    assert status["Existing stack"] is CheckStatus.FAIL


def test_readiness_ignores_unrelated_pods() -> None:
    plan = build_plan(Choices(guide="optimized-baseline"))

    def pod(name: str, *, ready: bool, labels: dict[str, str] | None = None) -> dict[str, object]:
        conditions = [{"type": "Ready", "status": "True" if ready else "False"}]
        return {"metadata": {"name": name, "labels": labels or {}}, "status": {"phase": "Running", "conditions": conditions}}

    pods = [
        pod("sim-1", ready=True, labels={"llm-d.ai/guide": "optimized-baseline"}),
        pod("optimized-baseline-epp-1", ready=True),
        pod("someone-elses-job", ready=False),
    ]
    kube = FakeKube(
        responses={
            ("kubectl", "get", "pods"): json.dumps({"items": pods}),
            ("kubectl", "get", "deployments"): json.dumps({"items": []}),
        },
    )
    done, progress, _ = asyncio.run(_readiness(kube, plan))
    assert done
    assert progress.total == 2


def test_plan_script_and_json_together_exit_2() -> None:
    result = CliRunner().invoke(cli.app, ["plan", "optimized-baseline", "--script", "--json"])
    assert result.exit_code == 2


def test_local_down_without_yes_and_no_tty_exits_4(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.local_core, "local_kube", lambda: FakeKube(context="kind-llmdup"))
    result = CliRunner().invoke(cli.app, ["local", "down"])
    assert result.exit_code == 4
