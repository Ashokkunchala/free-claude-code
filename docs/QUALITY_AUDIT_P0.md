# P0 Quality and CI Audit

**Audit date:** 2026-11-18  
**Baseline:** `main` at `03213aa844a668c2eae1378b029135b09771bac4`  
**Scope:** GitHub Actions run #1 for Dependabot PR #1, plus workflow and repository configuration inspection.

## Executive summary

The repository has a substantial implementation and broad automated coverage, but the first observed CI run is red. The failure is not a single dependency-update issue: the Dependabot PR changes an Actions artifact version, while the CI failures are in existing application code, tests, formatting, and typing. Do not merge the dependency PR as a green build until the baseline is repaired or the failures are explicitly understood.

## Observed CI baseline

Workflow: [CI run #1](https://github.com/Ashokkunchala/free-claude-code/actions/runs/44302110661)

- Ruff lint: **passed**
- Suppression / legacy-annotation guard: **passed**
- Ruff format check: **failed** — three Python files need formatting:
  - `src/free_claude_code/config/admin/values.py`
  - `src/free_claude_code/core/anthropic/conversion.py`
  - `src/free_claude_code/providers/groq/client.py`
- `ty check`: **failed** — 45 diagnostics, concentrated in test typing and untyped JSON-like values; includes invalid call/return types and indexing values inferred as broad JSON unions.
- pytest: **failed on Linux, Windows, and macOS** — approximately 46 failures per platform, with roughly 7,981–7,986 tests passing and a small number skipped.
- Playwright jobs were cancelled after the workflow became red; they are not evidence of a Playwright pass or failure.

## Failure clusters requiring targeted fixes

1. **Anthropic/OpenAI history conversion and tool boundaries** — multiple tests disagree with the current conversion output around neutral assistant boundaries, empty content, and image/tool results.
2. **Reasoning preservation / replay** — failures in OpenRouter, Z.ai, Fireworks, and chat history transport indicate reasoning blocks are lost or replayed differently than expected.
3. **Groq quota handling** — tests expect request-size/token-quota failures to map to HTTP 400, but the current path returns HTTP 429; the retry/correction test also exhausts its mocked stream.
4. **NVIDIA NIM tool argument aliases** — shared history correction produces upstream HTTP 400 in both streaming modes.
5. **Admin UI/config contract drift** — tests expect reasoning and web-tools sections in Model Config and a reasoning setting in the manifest, but the current static UI/manifest does not match.
6. **Request lifecycle / disconnect handling** — four real-request tests time out while exercising client disconnect and lease cleanup.
7. **Fallback telemetry contract** — the emitted fallback event includes an `automatic: false` field that the test's expected payload omits.
8. **Repository script metadata** — `tests/scripts/test_ci_scripts.py::test_ci_sh_is_tracked_executable` fails because `scripts/ci.sh` is committed as mode `100644`, not executable mode `100755`.

## Recommended repair order

- **P0.1 — Restore deterministic quality gates:** format the three files; correct executable mode for `scripts/ci.sh`; resolve all `ty` diagnostics with proper types rather than suppressions.
- **P0.2 — Fix shared protocol behavior first:** repair conversion/tool-boundary and reasoning replay contracts with focused tests. These failures fan out across several provider adapters.
- **P0.3 — Repair provider-specific contracts:** Groq quota classification/correction and NVIDIA NIM tool argument aliases.
- **P0.4 — Repair runtime lifecycle:** make disconnect cancellation and lease release deterministic, then rerun the request-lifetime tests repeatedly.
- **P0.5 — Align admin UI and configuration contracts:** update implementation and tests together, preserving secret masking and existing settings behavior.
- **P0.6 — Full matrix validation:** run the repository's documented CI scripts and the complete Linux/Windows/macOS + Playwright matrix before merge.

## Safety and compatibility constraints

- Preserve existing Claude Code, Codex, and other harness compatibility.
- Do not weaken tests, remove assertions, or add type-ignore suppressions to make CI green.
- Do not silently change provider error semantics or tool/reasoning payloads; add focused regression coverage.
- Keep credentials out of the repository and logs.
- Keep `main` unchanged until a reviewed PR is ready.

## Important limitation

This report records the observed GitHub baseline; it is not a claim that the failures have been fixed. The current CI logs do not provide enough evidence to safely patch all cross-provider and async lifecycle failures in one blind change. Each cluster needs source-level tracing and focused regression tests before implementation.
