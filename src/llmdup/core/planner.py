# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0

import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from llmdup.core.guides import DEFAULT_OPTION, Catalog, GuideEntry, catalog, manual_options, modes_of, static_tags, step_lists
from llmdup.core.models import (
    Action,
    Choices,
    ClusterFacts,
    GuideInfo,
    Plan,
    PlanVariable,
    Profile,
    SkippedStep,
    Step,
    StepTag,
    UsageError,
    VariableSource,
)
from llmdup.core.upstream import DEFAULT_LLMD_REF

FLAG_VARIABLES = {
    "accelerator": "ACCELERATOR_TYPE",
    "engine": "MODEL_SERVER",
    "provider": "INFRA_PROVIDER",
    "namespace": "NAMESPACE",
    "model": "MODEL",
}
ACTION_SECTIONS = {
    Action.UP: ("prerequisites", "deploy", "verify"),
    Action.TEST: ("verify",),
    Action.DOWN: ("cleanup",),
}
SECRET_NAMES = frozenset({"HF_TOKEN"})
LOCAL_PROFILE = Path(__file__).parents[1] / "local_profile"
LOCAL_GUIDE = "optimized-baseline"
SIM_MODEL = "llmdup-sim"
GATEWAY_NAME = "llm-d-inference-gateway"
# ponytail: GKE's built-in GatewayClasses all start with this; read the class's controllerName if one ever does not
GKE_GATEWAY_CLASS = "gke-l7-"
INDEX_SUFFIX = re.compile(r"\[\d+\]$")
DELETE_NAMESPACE = re.compile(r"kubectl\s+delete\s+namespace\b")


@dataclass(frozen=True)
class Detected:
    """Values detected on the cluster, used when the user did not choose them."""

    accelerator: str | None = None
    provider: str | None = None
    gateways: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_facts(cls, facts: ClusterFacts) -> Detected:
        """Take the accelerator, provider and Gateways that detection found."""
        return cls(accelerator=facts.accelerator, provider=facts.provider, gateways=facts.gateways)


@dataclass
class _Resolved:
    overrides: dict[str, str] = field(default_factory=dict)
    sources: dict[str, VariableSource] = field(default_factory=dict)

    def put(self, name: str, value: str, source: VariableSource) -> None:
        self.overrides[name] = value
        self.sources[name] = source


def resolve_variables(info: GuideInfo, choices: Choices, detected: Detected | None = None) -> _Resolved:
    """Merge flags, detection and the support matrix into `--var` overrides.

    Args:
        info: The guide.
        choices: What the user picked.
        detected: What detection found on the cluster.

    Returns:
        The overrides and where each came from.

    Raises:
        UsageError: A flag names a variable the guide lacks, or a value outside its allowed values.
    """
    declared = {v.name: v for v in info.variables}
    resolved = _Resolved()
    for flag, name in FLAG_VARIABLES.items():
        value = getattr(choices, flag)
        if value is None:
            continue
        if name not in declared:
            msg = f"{info.name} has no {name} variable, so --{flag} does not apply"
            raise UsageError(msg, hint=f"variables: {', '.join(declared)}")
        resolved.put(name, value, VariableSource.FLAG)
    for name, value in choices.sets.items():
        if name not in declared:
            msg = f"{info.name} declares no variable {name!r}"
            raise UsageError(msg, hint=f"variables: {', '.join(declared)}")
        if declared[name].sensitive or name in SECRET_NAMES:
            msg = f"{name} is sensitive; llmdup reads it from the environment only"
            raise UsageError(msg, hint=f"export {name} before running llmdup")
        resolved.put(name, value, VariableSource.FLAG)
    _apply_detected(info, resolved, detected or Detected())
    for name, value in resolved.overrides.items():
        allowed = declared[name].values
        if allowed and value not in allowed:
            msg = f"{name}={value} is not allowed by {info.name}"
            raise UsageError(msg, hint=f"allowed values: {', '.join(allowed)}")
    return resolved


