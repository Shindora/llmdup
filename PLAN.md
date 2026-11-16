# llmdup: build plan for Claude Code

As of 16 Nov 2026.

llmdup is a pip-installable terminal app that takes a person or an agent from a Kubernetes cluster to a working llm-d 1.0 endpoint. People get a k9s-style terminal UI; agents and scripts get a CLI with JSON output and an MCP server. Version 0.1 targets existing clusters and a local test cluster; creating cloud clusters is out of scope.

## Decisions

These choices are made so Claude Code can start without asking.

| Decision | Choice | Why |
| --- | --- | --- |
| First user | Someone who already has a Kubernetes cluster, or a laptop with Docker | Creating cloud clusters is out of scope; the upstream create-gke-infra-llm-d skill already covers GKE |
| Product shape | One core library with three front ends: a k9s-style terminal UI for people, a CLI with JSON output for scripts, and an MCP server plus skill for agents | People and agents get the same behaviour, and there is one code path to test |
| Language | Python 3.14 or newer, Textual for the terminal UI, Typer for the CLI, Pydantic for config and JSON output | The upstream guide tooling is Python, so the core imports it directly. A Rust core was set aside because it would have to reimplement that tooling. uv installs Python 3.14 for anyone who lacks it |
| Upstream reuse | Read the upstream `guide.yaml` files at a pinned llm-d tag; never copy Helm values into this repo | We do not have to chase every llm-d release |
| Cluster access | Run `kubectl` and `helm` as subprocesses | Same tools as the upstream guides; `--dry-run` can print the exact commands |
| Relation to llm-d-skills | Complement it; do not fork it | Those skills need a coding agent and a repo clone; llmdup is one install for anyone |
| Name and license | `llmdup` (working name), Apache-2.0 | Same license as llm-d; check the name on PyPI before publishing |

## Upstream facts

llm-d 1.0 already ships a machine-readable install recipe for every guide, so llmdup drives that recipe and writes no install commands of its own. Every row below was checked against a clone of the `release-1.0` branch on 16 Nov 2026.

