"""Model-facing plan generation: prompt builder and response parser.

The run engine (``terminal.run``) consumes plans as data. This module is
the seam between a model and that schema: it builds the exact prompt a
model should answer, and turns the model's text back into a validated
``RunPlan``. It never talks to a provider itself - a ``PlanModelPort``
callable supplies the model response, so the generator stays testable
and any provider adapter (or a pasted response) can drive it.
"""

import json
from collections.abc import Callable
from typing import Any

from free_claude_code.terminal.run import PlanError, RunPlan

#: A model adapter: prompt in, raw text out.
PlanModelPort = Callable[[str], str]

_SCHEMA_HELP = """\
{
  "goal": "one sentence naming the outcome",
  "steps": [
    {
      "id": "short-unique-id",
      "title": "what this step does",
      "argv": ["executable", "arg1", "arg2"],
      "cwd": "optional working directory (must stay inside the workspace)",
      "timeout_seconds": 300,
      "verify": {"argv": ["read-only", "check"], "expect_exit": 0}
    }
  ]
}"""

_RULES = """\
Rules:
- argv is always an argument vector; never a shell command string, never
  rely on shell features (pipes, redirection, globbing, && or ;).
- Prefer the fewest steps that achieve the goal; read-only inspection
  steps first, changes after.
- Every mutating step should carry a verify command that proves its
  postcondition; verify commands must be read-only.
- Commands are classified by risk: read-only runs automatically,
  privileged or destructive commands pause for human approval - use them
  only when the goal requires it.
- Never include package installs, force pushes, deletes of data, or cloud
  mutations unless the task explicitly asks for them.
- Use absolute paths inside the workspace or omit cwd to use the
  workspace root.
- Step ids must be unique short strings."""


def build_plan_prompt(
    task: str, *, workspace: str | None = None, context: str | None = None
) -> str:
    """The exact prompt a model should answer with one JSON plan."""

    where = f"\nThe workspace root is: {workspace}" if workspace else ""
    observed = (
        f"\n\nObserved environment (read-only facts):\n{context}" if context else ""
    )
    return (
        "You are planning work for a policy-enforced terminal agent. Produce "
        "a plan as ONE JSON object and nothing else (no prose, no Markdown "
        "fences).\n"
        f"Task: {task}{where}{observed}\n\n"
        f"The plan must match this shape:\n{_SCHEMA_HELP}\n\n{_RULES}\n"
    )


def extract_plan_json(text: str) -> dict[str, Any]:
    """Pull the plan object out of a model response.

    Accepts a bare JSON object or one wrapped in a single fenced code
    block; anything else (prose around the JSON, multiple objects) is a
    PlanError so the caller can re-ask the model.
    """

    stripped = text.strip()
    if not stripped:
        raise PlanError("model response is empty")
    candidate = stripped
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines[-1].strip() != "```" or len(lines) < 3:
            raise PlanError("fenced response is not closed")
        candidate = "\n".join(lines[1:-1]).strip()
    try:
        data = json.loads(candidate)
    except ValueError:
        # Tolerate leading prose before the first '{' only if the object
        # then runs to the end of the response.
        start = candidate.find("{")
        if start < 0:
            raise PlanError("model response contains no JSON object") from None
        try:
            data = json.loads(candidate[start:])
        except ValueError:
            raise PlanError("model response is not valid JSON") from None
    if not isinstance(data, dict):
        raise PlanError("model response JSON must be an object")
    return data


def plan_from_response(text: str) -> RunPlan:
    """Validate a model response into a RunPlan (raises PlanError)."""

    return RunPlan.from_dict(extract_plan_json(text))


def propose_plan(
    task: str,
    model: PlanModelPort,
    *,
    workspace: str | None = None,
    context: str | None = None,
) -> RunPlan:
    """One round-trip: build the prompt, call the port, validate the plan."""

    return plan_from_response(
        model(build_plan_prompt(task, workspace=workspace, context=context))
    )