def _apply_detected(info: GuideInfo, resolved: _Resolved, detected: Detected) -> None:
    declared = {v.name: v for v in info.variables}
    accel_var = declared.get("ACCELERATOR_TYPE")
    if accel_var and "ACCELERATOR_TYPE" not in resolved.overrides and detected.accelerator in (accel_var.values or []):
        resolved.put("ACCELERATOR_TYPE", detected.accelerator or "", VariableSource.DETECTED)
    accel = resolved.overrides.get("ACCELERATOR_TYPE") or (accel_var.default if accel_var else None)
    row = info.support.row(accel) if info.support and accel else None
    if "INFRA_PROVIDER" in declared and "INFRA_PROVIDER" not in resolved.overrides and detected.provider:
        listed = row.providers if row else (declared["INFRA_PROVIDER"].values or [])
        if detected.provider in listed:
            resolved.put("INFRA_PROVIDER", detected.provider, VariableSource.DETECTED)
    if "MODEL" in declared and "MODEL" not in resolved.overrides and row and row.model and row.model != declared["MODEL"].default:
        resolved.put("MODEL", row.model, VariableSource.SUPPORT)


def _pick_mode(info: GuideInfo, requested: str | None) -> str | None:
    if requested is None:
        return "standalone" if "standalone" in info.modes else (info.modes[0] if info.modes else None)
    if requested not in info.modes:
        msg = f"{info.name} has no mode {requested!r}"
        raise UsageError(msg, hint=f"modes: {', '.join(info.modes) or 'none'}")
    return requested


def _is_empty(script: str) -> bool:
    body = [line.strip() for line in script.splitlines()]
    return not any(line and not line.startswith("#") and line != "set -euo pipefail" for line in body)


def _command(script: str) -> str:
    lines = script.splitlines()
    start = next((i + 1 for i, line in enumerate(lines) if line.strip() == "set -euo pipefail"), 0)
    body = [line for line in lines[start:] if not line.startswith("# === ")]
    return "\n".join(body).strip("\n")


class _Builder:
    def __init__(self, found: Catalog, entry: GuideEntry, choices: Choices, mode: str | None, overrides: dict[str, str]) -> None:
        self.guide_error: type[Exception] = found.module.GuideError
        self.entry = entry
        self.choices = choices
        self.mode = mode
        self.overrides = overrides
        self.modes = modes_of(entry.guide.data)
        self.manual = manual_options(entry.guide.data, self.modes)
        self.steps: list[Step] = []
        self.skipped: list[SkippedStep] = []
        self.tests_seen = False

    def emit(self, path: str) -> str:
        try:
            return self.entry.guide.emit([path], variables=self.overrides)
        except self.guide_error as exc:
            raise UsageError(str(exc)) from exc

    def option_choice(self, path: str) -> str:
        group = _base(path).split(".")[1:-1]
        return self.choices.options.get(".".join(group), DEFAULT_OPTION)

    def reason_to_skip(self, path: str, tags: list[StepTag], action: Action) -> str | None:
        leaf = _base(path).rsplit(".", 1)[-1]
        if StepTag.SKIPPED in tags:
            return "llmdup already holds the release"
        if StepTag.MODE in tags and leaf != self.mode:
            return f"mode {leaf} not chosen"
        if StepTag.MANUAL_OPTION in tags and leaf != self.option_choice(path):
            return f"option {leaf} not chosen (use --option {'.'.join(path.split('.')[1:-1])}={leaf})"
        if StepTag.SENDS_TRAFFIC in tags and action is Action.UP and self.tests_seen:
            return "only the first test runs after deploy"
        return None

    def add(self, path: str, tags: list[StepTag], action: Action) -> None:
        if reason := self.reason_to_skip(path, tags, action):
            self.skipped.append(SkippedStep(path=path, reason=reason))
            return
        script = self.emit(path)
        if _is_empty(script):
            self.skipped.append(SkippedStep(path=path, reason="no steps after when/skip_in filtering"))
            return
        if StepTag.SENDS_TRAFFIC in tags:
            self.tests_seen = True
        self.steps.append(Step(path=path, tags=tags, command=_command(script), script=script))


