---
name: llmdup
description: Deploy, check, test and tear down llm-d 1.0 guides on a Kubernetes cluster with the llmdup CLI (JSON output) or its MCP server. Use when the user wants an llm-d inference endpoint from an existing cluster, a GPU-free local try-out on kind, a preflight check of a cluster for an llm-d guide, or the status or teardown of a stack llmdup deployed.
---

# llmdup

llmdup drives the machine-readable recipes (`guide.yaml`) of a pinned llm-d release (default `v1.0.0`). It writes no install commands of its own: every command it runs comes from the upstream guide, except the local profile's simulator overlay.

Use it when the user names an llm-d guide (or wants "llm-d on my cluster", "an inference endpoint", "try llm-d without GPUs"). Do not use it to choose a configuration from a workload or SLA, to run benchmarks, to create cloud clusters, or to stack several guides in one namespace.

## Rules

1. Before anything that creates or deletes resources, tell the user what will be created or deleted, in which cluster context and namespace, and show the exact commands. Run `llmdup up ... --dry-run` (or the MCP `up` tool without `confirm`) to get them. Pass `--yes` / `confirm: true` only after the user agrees.
2. Run `llmdup doctor <guide> --json` before `up` and report every `fail` and `warn` with its `fix`.
3. `HF_TOKEN` comes from the environment only. Never put its value in a command, a `--set`, a file or a message; say `set` or `not set`.
4. Pass `--context <name>` explicitly whenever the user has more than one kube context. Never deploy to a context the user did not name.
5. Test requests (`up` without `--no-verify`, `test`) send traffic and warm the prefix cache. Mention it before benchmarks.

## Commands

Every subcommand takes `--json` and then prints exactly one JSON document on stdout: `{"schema_version": 1, "ok": ..., "command": ..., "data": ..., "errors": [...]}`. Progress and logs go to stderr.

| Command | Use |
| --- | --- |
| `llmdup guides list [--section S] --json` | Guides with section, modes and support |
| `llmdup guides show <guide> --json` | Variables, defaults, allowed values, support matrix, step paths |
| `llmdup doctor [guide] [choice flags] --json` | Preflight checks: tools, reachability, version, accelerators, CRDs, support, capacity, HF_TOKEN, existing release, gateway |
| `llmdup plan <guide> [choice flags] --json` | Ordered steps with tags and exact commands; `--script` prints one bash script. Needs no cluster |
| `llmdup up <guide> [choice flags] [--dry-run] [--yes] [--no-verify] [--timeout S]` | Doctor, then run the plan, wait for ready pods, send one test request |
| `llmdup status [--namespace N] --json` | Stacks llmdup deployed: phase, health, pods, endpoint, port-forward command |
| `llmdup test [--namespace N]` | Run the guide's `verify.tests` steps again |
| `llmdup down [--namespace N] [--keep-namespace] --yes` | Run the guide's cleanup steps |
| `llmdup local up --yes` / `llmdup local down --yes` | Kind cluster `llmdup` with the llm-d simulator; no GPU |
| `llmdup mcp` | MCP server on stdio |
| `llmdup version --json` | llmdup version and pinned llm-d ref |

Choice flags (`plan`, `up`, `doctor`): `--accelerator`, `--engine`, `--provider`, `--mode`, `--namespace`, `--model`, `--set NAME=VALUE`, `--option GROUP=CHOICE` (manual options such as `modelserver=dynamic_slice`). Global: `--context`, `--llmd-ref`. Unset choices come from cluster detection, then from the guide's defaults.

## Exit codes

| Code | Meaning | What to do |
| --- | --- | --- |
| 0 | Success | |
| 1 | A step failed | Read `data.failed_step`, `data.command` and `data.tail` (last 40 log lines) |
| 2 | Bad usage, or a value the guide does not allow | `errors[0].hint` lists the allowed values |
| 3 | `doctor` found a blocking check | Report the failing checks and their fixes |
| 4 | Confirmation needed, no `--yes`, no TTY | Show the user the commands in `errors[0].hint`, ask, then rerun with `--yes` |

## Typical flow

```bash
llmdup guides list --json
llmdup doctor optimized-baseline --context my-cluster --json
llmdup up optimized-baseline --context my-cluster --dry-run          # show the user the commands
llmdup up optimized-baseline --context my-cluster --yes --json       # after the user agrees
llmdup status --context my-cluster --json
llmdup down --context my-cluster --namespace llm-d-optimized-baseline --yes
```

No cluster: `llmdup local up --yes` (needs Docker or Podman and kind; with Podman set `KIND_EXPERIMENTAL_PROVIDER=podman`).

## MCP tools

`list_guides`, `show_guide`, `doctor`, `plan`, `up`, `status`, `test`, `down`. Results are the same models as the CLI's `data`; tools that can return two shapes (`up`, `down`) wrap it as `{"result": ...}`. `up` and `down` without `confirm: true` change nothing and return the plan, the preflight report (for `up`) and the commands that need confirmation in `confirm`. Errors come back as tool errors whose text includes the hint.
