# llmdup: rules for Claude Code

Source: PLAN.md (16 Nov 2026). The "Rules for Claude Code" section, its Python conventions and the repo layout are copied here verbatim.

## Repo layout

```text
llmdup/
├── pyproject.toml              # uv project, entry point: llmdup
├── CLAUDE.md                   # rules for Claude Code, copied from this plan
├── PROGRESS.md                 # phase checklist, updated after every phase
├── src/llmdup/
│   ├── core/
│   │   ├── models.py           # Pydantic models shared by every front end
│   │   ├── upstream.py         # fetch and cache the pinned llm-d release
│   │   ├── guides.py           # guide index, guide model, support matrix
│   │   ├── planner.py          # guide + choices -> ordered Plan of steps
│   │   ├── detect.py           # context, version, accelerators, CRDs
│   │   ├── doctor.py           # preflight checks with fix hints
│   │   ├── runner.py           # run a Plan step by step, yield events
│   │   ├── state.py            # ConfigMap llmdup-state in the namespace
│   │   ├── status.py           # release, pods, endpoint, readiness
│   │   ├── local.py            # kind cluster + simulator profile
│   │   └── kube.py             # the only module that starts kubectl, helm, kind, bash
│   ├── local_profile/          # the only manifests llmdup owns
│   │   ├── sim-overlay/        # llm-d-inference-sim Deployment (kustomize)
│   │   └── router-small.values.yaml
│   ├── cli.py                  # Typer commands, --json output
│   ├── tui/                    # Textual app: app.py, keys.py, screens/, widgets/
│   └── mcp_server.py           # MCP tools over the same core
├── skills/llmdup/SKILL.md      # agent skill that calls the CLI
└── tests/
    ├── fixtures/llm-d-v1.0.0/  # trimmed copy of upstream files, Apache-2.0
    ├── unit/
    ├── tui/                    # Textual pilot and snapshot tests
    └── e2e/                    # kind + simulator
```

## Rules for Claude Code

These rules hold in every phase. Copy this whole section into `CLAUDE.md`.

1. Work one phase at a time. Pass its "Done when" checks, commit, update `PROGRESS.md`, then stop for review.
2. Never copy commands, Helm values or manifests from llm-d guides into this repo. Get them from `guide.py` at the pinned release. The only exceptions are `local_profile/` and `tests/fixtures/`.
3. Use only the public names of `guide.py`: its `__all__` list and the `Guide` class. If something you need is private, compute it from `Guide.data`.
4. Put every operation in `core/` with Pydantic inputs and outputs. `cli.py`, `tui/` and `mcp_server.py` only collect input and format output.
5. Only `core/kube.py` may start a subprocess.
6. During development, touch only kube contexts whose name begins with `kind-llmdup`. Never run against another context, and never create cloud resources.
7. Ask before any cluster-scoped step and before any delete: `--yes` in the CLI, a dialog in the UI, `confirm: true` in MCP. Always show the exact command.
8. Never write the `HF_TOKEN` value to disk, logs, state, JSON output, snapshots or the screen.
9. Keep `--json` output stable: one document on stdout, and bump `schema_version` on any breaking change.
10. Read the current docs of Textual, Typer and the MCP Python SDK before using them. Do not rely on memory for their APIs.
11. If a "Done when" check cannot pass after two different approaches, stop and report. Do not weaken or delete the check.
12. List every file copied from llm-d or llm-d-skills in `NOTICE`. Both are Apache-2.0.
13. If this plan and the upstream llm-d repo disagree, the upstream repo wins. Say what differs.

### Python conventions

- Target Python 3.14. Add no fallbacks for older versions and no `from __future__ import annotations`.
- Handle runner events and check results with `functools.singledispatch`. Each front end has one `render(event)` function with an implementation registered per event class. The base implementation raises `NotImplementedError`, so a new event type fails the tests until every front end handles it. Use `singledispatchmethod` inside classes.
- Use `functools.cache` and `cached_property` for the unpacked release and the guide index.
- Use `match` to read `kubectl` JSON, `StrEnum` for statuses and step tags, and the `type` statement and bracket syntax for aliases and generics.
- Run subprocesses with `asyncio`, using `asyncio.TaskGroup` and `asyncio.timeout`. The runner is an async generator of events.
- Unpack the release with `tarfile` and its `data` filter.
- Import Textual and the MCP SDK inside the commands that need them, so `llmdup plan --json` starts fast.
- Free threading: the code must pass on the free-threaded build and must not require it. Never rely on the GIL for safety; threads share state only through queues or locks.

### Local additions

- Type checker: `ty` (not mypy). Lint: `uv run ruff check`. Types: `uv run ty check`. Tests: `uv run pytest`.
- Development kubeconfig is isolated: export `KUBECONFIG` to a scratch file before any e2e run. The default kubeconfig on the dev machine holds production contexts; never run llmdup or the e2e tests against it.
- Local test cluster: `KIND_EXPERIMENTAL_PROVIDER=podman` on the rootful podman machine `llmdup`.
