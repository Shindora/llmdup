# llmdup

From a Kubernetes cluster to a working [llm-d](https://github.com/llm-d/llm-d) 1.0 endpoint.

- People: `llmdup` opens a k9s-style terminal UI.
- Scripts: every subcommand takes `--json` and prints one JSON document on stdout.
- Agents: `llmdup mcp` starts an MCP server; `skills/llmdup/SKILL.md` teaches an agent the CLI.

llmdup drives the upstream `guide.yaml` recipes of a pinned llm-d release (default `v1.0.0`) and writes no install commands of its own.

```bash
uv tool install llmdup
llmdup version --json
```