def build_plan(
    choices: Choices,
    *,
    action: Action = Action.UP,
    ref: str = DEFAULT_LLMD_REF,
    detected: Detected | None = None,
    verify: bool = True,
    keep_namespace: bool = False,
) -> Plan:
    """Turn a guide and the user's choices into an ordered plan of steps.

    Args:
        choices: Guide, accelerator, engine, mode and variables.
        action: Which sections to plan: deploy, test or clean up.
        ref: llm-d release tag.
        detected: Cluster facts used for choices the user left unset.
        verify: Include the smoke test after deploy.
        keep_namespace: For `down`, leave out the guide's namespace deletion.

    Returns:
        The plan. Nothing has run.

    Raises:
        UsageError: The local profile was asked for another guide.
    """
    if choices.profile is Profile.LOCAL and choices.guide != LOCAL_GUIDE:
        msg = f"the local profile deploys {LOCAL_GUIDE} only"
        raise UsageError(msg)
    if choices.profile is Profile.LOCAL and choices.model is None:
        choices = choices.model_copy(update={"model": SIM_MODEL})
    found = catalog(ref)
    entry = found.entry(choices.guide)
    info = found.info(choices.guide)
    mode = _pick_mode(info, choices.mode)
    resolved = resolve_variables(info, choices, detected)
    overrides = {**resolved.overrides, "REPO_ROOT": str(found.release.path)}
    builder = _Builder(found, entry, choices, mode, overrides)
    _check_options(builder.manual, choices.options)
    for path, node in step_lists(entry.guide.data, ACTION_SECTIONS[action]):
        tags = static_tags(path, node, builder.modes, builder.manual)
        if not verify and StepTag.SENDS_TRAFFIC in tags:
            builder.skipped.append(SkippedStep(path=path, reason="--no-verify"))
            continue
        if action is Action.DOWN:
            _add_cleanup(builder, path, node, tags, keep_namespace=keep_namespace)
            continue
        builder.add(path, tags, action)
    if choices.profile is Profile.LOCAL:
        _apply_local_profile(builder, action)
    if action is Action.UP and mode == "gateway":
        gateway_class = (detected or Detected()).gateways.get(f"{_namespace(info, resolved)}/{GATEWAY_NAME}", "")
        _apply_gateway_provider(builder, gateway_class)
    return _finish(found, info, choices, action, mode, resolved, builder)


def _check_options(manual: set[str], options: dict[str, str]) -> None:
    groups: dict[str, set[str]] = {}
    for path in manual:
        parts = path.split(".")
        groups.setdefault(".".join(parts[1:-1]), set()).add(parts[-1])
    for group, choice in options.items():
        if group not in groups:
            msg = f"no manual option group {group!r}"
            raise UsageError(msg, hint=f"groups: {', '.join(sorted(groups)) or 'none'}")
        if choice not in groups[group]:
            msg = f"option {group}={choice} is not one of the guide's choices"
            raise UsageError(msg, hint=f"choices: {', '.join(sorted(groups[group]))}")


def _add_cleanup(builder: _Builder, path: str, node: object, tags: list[StepTag], *, keep_namespace: bool) -> None:
    items = node if isinstance(node, list) else [node]
    for i, item in enumerate(items):
        item_path = f"{path}[{i}]" if isinstance(node, list) and len(node) > 1 else path
        if keep_namespace and _deletes_namespace(item):
            if _command_lines(item) > 1:
                msg = f"{item_path} deletes the namespace together with other commands, so --keep-namespace cannot skip it alone"
                raise UsageError(msg, hint="tear down without --keep-namespace")
            builder.skipped.append(SkippedStep(path=item_path, reason="--keep-namespace"))
        elif len(items) > 1:
            builder.add(item_path, tags, Action.DOWN)
    if len(items) == 1 and not (keep_namespace and _deletes_namespace(items[0])):
        builder.add(path, tags, Action.DOWN)


def _command_lines(step: object) -> int:
    run = str(step.get("run", "")) if isinstance(step, dict) else ""
    return sum(1 for line in run.splitlines() if line.strip() and not line.strip().startswith("#") and not line.rstrip().endswith("\\"))


def _base(path: str) -> str:
    return INDEX_SUFFIX.sub("", path)


def _deletes_namespace(step: object) -> bool:
    # ponytail: matches the upstream command text; add a guide.yaml tag upstream if this ever misfires
    return isinstance(step, dict) and bool(DELETE_NAMESPACE.search(str(step.get("run", ""))))


def _local_step(path: str, command: str, tag: StepTag = StepTag.LOCAL) -> Step:
    script = f"#!/usr/bin/env bash\n# added by llmdup: {tag}\nset -euo pipefail\n\n{command}\n"
    return Step(path=path, tags=[tag], command=command, script=script)


