# Progress

Phase checklist from PLAN.md. Updated after every phase.

## Phase 0: Scaffold
- [x] Create the repo layout above as a `uv` project with a `src/` layout and the entry point `llmdup = llmdup.cli:app`
- [x] Add Typer, Pydantic, PyYAML, Textual and the MCP Python SDK; for development add pytest, ruff, a type checker and Textual's snapshot plugin
- [x] Implement `llmdup version` with `--json`
- [x] Set `requires-python` to 3.14 or newer. Add GitHub Actions for lint, types and tests on Python 3.14, on 3.15, and on the free-threaded build 3.14t
- [x] Add `LICENSE` (Apache-2.0), `NOTICE`, a README stub, `CLAUDE.md` and `PROGRESS.md`

Done when:

- [x] `uv run llmdup version --json` prints the JSON envelope.
- [x] Lint, type check and `pytest` pass locally (3.14, 3.14t, 3.15.0rc2). CI: workflow added; not run yet because the repo has no GitHub remote.

Notes: type checker is `ty` (user request) instead of mypy. Typer cannot read PEP 695 `type` aliases, so CLI option aliases are plain `Annotated` assignments.

## Phase 1: Guides and plan, no cluster
- [x] `upstream.py`: download `https://codeload.github.com/llm-d/llm-d/tar.gz/refs/tags/<ref>` into the cache, unpack it safely, and record the ref and commit
- [x] Honour `LLMDUP_LLMD_PATH` to use a local llm-d checkout instead of downloading
- [x] Commit a trimmed fixture under `tests/fixtures/llm-d-v1.0.0/`: `scripts/guide.py`, `guides/env.sh`, `docs/well-lit-paths/guides.yaml`, `guides/recipes/`, and the guides `optimized-baseline`, `flow-control` and `pd-disaggregation`
- [x] `guides.py`: import the checkout's `guide.py`, then build a `GuideInfo` with name, title, section, modes, variables, allowed values and the support matrix
- [x] `planner.py`: build a `Plan` of ordered `Step`s, each with its path, tags and emitted script; drop steps whose script is empty after filtering
- [x] CLI: `guides list`, `guides show` and `plan`, with `--json` and `--script`

Done when:

- [x] A network-marked test loads the real `v1.0.0` release and builds a default plan for all 27 guides without an error.
- [x] `llmdup plan optimized-baseline --accelerator gpu --json` lists exactly these steps in order: `prerequisites.gaie`, `prerequisites.namespace`, `prerequisites.secrets`, `deploy.router_values`, `deploy.standalone`, `deploy.modelserver.standard`, `verify.endpoint.standalone`, `verify.tests.request`.
- [x] `llmdup plan optimized-baseline --accelerator bogus` exits with code 2 and prints the allowed values.
- [x] The output of `plan --script` passes `bash -n`.

Notes, where upstream differs from PLAN.md:

- `discover_modes` and `support_status` are not in `guide.py`'s `__all__` (it exports `Finding`, `Findings`, `Guide`, `GuideError`, `emit_script`, `parse_guide_yaml`), so modes and support are computed from `Guide.data` (rule 3).
- `turn-priority-fairness` and `workload-autoscaling/wva` have a `guide.yaml` but are not in `docs/well-lit-paths/guides.yaml`; they show under section `unlisted`.
- Each step is emitted on its own with `Guide.emit([path])`; the `env` section is emitted once and runs first, so exports from one step (for example `ROUTER_VALUES`) carry into later steps.
- Manual options are picked with `--option GROUP=CHOICE`, for example `--option modelserver=dynamic_slice`; `standard` is the default.
- `--set` refuses sensitive variables (such as `HF_TOKEN`); they come from the environment only.

## Phase 2: Detect and doctor
- [x] `kube.py`: one wrapper that runs `kubectl`, `helm`, `kind` and `bash` with a timeout and captured output, plus a fake for tests
- [x] `detect.py`: context, server version, installed CRDs, existing Helm releases, provider (`gke` when nodes carry `cloud.google.com/gke-nodepool`)
- [x] `detect.py`: map allocatable resources and DeviceClasses to `ACCELERATOR_TYPE`, using the table in step 3 of the upstream `deploy-llm-d` skill
- [x] `doctor.py`: the checks in the table below, each returning ok, warn or fail with `found` and `fix`
- [x] CLI: `doctor`, exit code 3 when any check fails

Done when:

- [x] Unit tests with recorded `kubectl -o json` output cover a GPU cluster, a CPU-only cluster and an unreachable cluster.
- [x] `llmdup doctor optimized-baseline --json` on a fresh kind cluster reports the CRD warning and no accelerator.

Notes:

- The CPU-only and unreachable fixtures are recorded from a real kind cluster (Kubernetes v1.37.0, podman). No GPU cluster was available to record, so the GPU fixture is that recording with `nvidia.com/gpu` allocatable, GKE node labels, the InferencePool CRD, a Helm release and a GatewayClass added; its `kubectl kustomize` output is a real rendering of the v1.0.0 `gpu/vllm/base` overlay.
- `llmdup doctor optimized-baseline` on the fresh kind cluster: CRD warning, "no accelerator detected" warning, capacity warning (4 `nvidia.com/gpu` wanted, 0 allocatable), HF_TOKEN fail, exit 3.
- Doctor adds an `Accelerators` check (the plan's "no accelerator" report) and accepts the same choice flags as `plan`.
- Bash steps reach the pinned context through exported `kubectl`/`helm` shell functions that add `--context`/`--kube-context`, so upstream commands run unchanged.

## Phase 3: Local profile, up, status, down
- [x] `local.py`: `local up` creates a kind cluster named `llmdup` when it is missing; `local down` deletes it
- [x] `local_profile/sim-overlay`: two replicas of `ghcr.io/llm-d/llm-d-inference-sim`, labelled `llm-d.ai/guide: optimized-baseline`; read the simulator's README and `manifests/deployment.yaml` for its flags and current tag
- [x] `local_profile/router-small.values.yaml`: lower the router's CPU and memory requests so it fits a laptop
- [x] Local plan: the `optimized-baseline` plan in standalone mode, with the model server step replaced by the simulator overlay and the small values applied after the router install
- [x] `runner.py`: run steps, yield events, wait for readiness, stop at the first failure and report the failing command with its last 40 log lines
- [x] `state.py` and `status.py`, then CLI `up`, `status`, `test` and `down`
- [x] Confirmation for cluster-scoped steps and for `down`

Done when:

- [x] `llmdup local up --yes` ends with an HTTP 200 from `/v1/completions` through the router.
- [x] `llmdup status --json` shows the router and two simulator pods ready, and the endpoint.
- [x] After `llmdup down --yes`, `llmdup status` shows nothing deployed.
- [x] After a step is killed mid-run, `status` reports a partial deploy and `down` cleans it up.
- [ ] The end-to-end job passes in CI. Passes locally (`pytest -m e2e`, 5 tests, kind v1.37.0 on rootful Podman, 1m53s); the CI job is written but has not run because the repo has no GitHub remote.

Notes:

- Upstream differs: `cleanup.rest` in optimized-baseline runs `kubectl delete -k guides/recipes/modelserver/components/monitoring --ignore-not-found=true`, which fails on any cluster without the Prometheus Operator CRDs (`no matches for kind "PodMonitor"`), and because the three commands share one step the namespace delete never ran. Teardown now runs each item of a cleanup step list as its own step (`cleanup.rest[0]`, ...), keeps going past failures, and then checks what is left. The state ConfigMap is removed only when nothing is left.
- Local profile: `prerequisites.secrets` is dropped (the simulator needs no Hugging Face token); `deploy.modelserver.standard` becomes `local.simulator`; `local.router_small` runs `helm upgrade --reuse-values -f router-small.values.yaml` right after the router install.
- Simulator: `ghcr.io/llm-d/llm-d-inference-sim:v1.2.0` (latest release; the repo's `manifests/deployment.yaml` still says v0.11.2 and adds a vLLM render sidecar, which llmdup leaves out). `--max-model-len 32768`, because the default 1024 made `verify.tests.shared_prefix` return HTTP 400.
- `up` runs the doctor checks first and exits 3 if any fails. `--dry-run` prints the script without gating.
- Steps run in separate bash processes; after each step its exported environment and working directory are captured (base64 `env -0` on stdout after a marker, never written to disk) and passed to the next step. Lines containing the `HF_TOKEN` value are redacted.

## Phase 4: Terminal UI
- [ ] Read the current Textual docs for screens, tables, inputs, logs, workers and testing before writing code
- [ ] `tui/keys.py`, the header with generated hints, the breadcrumb line, and the prompt with command and filter modes, suggestions and history
- [ ] The seven views in the Terminal UI section
- [ ] View history, filtering and sorting as listed under "Keys in every view"
- [ ] The confirm dialog, and a worker that turns runner events into step progress and log lines
- [ ] A fake core for tests, so UI tests need no cluster

Done when:

- [ ] Pilot tests open every view by key and by command, and typing `/cache` leaves only rows that contain `cache`.
- [ ] A test asserts that every binding in `keys.py` appears in the help view.
- [ ] Snapshot tests cover all seven views at 120 by 36, and the guides view at 100 by 30.
- [ ] With the local profile, a person can plan, deploy, open the stack, send a test request and tear down without leaving the UI.

## Phase 5: MCP server and skill
- [ ] `mcp_server.py` with the tools `list_guides`, `show_guide`, `doctor`, `plan`, `up`, `status`, `test` and `down`
- [ ] `up` and `down` require `confirm: true`; without it they return the plan and what needs confirmation
- [ ] Tool results are the same Pydantic models as the CLI's `data` field
- [ ] `skills/llmdup/SKILL.md`: when to use llmdup, the commands, the exit codes, and the rule to tell the user what will be created before passing `--yes`

Done when:

- [ ] An MCP client test lists the eight tools and calls `plan`.
- [ ] Calling `up` without `confirm` changes nothing in the cluster.

## Phase 6: Real clusters and release
- [ ] Gateway mode, and every accelerator and engine the guide's matrix allows
- [ ] README: install, a local demo, screenshots of the UI, and the command reference
- [ ] Publish 0.1.0 to PyPI after the name check

Done when:

