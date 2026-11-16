# Copyright 2026 The llmdup Authors
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import cache, cached_property
from pathlib import Path
from types import ModuleType
from typing import Protocol

import yaml

from llmdup.core.models import (
    AcceleratorSupport,
    GuideInfo,
    GuideSummary,
    StepPath,
    StepTag,
    Support,
    UsageError,
    Variable,
)
from llmdup.core.upstream import DEFAULT_LLMD_REF, Release, release

SECTIONS = ("prerequisites", "deploy", "verify", "cleanup")
NAMESPACED_PREREQUISITES = frozenset({"namespace", "secrets"})
INDEX_FILE = Path("docs/well-lit-paths/guides.yaml")
UNLISTED = "unlisted"


class UpstreamGuide(Protocol):
    """The parts of upstream `guide.Guide` llmdup uses."""

    data: dict[str, object]

    def emit(self, sections: Iterable[str], *, variables: dict[str, str] | None = None) -> str:
        """Return a bash script for `sections`."""
        ...


@dataclass(frozen=True)
class GuideEntry:
    """Where a guide lives in the release and how the index lists it."""

    name: str
    dir: str
    title: str
    section: str
    guide: UpstreamGuide


def step_lists(data: dict[str, object], sections: Iterable[str] = SECTIONS) -> Iterator[tuple[str, object]]:
    """Yield every step-list path of a guide in guide order.

    Args:
        data: The parsed `guide.yaml`.
        sections: Top-level sections to walk.

    Yields:
        `(path, node)` for each list of steps, or single step, under the sections.
    """
    for section in sections:
        if section in data:
            yield from _walk(data[section], section)


def _walk(node: object, path: str) -> Iterator[tuple[str, object]]:
    if isinstance(node, list) or (isinstance(node, dict) and "run" in node):
        yield path, node
    elif isinstance(node, dict):
        for key, child in node.items():
            yield from _walk(child, f"{path}.{key}")


def flatten(node: object) -> list[dict[str, object]]:
    """Return the step maps of a step-list node."""
    if isinstance(node, dict) and "run" in node:
        return [node]
    if isinstance(node, list):
        return [step for item in node for step in flatten(item)]
    if isinstance(node, dict):
        return [step for child in node.values() for step in flatten(child)]
    return []


def modes_of(data: dict[str, object]) -> list[str]:
    """Return the endpoint modes, read from `verify.endpoint`, then `benchmark.endpoint`."""
    for section in ("verify", "benchmark"):
        match data.get(section):
            case {"endpoint": dict(endpoint)}:
                return [str(mode) for mode in endpoint]
    return []


def static_tags(path: str, node: object, modes: list[str]) -> list[StepTag]:
    """Classify a step-list path the way the upstream deploy-llm-d skill does.

    Args:
        path: Dotted step path, such as `deploy.modelserver.standard`.
        node: The step list at that path.
        modes: The guide's endpoint modes.

    Returns:
        The tags that apply to the path.
    """
    parts = path.split(".")
    tags: list[StepTag] = []
    if path == "prerequisites.clone":
        tags.append(StepTag.SKIPPED)
    elif parts[0] == "prerequisites" and parts[-1] not in NAMESPACED_PREREQUISITES:
        tags.append(StepTag.CLUSTER_SCOPED)
    if parts[:2] == ["verify", "tests"]:
        tags.append(StepTag.SENDS_TRAFFIC)
    elif len(parts) >= 2 and parts[-1] in modes:
        tags.append(StepTag.MODE)
    elif len(parts) >= 3 and not all(step.get("when") for step in flatten(node)):
        tags.append(StepTag.MANUAL_OPTION)
    return tags


def _variables(data: dict[str, object]) -> list[Variable]:
    match data.get("env"):
        case {"static": dict(static)}:
            pass
        case _:
            return []
    out: list[Variable] = []
    for name, spec in static.items():
        match spec:
            case dict():
                values = spec.get("values")
                out.append(
                    Variable(
                        name=str(name),
                        default=None if spec.get("default") is None else str(spec["default"]),
                        values=[str(v) for v in values] if isinstance(values, list) else None,
                        sensitive=spec.get("sensitive") is True,
                        comment=None if spec.get("comment") is None else str(spec["comment"]),
                    ),
                )
            case _:
                out.append(Variable(name=str(name), default=str(spec)))
    return out


def _cell(cell: object) -> str:
    match cell:
        case str():
            return cell
        case {"status": status}:
            return str(status)
    return "unsupported"


