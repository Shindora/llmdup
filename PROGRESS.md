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
- [x] Lint, type check and `pytest` pass locally (3.14, 3.14t, 3.15.0rc2) and in CI (GitHub Actions run 43902140537: lint, 3.14, 3.15, 3.14t).

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
- [x] The end-to-end job passes in CI (run 43902140537), and locally (`pytest -m e2e`, kind v1.37.0 on rootful Podman). The first CI run showed that `kubectl run --rm -i` can drop the curl output of the guide's smoke test, so the e2e test reads step results from `--json` and proves the HTTP 200 through a port-forward.

Notes:

- Upstream differs: `cleanup.rest` in optimized-baseline runs `kubectl delete -k guides/recipes/modelserver/components/monitoring --ignore-not-found=true`, which fails on any cluster without the Prometheus Operator CRDs (`no matches for kind "PodMonitor"`), and because the three commands share one step the namespace delete never ran. Teardown now runs each item of a cleanup step list as its own step (`cleanup.rest[0]`, ...), keeps going past failures, and then checks what is left. The state ConfigMap is removed only when nothing is left.
- Local profile: `prerequisites.secrets` is dropped (the simulator needs no Hugging Face token); `deploy.modelserver.standard` becomes `local.simulator`; `local.router_small` runs `helm upgrade --reuse-values -f router-small.values.yaml` right after the router install.
- Simulator: `ghcr.io/llm-d/llm-d-inference-sim:v1.2.0` (latest release; the repo's `manifests/deployment.yaml` still says v0.11.2 and adds a vLLM render sidecar, which llmdup leaves out). `--max-model-len 32768`, because the default 1024 made `verify.tests.shared_prefix` return HTTP 400.
- `up` runs the doctor checks first and exits 3 if any fails. `--dry-run` prints the script without gating.
- Steps run in separate bash processes; after each step its exported environment and working directory are captured (base64 `env -0` on stdout after a marker, never written to disk) and passed to the next step. Lines containing the `HF_TOKEN` value are redacted.

## Phase 4: Terminal UI
- [x] Read the current Textual docs for screens, tables, inputs, logs, workers and testing before writing code (Textual 8.2.8 source: App, Screen, ModalScreen, Binding, DataTable, Input and Suggester, RichLog, workers, Pilot, pytest-textual-snapshot 1.0.0)
- [x] `tui/keys.py`, the header with generated hints, the breadcrumb line, and the prompt with command and filter modes, suggestions and history
- [x] The seven views in the Terminal UI section
- [x] View history, filtering and sorting as listed under "Keys in every view"
- [x] The confirm dialog, and a worker that turns runner events into step progress and log lines
- [x] A fake core for tests, so UI tests need no cluster

Done when:

- [x] Pilot tests open every view by key and by command, and typing `/cache` leaves only rows that contain `cache`.
- [x] A test asserts that every binding in `keys.py` appears in the help view.
- [x] Snapshot tests cover all seven views at 120 by 36, and the guides view at 100 by 30.
- [x] With the local profile, a person can plan, deploy, open the stack, send a test request and tear down without leaving the UI. `tests/tui/test_e2e.py` drives the real app with Pilot on `kind-llmdup` (kind v1.37.0, rootful Podman): `:plan optimized-baseline`, `u`, `y` on the confirm dialog, `:stacks`, `enter`, `t` (a `HTTP 200` line in the run log), `ctrl-d`, `y`; it then checks `status` shows nothing deployed. Passed in 2m03s; it refuses to run unless the current context starts with `kind-llmdup`.

Notes, where the UI differs from PLAN.md or adds to it:

- Every view opens by the key and the command the Views table gives it. `deploy` and `help` have no command there, so `:deploy [guide]` (the deploy view, or deploy a guide) and `:help` were added; `stacks` has no key of its own, and `guides` is the start view (`[`, `-` and `esc` go back to it).
- The stack view (pods, endpoint, last test) is the stacks view with a namespace: `enter` on a stack. It shares the stacks keys and has its own snapshot, as do the filter prompt and the pod log.
- On the kind cluster `kind-llmdup`, planning or deploying optimized-baseline uses the local profile, as `llmdup local up` does. Elsewhere a guide starts from its defaults plus detection.
- optimized-baseline does not declare `HF_TOKEN` in `env.static`, so the plan view adds an `HF_TOKEN` row (`set` or `not set`, never the value) whenever the plan creates the secret; it cannot be edited.
- Confirmation is the same for every run: whenever `Prepared.confirm` is not empty (cluster-scoped deploy steps, every tear down, and tests that need it) a dialog shows the exact commands; `n` or `esc` runs nothing. A doctor failure during `u` opens the doctor view instead.
- A `status()` error is shown on the breadcrumb line; it is never taken as "nothing deployed" (the guides view shows `-` in DEPLOYED then).
- `shift-f` copies the exact `kubectl port-forward` command from `StackStatus.port_forward`; it does not start it. `m` reads model-server metrics through the API server proxy on port 8000, as the guide's own `verify.tests.pod_metrics` step does.
- Sort by age exists only where there is an age: the stacks view (state ConfigMap `updated_at`). Sort by status uses support (guides), check status (doctor), health (stacks).
- `ctrl-a` lists the `:` commands in a dialog; Textual's own command palette is off. Dialog keys (`y`, `n`/`esc`, `c`) are declared in `keys.py` and listed in help.
- `ctrl-s` saves `llmdup-<guide>.sh` (plan) or `llmdup-<pod>.log` (logs) in the current directory.
- At 100 columns a few header hints end in `…`; the help view uses as many columns (up to four) as fit without cutting text and scrolls.
- `llmdup` with no arguments opens the UI when stdin and stdout are both terminals, else prints help.
- Core additions, read-only: `status.pod_log`, `status.model_metrics`, `status.contexts`, and the models `PodLog`, `PodMetrics`, `MetricsReport`.
- Snapshots are SVG files that syrupy 7 stores as `tests/tui/__snapshots__/test_snapshots/*.raw`; a test checks that none holds a token.
- Built by an Opus worker on branch `llmdup-tui`, merged into `main` after the review fixes. On the merged tree: ruff, ty, 127 offline tests (10 snapshots) and all 6 e2e tests (5 CLI, 1 UI) pass on kind-llmdup.

## Phase 5: MCP server and skill
- [x] `mcp_server.py` with the tools `list_guides`, `show_guide`, `doctor`, `plan`, `up`, `status`, `test` and `down`
- [x] `up` and `down` require `confirm: true`; without it they return the plan and what needs confirmation
- [x] Tool results are the same Pydantic models as the CLI's `data` field
- [x] `skills/llmdup/SKILL.md`: when to use llmdup, the commands, the exit codes, and the rule to tell the user what will be created before passing `--yes`

Done when:

- [x] An MCP client test lists the eight tools and calls `plan`.
- [x] Calling `up` without `confirm` changes nothing in the cluster.

Notes:

- MCP Python SDK 2.3.0: `FastMCP` is now `mcp.server.MCPServer`; tests use the in-memory `Client(server)` and a stdio `Client(StdioServerParameters(command="llmdup", args=["mcp"]))`.
- The SDK deprecates the logging capability (SEP-2577), so run events reach the client as progress notifications, one message per event via the MCP `render`.
- Only `ToolError` text reaches the agent in SDK 2.x; llmdup errors are converted to it, with the hint.
- `up` and `down` return `Prepared | RunFinished`, which the SDK wraps as `{"result": ...}`. In MCP, `up` needs `confirm: true` even when no step is cluster-scoped, because it creates resources; without it `confirm` lists every step's command.
- "Calling `up` without `confirm` changes nothing" is tested with a recording fake: every command it ran is a read (`kubectl version/get/config/kustomize`, `helm list`), and no bash step ran.

## Review fixes (Opus review of Phases 0-3 and 5)

An Opus reviewer read Phases 0-3 and 5 and reported 4 high, 8 medium and 11 low findings. All were fixed:

- Steps whose commands create or delete cluster-scoped objects (`clusterrole`, CRDs) are tagged cluster-scoped wherever they sit, not only under `prerequisites`. That catches `verify.tests.metrics_rbac` (flow-control, turn-priority-fairness, precise-prefix-cache-routing) and the FMA deploy steps. `llmdup test` needs `--yes` for them; MCP `test` returns a preview without `confirm: true`.
- The kube context is resolved once per command and pinned (`--context` on every kubectl, helm and bash step), so switching context mid-run cannot redirect it. MCP `up`, `down` and `test` with `confirm: true` require `kube_context`, which the preview returns as `context`.
- Teardown treats the namespace as gone only on NotFound. Any other lookup failure, or a failed Helm or pod query, counts as left behind, and the state is kept.
- A failed state query is an error, not "nothing deployed".
- `--keep-namespace` works for single-step cleanups and refuses a step that deletes the namespace together with other commands. Manual options need two or more `when`-less siblings including `standard`; `--option` is validated.
- SIGTERM and SIGHUP cancel the run, so the running step is killed instead of orphaned; state stays `deploying` (partial). The e2e test now interrupts with SIGTERM.
- Steps run in a fresh temporary directory, never the user's cwd.
- `plan` and `guides list` detect the cluster when `--context` is given; without it they use the guide's defaults and need no cluster.
- Every failure, expected or not, prints one JSON envelope with `--json`. An unknown `--llmd-ref` exits 2. The v1.0.0 tarball's commit is checked (`378b42d`).
- Confirmation shows the kube context, namespace and commands with the plan's variables filled in.
- Doctor fails when the namespace already holds an llmdup stack (guides do not stack), and warns when CRDs or Helm releases cannot be listed.
- Readiness waits only for the guide's model-server pods and the router release's pods; with no verify step it waits at the end.
- All of a guide's `sensitive` variables, plus `HF_TOKEN`, are redacted from logs and refused by `--set`.
- `mcp>=2.3`; NOTICE lists the rendered GPU overlay fixture; the local-profile flow moved from cli.py into `core/local.py`; MCP `doctor` takes the same choices as `up`.

## Phase 6: Real clusters and release
- [ ] Gateway mode, and every accelerator and engine the guide's matrix allows. Gateway mode on GKE: two llmdup fixes landed, the run then stopped on a GCP API that is off in the test project (below).
- [x] README: install, a local demo, screenshots of the UI, and the command reference. Screenshots are now the app on the GKE cluster below.
- [ ] Publish 0.1.0 to PyPI after the name check. Name check done 16 Nov 2026: `llmdup` is free on PyPI (404) and as a GitHub user/org (404). Not published: needs the user's go-ahead.

Done when: not met yet. It needs `up optimized-baseline` on real accelerators the guide supports (4 x 80 GB GPUs, or TPU v5p/v6e/v7).

### GKE run, 17 Nov 2026

Cluster: GKE 1.36.4-gke.2377000, zonal us-central1-a, 2 x e2-standard-4 plus 1 spot g2-standard-4 with one NVIDIA L4, Gateway API enabled. Project quotas: 1 GPU across all regions, no H100/A100, 16 TPU v5e chips, 32 vCPU. No optimized-baseline overlay fits: GPU wants 4 x 80 GB, TPU wants v5p/v6e/v7 (no v5e overlay), CPU wants 128 cores. So the real model servers did not run; the simulator ran on the real cluster instead (`--profile local`, new in this phase). The cluster, its node pools and the proxy-only subnet were deleted after the run.

| Check | Result |
| --- | --- |
| `doctor optimized-baseline` | Real detection: `gpu: 1 x nvidia.com/gpu`, `INFRA_PROVIDER=gke`; capacity warns 4 wanted, 1 allocatable; HF_TOKEN unset fails; exit 3 |
| `up --profile local` standalone | 9/9 steps ok in 80 s, completion through the router |
| `status`, `test`, `down` | ready 3/3 pods; test 5/5 steps ok (request, 10 x HTTP 200 shared prefix, metrics); down leaves nothing |
| UI on GKE | plan, deploy (`u`, confirm), stack, test (`t`), logs, tear down (`ctrl-d`): all ok |
| `up --profile local --mode gateway` | Not finished: GKE answers `NetworkServices API is not enabled` for the inference load balancer's traffic extension; enabling `networkservices.googleapis.com` was not done |

Bugs found on GKE and fixed (commit 7cdceb3):
- The simulator profile ran only on kind. `plan`, `doctor` and `up` take `--profile local` for any cluster, in standalone or gateway mode.
- Upstream differs: the optimized-baseline `deploy.gateway` step pins `export GW_PROVIDER_NAME=none`, but `docs/infrastructure/gateway/gke.md` installs the same chart with `provider.name=gke`. With `none` the chart's HTTPRoute carries `timeouts`, which GKE rejects (`GWCER104 ... timeouts are not supported`), and no GKE HealthCheckPolicy is made. When the stack's `llm-d-inference-gateway` has a `gke-l7-*` class, the plan now adds `gateway.provider` right after `deploy.gateway`: `helm upgrade --reuse-values --set provider.name=gke`. Doctor names the Gateway's class, and warns when the Gateway is missing from the namespace.
- Gateway mode ran the verify step before the Gateway had an address, so `IP` was empty. The wait now also requires the Gateway to be `Programmed` with an address (a missing Gateway does not block; doctor warns about it).
- Stopping a run on macOS printed `PermissionError`: `killpg` answers EPERM, not ESRCH, when only zombies are left in the group. llmdup now signals the child directly in that case.
- The stack view titled the test pane with the test plan's first step (`verify.endpoint.standalone`); it is `verify.tests` now.

Seen, not llmdup: `kubectl run --rm -i` on GKE falls back from attach to streaming logs and prints the pod's output twice. The `cleanup.rest[1]` monitoring delete fails without Prometheus Operator CRDs, as on kind.