def _apply_gateway_provider(builder: _Builder, gateway_class: str) -> None:
    """On a GKE Gateway, switch the router chart to `provider.name=gke`, as llm-d's GKE gateway doc does.

    The guide's gateway step pins `GW_PROVIDER_NAME=none`, whose HTTPRoute sets `timeouts`; GKE rejects that route.
    """
    if not gateway_class.startswith(GKE_GATEWAY_CLASS):
        return
    step = _local_step(
        "gateway.provider",
        'helm upgrade "${GUIDE_NAME}" "${ROUTER_GATEWAY_CHART}" --reuse-values --set provider.name=gke '
        '-n "${NAMESPACE}" --version "${ROUTER_CHART_VERSION}"',
        StepTag.GATEWAY_PROVIDER,
    )
    at = next((i for i, s in enumerate(builder.steps) if s.path == "deploy.gateway"), None)
    if at is not None:
        builder.steps.insert(at + 1, step)


def _namespace(info: GuideInfo, resolved: _Resolved) -> str | None:
    return resolved.overrides.get("NAMESPACE") or next((v.default for v in info.variables if v.name == "NAMESPACE"), None)


def _apply_local_profile(builder: _Builder, action: Action) -> None:
    overlay = shlex.quote(str(LOCAL_PROFILE / "sim-overlay"))
    values = shlex.quote(str(LOCAL_PROFILE / "router-small.values.yaml"))
    replaced = {
        "prerequisites.secrets": None,
        "deploy.modelserver.standard": _local_step("local.simulator", f'kubectl apply -n "${{NAMESPACE}}" -k {overlay}'),
        "cleanup.modelserver.standard": _local_step(
            "local.simulator",
            f'kubectl delete -n "${{NAMESPACE}}" -k {overlay} --ignore-not-found',
        ),
    }
    charts = {"deploy.standalone": "ROUTER_STANDALONE_CHART", "deploy.gateway": "ROUTER_GATEWAY_CHART"}
    steps: list[Step] = []
    for step in builder.steps:
        if step.path in replaced:
            reason = "the simulator replaces the model servers" if replaced[step.path] else "the simulator needs no Hugging Face token"
            builder.skipped.append(SkippedStep(path=step.path, reason=f"local profile: {reason}"))
            if (local := replaced[step.path]) is not None:
                steps.append(local)
            continue
        steps.append(step)
        if action is Action.UP and step.path in charts:
            steps.append(
                _local_step(
                    "local.router_small",
                    f'helm upgrade "${{GUIDE_NAME}}" "${{{charts[step.path]}}}" --reuse-values '
                    f'-f {values} -n "${{NAMESPACE}}" --version "${{ROUTER_CHART_VERSION}}"',
                ),
            )
    builder.steps = steps


def _finish(
    found: Catalog,
    info: GuideInfo,
    choices: Choices,
    action: Action,
    mode: str | None,
    resolved: _Resolved,
    builder: _Builder,
) -> Plan:
    variables = [
        PlanVariable(
            name=v.name,
            value=None if v.sensitive else resolved.overrides.get(v.name, v.default),
            source=VariableSource.ENVIRONMENT if v.sensitive else resolved.sources.get(v.name, VariableSource.DEFAULT),
            values=v.values,
            sensitive=v.sensitive,
        )
        for v in info.variables
        if v.name != "REPO_ROOT"
    ]
    namespace = _namespace(info, resolved)
    if action is Action.DOWN:
        confirm = [s.path for s in builder.steps]
    else:
        confirm = [s.path for s in builder.steps if StepTag.CLUSTER_SCOPED in s.tags]
    return Plan(
        guide=info.name,
        action=action,
        profile=choices.profile,
        llmd_ref=found.release.ref,
        commit=found.release.commit,
        mode=mode,
        namespace=namespace or "default",
        options=choices.options,
        overrides=resolved.overrides,
        variables=variables,
        env_script=builder.emit("env"),
        steps=builder.steps,
        skipped=builder.skipped,
        needs_confirmation=confirm,
    )


def secret_status(name: str = "HF_TOKEN") -> str:
    """Return `set` or `not set` for a secret in the environment, never its value."""
    return "set" if os.environ.get(name) else "not set"
