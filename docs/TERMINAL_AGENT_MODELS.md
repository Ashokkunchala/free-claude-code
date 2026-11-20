# Terminal agent plan models

`fcc-run --task` and `fcc-shell` plan through any OpenAI-compatible chat
endpoint. Configuration is environment-only; no account or config file is
required, and credentials never appear in error messages.

## Configuration

| Variable | Required | Meaning |
|---|---|---|
| `FCC_PLAN_BASE_URL` | yes | Base URL of the endpoint, or the alias `ollama` |
| `FCC_PLAN_MODEL` | yes | Model name the endpoint expects |
| `FCC_PLAN_API_KEY` | no | Bearer token; omit for keyless/local endpoints |
| `FCC_PLAN_TIMEOUT` | no | Request timeout in seconds (default 120) |
| `FCC_PLAN_FALLBACKS` | no | Comma-separated `base_url|model[|api_key]` entries |

## Local and offline operation

For private or offline tasks, run a local server and point the planner at
it. With Ollama:

```sh
ollama serve
ollama pull qwen2.5-coder
export FCC_PLAN_BASE_URL=ollama   # alias for http://localhost:11434/v1
export FCC_PLAN_MODEL=qwen2.5-coder
```

A local endpoint needs no API key. Planning then runs entirely on the
machine; whether it works offline depends on the local server and model,
not on FCC. Prompts and task details leave the machine only when the
configured endpoint is remote.

## Fallback chains

`FCC_PLAN_FALLBACKS` adds backup endpoints, tried in order when the
primary fails:

```sh
export FCC_PLAN_FALLBACKS="ollama|qwen2.5-coder,https://api.example.com/v1|planner-2|token"
```

When every leg fails, the error names each leg with its own failure.

## Rate limits and quota

FCC does not provide hosted inference and makes no claim of unlimited
free capacity: availability, quota and rate limits belong to whichever
endpoint you configure. When an endpoint answers HTTP 429, the planner
says so plainly ("rate limited or out of quota") and reports the
provider's Retry-After hint when one was given. If every leg of a
fallback chain is throttled, the error reports the earliest hinted
backoff.

## Model pools by task needs

`FCC_PLAN_PROFILES` replaces the primary config with a named pool, so each
call is routed by prompt size and availability (roadmap section 9). Entries
are semicolon-separated `label=base_url|model|budget[|api_key]`, where
`budget` is the prompt size in characters the leg accepts, or `unbounded`:

```sh
export FCC_PLAN_PROFILES="fast=ollama|qwen2.5-coder|8000; deep=https://api.example.com/v1|planner-2|unbounded|token"
```

A call goes to the smallest-budget leg that fits the prompt: small prompts
stay on the small, fast or local model, large prompts route to the
large-context endpoint, and an unbounded leg catches anything. A leg that
fails (including rate limits) falls through to the next fitting leg; when
every fitting leg fails, the error names each attempt, and an
all-rate-limited failure reports the earliest hinted backoff. A prompt
that fits no leg fails honestly and says to raise a budget or add an
unbounded leg.

When `FCC_PLAN_PROFILES` is set, `FCC_PLAN_BASE_URL` and `FCC_PLAN_MODEL`
are not required; `FCC_PLAN_TIMEOUT` applies to every leg, and base URLs
accept the same local aliases (`ollama`).
