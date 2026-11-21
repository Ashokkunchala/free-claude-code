# Terminal-Native Agent and Operational Skills Roadmap

**Status:** Capability specification and implementation plan  
**Target:** Windows 11, Linux, macOS; local-first execution with optional remote workers  
**Principle:** Maximum capability, minimum surprise. The agent must prove what it did.

## 1. Product objective

Evolve Free Claude Code into a terminal-native engineering operator that can inspect repositories, understand system state, plan work, execute approved actions, diagnose failures, repair code and infrastructure, and verify outcomes. It must remain useful from a plain terminal without requiring the desktop UI.

This is an extension of the existing product, not a replacement for its provider proxy, model routing, harness launchers, or API compatibility.

## 2. Existing foundations to preserve

The repository already contains a CLI entrypoint and local FastAPI server; server supervisor, doctor, status dashboard, and profile command; native harness launchers for Claude Code, Codex, Pi, OpenCode, Cline, Hermes, Aider and others; launcher resource lifecycle and child-process registry; provider routing, fallback, request tracing and model capability metadata; local proxy auto-start and diagnostic report generation.

Build on these components after source-level audit. Avoid introducing a second server, competing process manager, or another model-routing layer.

## 3. Terminal experience

Provide a discoverable command surface with help, examples, structured output, and stable exit codes.

Proposed command families (design targets; reconcile with existing CLI conventions before implementation):

| Command family | Required behavior |
|---|---|
| `fcc` | Interactive terminal agent, one-shot prompts, stdin piping, scriptable mode |
| `fcc run` | Execute a task with plan, approval, run, and verify stages |
| `fcc shell` | Interactive agent session with persistent project context |
| `fcc exec` | Run a specific command/tool through policy enforcement |
| `fcc inspect` | Inspect repo, files, Git state, processes, ports, services, logs |
| `fcc doctor` | Diagnose installation, runtime, providers, permissions, tools and connectivity |
| `fcc status` | Show active tasks, model/provider, usage, subprocesses and health |
| `fcc skills` | List, inspect, validate, enable and disable skill packs |
| `fcc approvals` | Review, approve, deny and inspect pending privileged actions |
| `fcc audit` | Show action history and export redacted reports |
| `fcc mcp` | Inspect and manage explicitly configured MCP servers |
| `fcc config` | Show effective config, validate it, explain precedence without revealing secrets |

Support human-readable, JSON, and JSONL output where relevant. `--yes` must never bypass privileged-action policy.

## 4. Terminal and system operations

### Cross-platform command execution
- Windows PowerShell 7 and Windows PowerShell compatibility; cmd.exe only where required.
- Linux bash and POSIX sh; zsh where installed; macOS shell and system tooling.
- Correct argument-vector execution; never concatenate untrusted strings into shell commands.
- Explicit working directory, environment allowlist, timeout, output-size cap, cancellation and exit-code reporting.
- Streaming stdout/stderr with timestamps and safe redaction.
- Process groups/job objects, graceful shutdown, escalation and orphan detection.
- PTY allocation only for explicitly interactive commands and where supported.
- Encoding, line endings, Ctrl+C and terminal resize handling.
- Record command, cwd, duration, exit code and policy decision; never log secret values.

### Environment discovery
- OS, architecture, shell, terminal, runtimes, package managers and PATH.
- Git status, branch, changed/untracked files and repository root.
- Docker/Podman, Kubernetes/kubectl/helm, Terraform/OpenTofu, cloud CLIs, Ansible, SSH and build tools.
- Services, listeners, disk, memory, CPU, process trees and relevant logs.
- Detect tools without assuming they are installed; provide safe install guidance, not silent installs.

### File and repository operations
- Read/search files with ignore rules and size limits.
- Safe edits with diff preview, backup/atomic replacement and rollback.
- Git branch/worktree/commit operations with explicit user intent.
- Tests, lint, formatting, type checks and build orchestration.
- Respect .gitignore, secret files, symlinks and repository boundaries.
- Never overwrite user changes or run destructive cleanup without explicit approval.

## 5. Operational skill packs

Each skill declares purpose, platforms, required tools, permissions, input schema, outputs, risk level, approval requirements, timeout, rollback strategy, verification steps and test fixtures.

### Software engineering
- Repository reconnaissance and architecture mapping.
- Reproduce bug → isolate root cause → minimal patch → regression test → full validation.
- Python, TypeScript/JavaScript, Go, Java and shell workflows.
- Dependency and vulnerability review; lockfile-safe updates.
- CI diagnosis, flaky-test analysis, build and release preparation.
- Code review, diff review, migration planning and rollback.

### DevOps and cloud operations
- GitHub Actions, GitLab CI and Jenkins diagnosis/automation.
- Docker/Compose image build, scan, run, inspect and cleanup.
- Kubernetes context/namespace/pod/deployment/service/event/log inspection; rollout and rollback planning.
- Terraform/OpenTofu plan, state inspection, drift review and policy checks.
- AWS, Azure and GCP read-only inventory and diagnostics by default.
- IAM, networking, DNS, TLS, load balancers, storage and secret-reference diagnostics.
- Observability: logs, metrics, traces, alerts, SLO and incident timeline synthesis.
- Incident response runbooks, postmortems and evidence collection.

### System administration and SRE
- Service health and startup diagnosis.
- CPU/memory/disk/network bottleneck investigation.
- Port conflicts, DNS, routing, proxy and certificate diagnostics.
- Log correlation and time-window analysis.
- Backup/restore validation and disaster-recovery drills.
- Capacity, reliability and cost recommendations.