def _support(data: dict[str, object]) -> Support | None:
    match data.get("support"):
        case {"accelerators": dict(accelerators), **rest}:
            pass
        case _:
            return None
    engines = rest.get("engines")
    rows = []
    for name, raw in accelerators.items():
        spec: dict[str, object] = raw if isinstance(raw, dict) else {}
        cells = spec.get("engines")
        cells = cells if isinstance(cells, dict) else {}
        providers = spec.get("providers")
        providers = providers if isinstance(providers, list) else []
        rows.append(
            AcceleratorSupport(
                name=str(name),
                label=_str(spec.get("label")),
                model=_str(spec.get("model")),
                notes=_str(spec.get("notes")),
                engines={str(k): _cell(v) for k, v in cells.items()},
                providers=[str(p["name"]) if isinstance(p, dict) else str(p) for p in providers],
            ),
        )
    labels = engines if isinstance(engines, dict) else {}
    return Support(engines={str(k): str(v) for k, v in labels.items()}, accelerators=rows)


def _str(value: object) -> str | None:
    return None if value is None else str(value)


class Catalog:
    """The guides of one llm-d release, read through the release's own `scripts/guide.py`."""

    def __init__(self, found: Release) -> None:
        self.release = found

    @cached_property
    def module(self) -> ModuleType:
        """The release's `scripts/guide.py`, imported as a module.

        Raises:
            UsageError: The file cannot be imported.
        """
        path = self.release.path / "scripts" / "guide.py"
        name = f"llmdup_upstream_guide_{abs(hash(path))}"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            msg = f"cannot import {path}"
            raise UsageError(msg)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    @cached_property
    def entries(self) -> dict[str, GuideEntry]:
        """Every guide with a `guide.yaml`, keyed by its name."""
        listed = self._index()
        out: dict[str, GuideEntry] = {}
        for path in sorted((self.release.path / "guides").rglob("guide.yaml")):
            guide: UpstreamGuide = self.module.Guide.load(path.parent)
            rel = path.parent.relative_to(self.release.path).as_posix()
            name = str(guide.data.get("name") or path.parent.name)
            section, title = listed.get(rel, (UNLISTED, _readme_title(path.parent) or name))
            out[name] = GuideEntry(name=name, dir=rel, title=title, section=section, guide=guide)
        return out

    def _index(self) -> dict[str, tuple[str, str]]:
        data = yaml.safe_load((self.release.path / INDEX_FILE).read_text())
        out: dict[str, tuple[str, str]] = {}
        for section, spec in (data.get("sections") or {}).items():
            for entry in spec.get("guides") or []:
                out[entry["dir"]] = (section, entry.get("title") or entry["dir"])
                for page in entry.get("pages") or []:
                    child = (Path(entry["dir"]) / page["from"]).parent.as_posix()
                    out.setdefault(child, (section, page.get("title") or child))
        return out

    def entry(self, name: str) -> GuideEntry:
        """Return one guide by name.

        Raises:
            UsageError: No guide has that name.
        """
        if name not in self.entries:
            msg = f"unknown guide {name!r}"
            raise UsageError(msg, hint=f"guides: {', '.join(sorted(self.entries))}")
        return self.entries[name]

    def info(self, name: str) -> GuideInfo:
        """Return the variables, modes, support matrix and step paths of a guide."""
        entry = self.entry(name)
        data = entry.guide.data
        modes = modes_of(data)
        return GuideInfo(
            name=entry.name,
            title=entry.title,
            section=entry.section,
            dir=entry.dir,
            modes=modes,
            variables=_variables(data),
            support=_support(data),
            steps=[StepPath(path=p, tags=static_tags(p, node, modes)) for p, node in step_lists(data)],
        )

    def summaries(self, accelerator: str | None = None, engine: str | None = None) -> list[GuideSummary]:
        """Return one row per guide, with support for the given or default accelerator and engine."""
        rows = []
        for name in self.entries:
            info = self.info(name)
            defaults = {v.name: v.default for v in info.variables}
            accel = accelerator or defaults.get("ACCELERATOR_TYPE")
            eng = engine or defaults.get("MODEL_SERVER")
            support = info.support.status(accel, eng) if info.support and accel and eng else None
            rows.append(
                GuideSummary(
                    name=name,
                    title=info.title,
                    section=info.section,
                    modes=info.modes,
                    accelerator=accel if info.support else None,
                    engine=eng if info.support else None,
                    support=support,
                ),
            )
        order = {s: i for i, s in enumerate(self._index_sections())}
        return sorted(rows, key=lambda r: order.get(r.section, len(order)))

    def _index_sections(self) -> list[str]:
        data = yaml.safe_load((self.release.path / INDEX_FILE).read_text())
        return list(data.get("sections") or {})


def _readme_title(directory: Path) -> str | None:
    readme = directory / "README.md"
    if not readme.is_file():
        return None
    for line in readme.read_text().splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


@cache
def _catalog(found: Release) -> Catalog:
    return Catalog(found)


def catalog(ref: str = DEFAULT_LLMD_REF) -> Catalog:
    """Return the guide catalog of an llm-d release."""
    return _catalog(release(ref))