| Fact | Detail | Source |
| --- | --- | --- |
| Release | Tag `v1.0.0`; guides are published from branch `release-1.0` | [llm-d/llm-d](https://github.com/llm-d/llm-d/tree/release-1.0) |
| Guide format | Each guide directory holds `guide.yaml` (for tools) and `README.md` (for people); 27 `guide.yaml` files in the branch | [guides/](https://github.com/llm-d/llm-d/tree/release-1.0/guides) |
| `guide.yaml` sections | `support` (accelerator, engine, provider matrix), `env.static` (variables, defaults, allowed values), `prerequisites`, `deploy`, `verify`, `cleanup`, sometimes `benchmark`. Steps carry `run`, and optional `when` and `skip_in` | [optimized-baseline/guide.yaml](https://github.com/llm-d/llm-d/blob/release-1.0/guides/optimized-baseline/guide.yaml) |
| Renderer | `scripts/guide.py` is a CLI and an importable library; it needs only PyYAML. `guide.py emit <guide-dir> env <section...> --var NAME=VALUE` prints a runnable bash script | [scripts/guide.py](https://github.com/llm-d/llm-d/blob/release-1.0/scripts/guide.py) |
| Library API | `Guide.load(path)`, `Guide.emit(...)`, `emit_script(...)`, `discover_modes(...)`, `support_status(...)` | same file |
| Guide index | `docs/well-lit-paths/guides.yaml` lists every published guide by section | [guides.yaml](https://github.com/llm-d/llm-d/blob/release-1.0/docs/well-lit-paths/guides.yaml) |
| Pinned versions | `guides/env.sh` sets Gateway API Inference Extension v1.6.2, router chart v1.0.0 and LeaderWorkerSet 1.0.0 | [guides/env.sh](https://github.com/llm-d/llm-d/blob/release-1.0/guides/env.sh) |
| Deploy shape | Router: one `helm install` of `oci://ghcr.io/llm-d/charts/llm-d-router-standalone` (or `-gateway`). Model servers: `kubectl apply -k guides/<guide>/modelserver/<accelerator>/<engine>/<provider>/` | guide.yaml above |
| One guide, one stack | Each guide deploys its own namespace and a Helm release named after the guide. Guides are not add-ons you stack on each other | [flow-control/guide.yaml](https://github.com/llm-d/llm-d/blob/release-1.0/guides/flow-control/guide.yaml) |
| No git needed | `REPO_ROOT` defaults to `git rev-parse`, but `--var REPO_ROOT=<path>` overrides it, so an unpacked tarball works | tested with `emit` |
| No GPU-free overlay | No guide in the branch ships a simulator overlay. The simulator is a separate image, `ghcr.io/llm-d/llm-d-inference-sim` | [llm-d-inference-sim](https://github.com/llm-d/llm-d-inference-sim) |
| Router is heavy by default | The base values request 4 CPU and 8 Gi each for the endpoint picker and its proxy | [base.values.yaml](https://github.com/llm-d/llm-d/blob/release-1.0/guides/recipes/router/base.values.yaml) |
| Cluster minimum | Kubernetes 1.29 or newer; 1.34 or newer for overlays that use Dynamic Resource Allocation | [deploy-llm-d skill](https://github.com/llm-d-incubation/llm-d-skills/blob/main/skills/deploy-llm-d/SKILL.md) |
| Existing agent skills | `create-gke-infra-llm-d`, `deploy-llm-d`, `teardown-llm-d`, `run-llm-d-benchmark`, `compare-llm-d-configurations`, `configure-wva-autoscaling-llm-d`, and `llm-d-autoconfig` | [llm-d-skills](https://github.com/llm-d-incubation/llm-d-skills) |

The gap llmdup fills: those skills need a coding agent, and `deploy-llm-d` needs a local clone. No upstream terminal UI, deploy CLI with JSON output, MCP server or GPU-free try-out turned up in the repos checked.

Two adjacent tools exist and stay out of llmdup's scope. [llm-d-planner](https://github.com/llm-d-incubation/llm-d-planner) sizes hardware and recommends a configuration from business requirements. [hermes](https://github.com/llm-d-incubation/hermes) scans a cluster for RDMA and GPU topology. llmdup deploys a guide you already picked; it does not choose one for you.

## Architecture

One core library does all the work. The terminal UI, the CLI and the MCP server only collect input and show results.

```text
  Terminal UI (Textual)      CLI (Typer, --json)      MCP server + skill
      for people                 for scripts              for agents
          |                          |                        |
          +--------------------------+------------------------+
                                     v
  +---------------------- core library, Python 3.14 ----------------------+
  |  guides, planner  |  detect, doctor  |  runner  |  state, status      |
  +---------^------------------------------------+------------------------+
            | reads guides                       | runs commands
            |                                    v
  llm-d release, cached                  kubectl and helm  --->  Kubernetes cluster
  (guide.yaml + guide.py, pinned tag)    (subprocesses)          (router, model servers)
```

The front ends never call kubectl or helm. They call the core, which reads the pinned llm-d release and runs its commands.

### How a deploy runs

1. `upstream` downloads the llm-d tarball for the pinned ref into `~/.cache/llmdup/llm-d/<ref>/` and records the commit.
2. `guides` imports that checkout's own `scripts/guide.py` and parses the chosen `guide.yaml`.
3. `planner` picks the step paths for the chosen mode and variables, in guide order, and tags each step.
4. For each step, `planner` calls `emit_script(guide, ["env", <step>], overrides)` with `REPO_ROOT` set to the cache path.
5. `runner` runs each script with bash, streams the output as events, and stops at the first failure.
6. Between deploy and verify, `runner` waits until pods labelled `llm-d.ai/guide=<guide>` are ready.
7. `state` writes the guide, variables, mode, llm-d ref and commit to a ConfigMap, so `status` and `down` work from any machine.

### Step tags

| Tag | Rule | Effect |
| --- | --- | --- |
| skipped | `prerequisites.clone` | Never run; llmdup already holds the release |
| cluster-scoped | Any other `prerequisites.*` step except `namespace` and `secrets` | Needs confirmation before it runs |
| sends traffic | `verify.tests.*` | Only the guide's first test runs after deploy; `--no-verify` skips it |
| mode | A step path that ends in `standalone` or `gateway` | Only the chosen mode runs |
| manual option | Sibling alternatives with no `when` filter, such as `deploy.modelserver.standard` and `dynamic_slice` | Pick `standard`; anything else needs an explicit flag |

Use [list-guide-steps.py](https://github.com/llm-d-incubation/llm-d-skills/blob/main/skills/deploy-llm-d/scripts/list-guide-steps.py) from the upstream skill as the reference for classifying step paths.

### Repo layout

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

## Command spec

`llmdup` with no arguments opens the terminal UI. Every subcommand runs without prompts and can print JSON, so scripts and agents never need the UI.

| Command | What it does | Main flags |
| --- | --- | --- |
| `llmdup` | Opens the terminal UI; prints help when there is no TTY | `--context`, `--llmd-ref` |
| `llmdup guides list` | Lists the guides in the pinned release with section, modes and support for the detected accelerator | `--section`, `--json` |
| `llmdup guides show <guide>` | Shows variables, defaults, the support matrix and step paths | `--json` |
| `llmdup doctor [guide]` | Runs the preflight checks | `--json` |
| `llmdup plan <guide>` | Prints the ordered steps and runs nothing | choice flags, `--script`, `--json` |
| `llmdup up <guide>` | Runs the plan, waits for ready pods, then runs the smoke test | choice flags, `--yes`, `--no-verify`, `--timeout`, `--dry-run` |
| `llmdup status` | Shows deployed stacks, pods, endpoint and readiness | `--namespace`, `--watch`, `--json` |
| `llmdup test` | Runs the guide's `verify.tests.*` steps again | `--namespace` |
| `llmdup down` | Runs the guide's `cleanup` steps | `--namespace`, `--yes`, `--keep-namespace` |
| `llmdup local up` and `local down` | Creates or deletes a kind cluster with the simulator profile | `--yes` |
| `llmdup mcp` | Starts the MCP server on stdio | |
| `llmdup version` | Prints the llmdup version and the pinned llm-d ref | `--json` |

Choice flags, shared by `plan` and `up`: `--accelerator`, `--engine`, `--provider`, `--mode`, `--namespace`, `--model`, and `--set NAME=VALUE` for any other `env.static` variable. Global flags: `--json`, `--context`, `--llmd-ref` (default `v1.0.0`), `--yes`.

- Missing choices come from detection first, then from the guide's defaults.
- A value outside the guide's allowed values exits with code 2 and names the allowed values.
- With `--json`, stdout holds exactly one JSON document. Progress and logs go to stderr.
- `HF_TOKEN` is read from the environment only.

```json
{"schema_version": 1, "ok": true, "command": "up", "data": {}, "errors": []}
```

| Exit code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | A step failed |
| 2 | Bad usage or a value the guide does not allow |
| 3 | `doctor` found a blocking check |
| 4 | Confirmation needed, but `--yes` is absent and there is no TTY |

```bash
llmdup doctor optimized-baseline --json
llmdup plan optimized-baseline --accelerator gpu --script > deploy.sh
llmdup up optimized-baseline --yes
llmdup status --json
llmdup down --namespace llm-d-optimized-baseline --yes
```

## Terminal UI

The terminal UI is a keyboard-only app in the style of k9s, built with Textual. It has a header with cluster facts and key hints, one main view, and a breadcrumb line. Keys follow k9s wherever the meaning is the same.

The mockups are at https://claude.ai/artifact/MCnkKfnHVNE2pfKaHnKVJ4 (seven screens). Claude Code cannot open that link, so the wireframe and tables here are the spec.

```text
┌────────────────────────────────────────────────────────────────────────────────────────────┐
│ Context:     my-gpu-cluster    <enter>   Plan      <j/k> Down, up      </>   Filter llmdup │
│ Cluster:     Kubernetes v1.34  <d>       Describe  <g/G> Top, bottom   <:>   Command 0.1.0 │
│ llm-d:       v1.0.0            <shift-d> Doctor    <tab> Next section  <?>   Help          │
│ Namespace:   all               <u>       Deploy    <[ ]> History       <esc> Back          │
│ Accelerator: gpu x 8                                                                       │
│ ┌────────────────────────────────────────────────────────────────────────────────────────┐ │
│ │ /cache_                                            prompt row: shown only while typing │ │
│ └────────────────────────────────────────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────── guides(all)[3] </cache> ────────────────────────────────┐ │
│ │ GUIDE                         WHAT IT DOES                      GPU + VLLM  DEPLOYED   │ │
│ │ precise-prefix-cache-routing  Enable Precise Prefix-Cache A...  validated   no         │ │
│ │ tiered-prefix-cache           Configure a Tiered Prefix Cache   validated   no         │ │
│ │ p2p-kv-cache-sharing          Enable P2P Prefix Cache Sharing   community   no         │ │
│ │                                                                                        │ │
│ └────────────────────────────────────────────────────────────────────────────────────────┘ │
│ [guides]                                                      flash message, right-aligned │
└────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Views

| View | Opens with | Shows | Keys in this view |
| --- | --- | --- | --- |
| guides | Start, `:guides` | Guides by section, support for the detected accelerator, whether each is deployed | `enter` plan, `d` describe, `shift-d` doctor, `u` deploy, `tab` next section |
| doctor | `shift-d`, `:doctor` | Each check with its status, what was found, and a fix | `ctrl-r` run again, `enter` show fix, `c` copy fix |
| plan | `enter` on a guide, `:plan <guide>` | A variables pane, an ordered steps pane, and the command of the selected step | `e` edit value, `enter` show command, `ctrl-s` save script, `u` deploy, `tab` switch pane |
| deploy | `u` | Step progress on the left, a live log on the right | `ctrl-k` cancel, `l` full log, `s` autoscroll, `enter` step output |
| stacks | `:stacks` | One row per deployed stack; `enter` opens its pods, endpoint and last test | `l` logs, `d` describe, `t` test request, `m` metrics, `shift-f` port-forward, `ctrl-d` tear down |
| logs | `l` | A pod log or the deploy log | `s` autoscroll, `w` wrap, `t` timestamps, `ctrl-s` save |
| help | `?` | Every key, grouped | `esc` close |

### Keys in every view

| Group | Keys |
| --- | --- |
| Move | `j` or `↓` down, `k` or `↑` up, `g` top, `shift-g` bottom, `ctrl-f` page down, `ctrl-b` page up, `enter` open, `esc` back, `tab` next pane |
| History | `[` back, `]` forward, `-` last view |
| Search | `/text` filter rows, `/!text` invert, `/-f text` fuzzy, `esc` clear |
| Sort | `shift-n` by name, `shift-s` by status, `shift-a` by age |
| Command prompt | `:` open, `tab` accept the suggestion, `↑` and `↓` command history, `ctrl-u` clear input, `enter` run |
| Commands | `:guides`, `:doctor`, `:plan <guide>`, `:stacks`, `:logs`, `:ctx [name]`, `:ns [name]`, `:q` |
| General | `?` help, `ctrl-r` refresh, `ctrl-a` all commands, `ctrl-e` hide the header, `c` copy, `ctrl-c` quit |

### Behaviour

- Every action calls the same core function as the matching CLI command. No `kubectl` call lives in `tui/`.
- All keys are declared once in `tui/keys.py`. The header hints and the help view are generated from it, so they cannot drift apart.
- The filter searches every visible column and narrows the table as you type. The view title shows it, as in `guides(all)[3] </cache>`.
- The command prompt suggests a completion in dim text; `tab` accepts it.
- Cluster-scoped steps and tear down open a confirm dialog that shows the exact command.
- A deploy runs in a background worker. Leaving the view does not stop it; quitting during a deploy asks first.
- The `HF_TOKEN` value is never drawn. Show `set` or `not set`.
- The layout must work at 100 columns by 30 rows.

Theme from the mockups: background `#0E1116`, text `#D7DCE4`, dim text `#8B95A5`, borders `#3A4250`, accent and selected row `#F2B34C`, keys `#5CC8E8`, ok `#6FD0B0`, failure `#FF8A7A`.

## Build phases

Seven phases, each small enough to review, and the first six can be checked on a laptop without a GPU. Do them in order and stop after each one.

### Phase 0: Scaffold

- [ ] Create the repo layout above as a `uv` project with a `src/` layout and the entry point `llmdup = llmdup.cli:app`
- [ ] Add Typer, Pydantic, PyYAML, Textual and the MCP Python SDK; for development add pytest, ruff, a type checker and Textual's snapshot plugin
- [ ] Implement `llmdup version` with `--json`
- [ ] Set `requires-python` to 3.14 or newer. Add GitHub Actions for lint, types and tests on Python 3.14, on 3.15, and on the free-threaded build 3.14t
- [ ] Add `LICENSE` (Apache-2.0), `NOTICE`, a README stub, `CLAUDE.md` and `PROGRESS.md`

Done when:

- `uv run llmdup version --json` prints the JSON envelope.
- Lint, type check and `pytest` pass locally and in CI.

### Phase 1: Guides and plan, no cluster

- [ ] `upstream.py`: download `https://codeload.github.com/llm-d/llm-d/tar.gz/refs/tags/<ref>` into the cache, unpack it safely, and record the ref and commit
- [ ] Honour `LLMDUP_LLMD_PATH` to use a local llm-d checkout instead of downloading
- [ ] Commit a trimmed fixture under `tests/fixtures/llm-d-v1.0.0/`: `scripts/guide.py`, `guides/env.sh`, `docs/well-lit-paths/guides.yaml`, `guides/recipes/`, and the guides `optimized-baseline`, `flow-control` and `pd-disaggregation`
- [ ] `guides.py`: import the checkout's `guide.py`, then build a `GuideInfo` with name, title, section, modes, variables, allowed values and the support matrix
- [ ] `planner.py`: build a `Plan` of ordered `Step`s, each with its path, tags and emitted script; drop steps whose script is empty after filtering
- [ ] CLI: `guides list`, `guides show` and `plan`, with `--json` and `--script`

Done when:

- A network-marked test loads the real `v1.0.0` release and builds a default plan for all 27 guides without an error.
- `llmdup plan optimized-baseline --accelerator gpu --json` lists exactly these steps in order: `prerequisites.gaie`, `prerequisites.namespace`, `prerequisites.secrets`, `deploy.router_values`, `deploy.standalone`, `deploy.modelserver.standard`, `verify.endpoint.standalone`, `verify.tests.request`.
- `llmdup plan optimized-baseline --accelerator bogus` exits with code 2 and prints the allowed values.
- The output of `plan --script` passes `bash -n`.

### Phase 2: Detect and doctor

- [ ] `kube.py`: one wrapper that runs `kubectl`, `helm`, `kind` and `bash` with a timeout and captured output, plus a fake for tests
- [ ] `detect.py`: context, server version, installed CRDs, existing Helm releases, provider (`gke` when nodes carry `cloud.google.com/gke-nodepool`)
- [ ] `detect.py`: map allocatable resources and DeviceClasses to `ACCELERATOR_TYPE`, using the table in step 3 of the upstream `deploy-llm-d` skill
- [ ] `doctor.py`: the checks in the table below, each returning ok, warn or fail with `found` and `fix`
- [ ] CLI: `doctor`, exit code 3 when any check fails

| Check | Fails or warns when |
| --- | --- |
| Tools | Fail: `kubectl` or `helm` is missing |
| Cluster reachable | Fail: the current context does not answer |
| Kubernetes version | Fail: older than 1.29 |
| Guide support | Fail: the accelerator and engine pair is unsupported. Warn: it is `community` |
| Capacity | Warn: the rendered overlay asks for more accelerators than the nodes can allocate. Sum the requests from `kubectl kustomize` output |
| Inference Extension CRDs | Warn: not installed; the plan installs them after confirmation |
| `HF_TOKEN` | Fail: not set and the plan includes `prerequisites.secrets` |
| Existing release | Fail: a Helm release named after the guide already exists in the namespace |
| Gateway provider | Warn: mode is `gateway` and no Gateway class is installed |

Done when:

- Unit tests with recorded `kubectl -o json` output cover a GPU cluster, a CPU-only cluster and an unreachable cluster.
- `llmdup doctor optimized-baseline --json` on a fresh kind cluster reports the CRD warning and no accelerator.

### Phase 3: Local profile, up, status, down

- [ ] `local.py`: `local up` creates a kind cluster named `llmdup` when it is missing; `local down` deletes it
- [ ] `local_profile/sim-overlay`: two replicas of `ghcr.io/llm-d/llm-d-inference-sim`, labelled `llm-d.ai/guide: optimized-baseline`; read the simulator's README and `manifests/deployment.yaml` for its flags and current tag
- [ ] `local_profile/router-small.values.yaml`: lower the router's CPU and memory requests so it fits a laptop
- [ ] Local plan: the `optimized-baseline` plan in standalone mode, with the model server step replaced by the simulator overlay and the small values applied after the router install
- [ ] `runner.py`: run steps, yield events, wait for readiness, stop at the first failure and report the failing command with its last 40 log lines
- [ ] `state.py` and `status.py`, then CLI `up`, `status`, `test` and `down`
- [ ] Confirmation for cluster-scoped steps and for `down`

Done when, on a machine with Docker:

- `llmdup local up --yes` ends with an HTTP 200 from `/v1/completions` through the router.
- `llmdup status --json` shows the router and two simulator pods ready, and the endpoint.
- After `llmdup down --yes`, `llmdup status` shows nothing deployed.
- After a step is killed mid-run, `status` reports a partial deploy and `down` cleans it up.
- The end-to-end job passes in CI.

### Phase 4: Terminal UI

- [ ] Read the current Textual docs for screens, tables, inputs, logs, workers and testing before writing code
- [ ] `tui/keys.py`, the header with generated hints, the breadcrumb line, and the prompt with command and filter modes, suggestions and history
- [ ] The seven views in the Terminal UI section
- [ ] View history, filtering and sorting as listed under "Keys in every view"
- [ ] The confirm dialog, and a worker that turns runner events into step progress and log lines
- [ ] A fake core for tests, so UI tests need no cluster

Done when:

- Pilot tests open every view by key and by command, and typing `/cache` leaves only rows that contain `cache`.
- A test asserts that every binding in `keys.py` appears in the help view.
- Snapshot tests cover all seven views at 120 by 36, and the guides view at 100 by 30.
- With the local profile, a person can plan, deploy, open the stack, send a test request and tear down without leaving the UI.

### Phase 5: MCP server and skill

- [ ] `mcp_server.py` with the tools `list_guides`, `show_guide`, `doctor`, `plan`, `up`, `status`, `test` and `down`
- [ ] `up` and `down` require `confirm: true`; without it they return the plan and what needs confirmation
- [ ] Tool results are the same Pydantic models as the CLI's `data` field
- [ ] `skills/llmdup/SKILL.md`: when to use llmdup, the commands, the exit codes, and the rule to tell the user what will be created before passing `--yes`

Done when:

- An MCP client test lists the eight tools and calls `plan`.
- Calling `up` without `confirm` changes nothing in the cluster.

### Phase 6: Real clusters and release

- [ ] Gateway mode, and every accelerator and engine the guide's matrix allows
- [ ] README: install, a local demo, screenshots of the UI, and the command reference
- [ ] Publish 0.1.0 to PyPI after the name check

Done when a person with a GPU cluster has run `doctor`, `up optimized-baseline`, `status`, `test` and `down` on it, and the result is recorded in `PROGRESS.md`. Claude Code cannot do this part alone.

### Out of scope for 0.1

- Creating cloud clusters
- Choosing a configuration from a workload or an SLA
- Benchmarks
- Stacking several guides in one namespace
- Upgrades between llm-d versions
- Guides that have no `guide.yaml`

## Testing without GPUs

Every phase except the last can be verified on a laptop with Docker. Only Phase 6 needs a real accelerator.

| Layer | What runs | Needs |
| --- | --- | --- |
| Unit | Planner, detect and doctor against the fixture and a fake `kube.py` | Nothing |
| Upstream | Load and plan all 27 guides from the real `v1.0.0` tarball | Network |
| Terminal UI | Textual pilot and snapshot tests on a fake core | Nothing |
| End to end | `local up`, `status`, `test` and `down` on kind with the simulator | Docker, kind, kubectl, helm |
| Real cluster | The Phase 6 checklist | A GPU cluster and a person |

- Pytest markers `network` and `e2e` keep the default run offline and fast.
- CI runs unit and UI tests on every push, on Python 3.14, 3.15 and 3.14t. Upstream and end-to-end tests run on main and nightly.
- If a dependency has no free-threaded wheel yet, mark the 3.14t job as allowed to fail and note it in `PROGRESS.md`.
- End-to-end tests refuse to start unless the current kube context begins with `kind-llmdup`.
- If Docker is missing where Claude Code runs, skip the end-to-end tests and say so in `PROGRESS.md`. Do not replace them with mocks.

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

## Open questions

None of these blocks Phase 0. Each has a default the plan already assumes.

- [ ] Is the name `llmdup` free on PyPI and GitHub?
- [ ] Pin the tag `v1.0.0`, or follow the branch `release-1.0`, which receives fixes? The plan assumes the tag.
- [ ] Should the smoke test run by default after `up`? The upstream skill makes test requests opt-in, because they warm the cache and can skew benchmarks. The plan assumes on by default.
- [ ] Which real cluster validates Phase 6: which accelerator and provider, and who runs it?
- [ ] Keep llmdup as its own repo, or propose it to `llm-d-incubation`?

## Kickoff

1. Put this file in a new empty folder as `PLAN.md`.
2. Run `git init` in that folder and start Claude Code there.
3. Paste the prompt below.

```text
Read PLAN.md from start to finish before doing anything.

Then:
1. Create CLAUDE.md containing the "Rules for Claude Code" section
   (including "Python conventions") and the repo layout from PLAN.md.
2. Create PROGRESS.md with every phase and its tasks as a checklist.
3. Do Phase 0 only. Run its "Done when" checks, commit,
   update PROGRESS.md, and stop.

After each phase, stop and wait for me to say "next phase".
Before using Textual, Typer or the MCP Python SDK, read their current docs.
If PLAN.md and the upstream llm-d repo disagree, the upstream repo wins:
tell me what differs.
```

## Sources

All opened or cloned on 16 Nov 2026.

- [Announcing llm-d 1.0](https://cloud.google.com/blog/topics/ai-infrastructure/llm-d-1-0-an-open-source-stack-for-distributed-inference), Google Cloud blog, 20 Oct 2026
- [llm-d quickstart, v1.0](https://llm-d.ai/docs/getting-started/quickstart)
- [llm-d/llm-d, branch release-1.0](https://github.com/llm-d/llm-d/tree/release-1.0), cloned and inspected
- [llm-d-incubation/llm-d-skills](https://github.com/llm-d-incubation/llm-d-skills), cloned and inspected
- [llm-d/llm-d-inference-sim README](https://github.com/llm-d/llm-d-inference-sim)
- [llm-d-incubation/llm-d-planner](https://github.com/llm-d-incubation/llm-d-planner) and [llm-d-incubation/hermes](https://github.com/llm-d-incubation/hermes), READMEs only
- [Python 3.15.0 release post](https://blog.python.org/2026/10/python-3150-final-is-here/), Python Insider