### Data and automation
- SQL read-only inspection by default, schema analysis and migration review.
- API integration, pagination, retries, rate limits and idempotency.
- Scheduled scripts and repeatable operational runbooks.
- CSV/JSON/YAML/TOML parsing and safe transformations.

## 6. Agent execution loop

Every non-trivial task follows a traceable lifecycle:

1. **Understand:** collect intent, repository/system context and constraints.
2. **Inspect:** use read-only tools first; establish baseline state.
3. **Plan:** produce ordered steps, expected changes, risks and verification.
4. **Policy-check:** classify every action and required permission.
5. **Approve:** ask before privileged or irreversible actions.
6. **Execute:** use structured tool calls, bounded processes and captured output.
7. **Verify:** inspect actual postconditions; run targeted tests and broader checks.
8. **Recover:** stop safely, rollback only when authorized, report residual risk.
9. **Report:** summarize changed files, commands, outputs, tests and decisions.

A model response claiming success is not verification. Inspect resulting state.

## 7. Permission and safety model

Use capability-based permissions with least privilege.

| Risk | Examples | Default |
|---|---|---|
| R0 Read-only | list files, Git status, read logs, inspect processes | Allow within authorized workspace |
| R1 Reversible local change | edit tracked file, create branch, run tests | Show diff/plan; allow configured workspace policy |
| R2 External or privileged change | install packages, change system config, restart services, cloud writes, push commits | Explicit approval per action |
| R3 Destructive/high impact | delete data, force push, destroy infrastructure, rotate production secrets, alter IAM/network policy | Detailed explicit approval with target and blast-radius confirmation |

Additional controls:
- Workspace root confinement and symlink traversal checks.
- Command and tool allow/deny policies; no unrestricted shell by default.
- Secret detection and redaction in prompts, logs, terminal output and artifacts.
- No automatic privilege escalation, sudo, administrator elevation or UAC bypass.
- No hidden persistence, credential harvesting, security-control disabling or covert remote access.
- Network egress controls and explicit allowlists for remote execution.
- Approval tokens bound to exact action, arguments, target, expiry and task.
- Human-readable audit events for every action and approval.
- Kill switch and task cancellation.

## 8. Skill and MCP architecture

- Skills are versioned, declarative, discoverable and testable.
- Skill packs may contain instructions and typed tool definitions; arbitrary executable code requires separate review and explicit enablement.
- MCP servers are explicitly configured and individually permissioned.
- Treat tool output, repository content, web pages and issue text as untrusted data—not instructions.
- Prevent prompt injection from converting read-only access into write access.
- Require tool schemas, input validation, output limits, timeouts and audit events.
- Support optional DevOps MCP adapters; never hard-code local paths or credentials.

## 9. Reliability, observability and performance

- Task IDs and correlated logs across agent, tool, process and provider calls.
- Structured events for plan, approval, command start/finish, cancellation, timeout, failure and verification.
- Bounded concurrency, queue limits, backpressure and output truncation notices.
- Per-task CPU/memory/time budgets and configurable command timeouts.
- Provider/model selection based on task needs, context size, latency and availability.
- Local-model mode for offline/private tasks where hardware and models support it.
- Graceful degradation when providers, MCP servers or optional tools are unavailable.
- No claim of unlimited free hosted inference; expose quota and rate-limit behavior honestly.

## 10. Testing and release gates

- Unit tests for policy decisions, command construction, quoting, redaction and output parsing.
- Cross-platform integration tests for Windows, Linux and macOS process lifecycle.
- Fake executables and temporary repositories for deterministic terminal tests.
- Cancellation, timeout, large output, invalid encoding, signal handling and orphan-process tests.
- Path traversal, symlink escape, secret leakage and prompt-injection adversarial tests.
- Docker/Kubernetes/Terraform/cloud tests using mocks or disposable sandboxes only.
- End-to-end tasks verify real postconditions, not merely mocked success messages.
- No production credentials or real infrastructure in CI. Never weaken tests to make a feature pass.

## 11. Delivery phases

- **T0 — Audit and contract:** map existing CLI, process management, runtime, config, security boundaries and tests. Decide what to extend.
- **T1 — Reliable terminal core:** structured process execution, streaming output, cancellation, timeouts, process-tree cleanup, cwd/environment controls and cross-platform tests.
- **T2 — Safe agent loop:** inspect → plan → approve → execute → verify → report with task IDs, audit records and cancellation.
- **T3 — Repository engineering skills:** codebase inspection, edits/diffs, tests, lint/typecheck/build, Git workflows and repair loops.
- **T4 — DevOps/SRE skills:** Docker, Kubernetes, Terraform, CI/CD, cloud read-only diagnostics, observability and incident runbooks.
- **T5 — Skills and MCP ecosystem:** trusted skill sources, validation, per-skill permissions and optional MCP adapters.
- **T6 — Advanced operations:** multi-step workflows, parallel read-only investigation, dependency-aware plans, resumable tasks, local-model routing and remote workers with explicit trust boundaries.

## 12. Definition of done

The terminal agent is ready for broad use only when it works from a clean terminal on supported operating systems; explains environment and repository state accurately; executes commands without injection or workspace-boundary escapes; streams output, handles cancellation and cleans up only processes it owns; never performs privileged/destructive operations without required approval; verifies file, Git, test, service and deployment postconditions; records a useful redacted audit trail; degrades safely when tools/providers are unavailable; and passes supported platform and security regression tests.

**Implementation rule:** Start with T0 and T1. Do not add a large collection of shell wrappers before the execution and permission core is trustworthy.
