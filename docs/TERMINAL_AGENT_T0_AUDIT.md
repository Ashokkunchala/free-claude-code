# T0 Audit - Terminal-Native Agent: Existing Surface and Extension Points

**Status:** T0 deliverable from `docs/TERMINAL_OPERATIONS_ROADMAP.md` (PR #3)
**Method:** Source-level audit of the CLI, process management, config, security boundaries, and test layout at main after the CI baseline repair (PR #5). No behavior changes in this PR.

## 1. What exists today

### 1.1 CLI surface (`src/free_claude_code/cli/`, `pyproject.toml [project.scripts]`)

Installed commands are thin entry points in `cli/entrypoints.py` (55 lines) that defer heavy imports:

| Command | Implementation | Purpose |
|---|---|---|
| `fcc-server` | `cli/commands.py::serve` (447 lines) | Run the FastAPI server |
| `fcc-doctor` | `cli/doctor.py` | Local diagnostic report (prints and copies) |
| `fcc-status` | `cli/dashboard.py` | Usage and health dashboard; already supports `--json` |
| `fcc-statusline` | `cli/statusline.py` | One-line status for harness integrations |
| `fcc-profile` | `cli/profiles.py` | Per-project model profiles |
| `fcc-<harness>` (claude, codex, pi, opencode, cline, hermes, dsh, grok, muse, aider) | `cli/launchers/<name>.py` | Launch a coding-harness CLI against the local proxy |
| `fcc-desktop` (gui) | `cli/desktop_entrypoint.py` | Desktop shell |

There is no `fcc` umbrella command and no `run/exec/inspect/skills/approvals/audit/mcp/config` families yet. `fcc-status --json` is the only structured-output precedent; output conventions and exit codes are not yet unified.

### 1.2 Process management

- `cli/process_registry.py` (77 lines): a process-global set of PIDs with an `atexit` best-effort cleanup. Windows uses `taskkill /PID <pid> /T /F` (tree kill); POSIX sends SIGTERM to the single PID only (no process-group kill, no SIGKILL escalation, no timeout).
- `cli/launchers/common.py::run_client_process` (103 lines): the one place harness CLIs are spawned. Argument-vector `Popen` (no shell), `env` passed as a mapping, `wait()`, mirror exit code, `KeyboardInterrupt` kills the child tree best-effort. No stdout/stderr capture or streaming, no timeout, no output cap, no cancellation token, no PTY.
- `cli/server_bootstrap.py`: spawns the server process (`Popen`, argument vector).
- `harnesses/resources.py`, `cli/managed/`: managed-session lifecycle for harnesses running under the server.

### 1.3 Server and runtime (`src/free_claude_code/runtime/`)

FastAPI app composed in `runtime/bootstrap.py`, served by `cli/uvicorn_server.py` + `cli/server_socket.py`, with lifecycle in `runtime/lifecycle.py`. Existing services: provider routing/fallback (`application/routing.py`, `providers/`), code sessions (`application/code_sessions/` + `runtime/code_sessions_sqlite.py`), messaging (`runtime/messaging_service.py`), web tools with an egress boundary (`runtime/web_tools/egress.py`), diagnostics (`runtime/diagnostics.py`).

### 1.4 Configuration (`src/free_claude_code/config/`)

Pydantic `Settings` (`config/settings.py`, 912 lines, ~175 fields) loaded from env + env files (`loader.py`, `env_files.py`). API keys are individual fields with `validation_alias` (`OPENAI_API_KEY`, `AZURE_OPENAI_API_KEY`, ...). Admin config under `config/admin/`. There is no redaction helper for config display yet; a future `fcc config` must not print these values.

### 1.5 Tests

394 test files: `tests/cli/` (launcher, process registry, server bootstrap/shutdown, doctor, dashboard, profiles), `tests/contracts/` (import boundaries, capability map, feature manifest), `tests/api|application|config|core|harnesses|messaging|providers|runtime|scripts/`, and Playwright e2e in `e2e/`. `tests/cli/test_process_registry.py` and `tests/cli/test_managed_session_shutdown.py` are the process-lifecycle specs to extend. CI matrix: Linux/macOS/Windows, pytest + Playwright + ruff + ty; `type: ignore`/`ty: ignore`/`__future__` annotations are banned.

## 2. Gaps against the roadmap (T1/T2 scope)

| Roadmap requirement | Today | Gap |
|---|---|---|
| Structured process execution (argv, cwd, env allowlist, timeout, output cap, exit code) | argv + env + wait only (`launchers/common.py`) | No cwd control, no env allowlist, no timeout, no output cap |
| Streaming stdout/stderr with timestamps and redaction | None (inherit terminal) | Entirely new |
| Cancellation + process-tree cleanup | SIGTERM single PID (POSIX), taskkill tree (Windows), atexit net | No process groups on POSIX, no escalation SIGTERM->SIGKILL, no Job Objects on Windows, no orphan detection |
| Cross-platform lifecycle tests | Registry/launcher unit tests | No fake-executable cross-platform integration suite for timeout/cancel/orphans |
| Policy/permission model (R0-R3) | None anywhere (`application/execution.py` is model-execution, not actions) | Entirely new; T2 |
| Audit events for commands | None | Entirely new; design alongside T1 executor so events are emitted from day one |
| Unified CLI output (`--json`/JSONL) and stable exit codes | `fcc-status --json` only | Conventions to define before adding families |

## 3. T0 decisions

1. **Extend, do not parallel-build.** The terminal core grows out of `cli/process_registry.py` + `cli/launchers/common.py`, into a new `src/free_claude_code/terminal/` package. No second server, no new process supervisor outside it; the server-side managed sessions keep working and can adopt the executor incrementally.
2. **One executor primitive.** A `TerminalExecutor` producing a `CommandResult` (argv, cwd, duration, exit code, truncated-output flags) with streaming callbacks; launchers migrate to it first as the proof consumer, because they already cover Windows/macOS/Linux in CI.
3. **Policy is a separate layer (T2).** T1 ships the executor with the audit-event hook points (command planned/started/finished/cancelled/timeout) but without R0-R3 enforcement; the executor API takes an explicit policy-decision parameter so T2 adds checks without changing call sites.
4. **No behavior change to harness UX in T1.** Launchers keep inheriting the terminal by default; capture/streaming is opt-in per call site until the agent loop (T2) needs it.
5. **Tests are the spec.** New cross-platform integration tests use fake executables (per roadmap section 10) and run in the existing pytest matrix; Playwright untouched.

## 4. T1 module plan (`src/free_claude_code/terminal/`)

```
terminal/
  __init__.py
  executor.py        # TerminalExecutor.run(CommandSpec) -> CommandResult; streaming callbacks
  spec.py            # CommandSpec: argv, cwd, env allowlist, timeout, output cap, pty flag
  result.py          # CommandResult + OutputTruncated flags + timing
  processes.py       # process-group/Job-Object spawn, tree kill, escalation, orphan sweep
  redaction.py       # secret redaction for captured output (config values + common token shapes)
  audit.py           # audit event sink protocol + in-memory/jsonl implementations
  errors.py          # CommandTimeout, CommandCancelled, SpawnError
tests/terminal/
  conftest.py        # fake executables (sleep/echo/hang/spawn-child) per platform
  test_executor.py, test_processes.py, test_redaction.py, test_audit.py,
  test_cancellation.py, test_timeout.py, test_orphans.py
```

Migration order: (a) land package + tests green; (b) move `process_registry` tracking into `processes.py` keeping its public API as a shim; (c) port `launchers/common.py::run_client_process` onto the executor; (d) port `server_bootstrap.py`.

## 5. Risks and open questions

- **Windows Job Objects** need pywin32 or ctypes; decide dependency policy before T1 lands (ctypes keeps zero new deps).
- **POSIX process groups** change Ctrl+C semantics for interactive harnesses; group creation must be opt-in per spawn so `fcc-claude` keeps current behavior until the agent loop owns the terminal.
- **Redaction source of truth:** `config/settings.py` key fields + provider catalog; redaction must cover base64/plain variants without false-positive storms in diffs.
- **Exit-code stability** for existing commands must be pinned by tests before launcher migration (several launchers rely on mirrored child exit codes).
