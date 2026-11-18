"""OpenRouter provider implementation."""

import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from free_claude_code.application.model_metadata import ProviderModelInfo
from free_claude_code.config.constants import ANTHROPIC_DEFAULT_MAX_OUTPUT_TOKENS
from free_claude_code.core.anthropic import ReasoningReplayMode
from free_claude_code.core.diagnostics import extract_upstream_error_detail
from free_claude_code.core.failures import ExecutionFailure, FailureKind
from free_claude_code.core.reasoning import ReasoningEffort, ReasoningPolicy
from free_claude_code.providers.admission import ProviderAdmissionController
from free_claude_code.providers.base import ProviderConfig
from free_claude_code.providers.model_listing import extract_tool_capable_model_infos
from free_claude_code.providers.openai_chat import (
    OpenAIChatBehavior,
    OpenAIChatProfile,
    OpenAIChatProvider,
    OpenAIChatRequestPolicy,
    ReasoningObject,
    validate_extra_body_does_not_override_canonical_fields,
)
from free_claude_code.providers.reasoning_compatibility import (
    reasoning_control_rejected,
)

_REQUEST_POLICY = OpenAIChatRequestPolicy(
    provider_name="OPENROUTER",
    reasoning_replay=ReasoningReplayMode.REASONING_CONTENT,
    include_extra_body=True,
    extra_body_validator=validate_extra_body_does_not_override_canonical_fields,
    default_max_tokens=ANTHROPIC_DEFAULT_MAX_OUTPUT_TOKENS,
)


class OpenRouterChatBehavior(OpenAIChatBehavior):
    """OpenRouter Chat adaptation without HTTP ownership."""

    def finalize_chat_body(
        self, body: dict[str, Any], *, reasoning: ReasoningPolicy
    ) -> dict[str, Any]:
        """Drop regex lookarounds Cohere rejects in tool parameter schemas."""
        model = body.get("model")
        if isinstance(model, str) and model.casefold().startswith("cohere/"):
            body = deepcopy(body)
            messages = body.get("messages")
            if isinstance(messages, list):
                for message in messages:
                    if not isinstance(message, dict) or message.get("tool_calls"):
                        continue
                    content = message.get("content")
                    if content is None or content == "" or content == []:
                        # Cohere rejects empty chat messages, including otherwise
                        # valid empty tool results. OpenAI-compatible APIs accept
                        # an empty string, so provide a minimal neutral placeholder.
                        message["content"] = " "
            tools = body.get("tools")
            if isinstance(tools, list):
                for tool in tools:
                    if not isinstance(tool, dict):
                        continue
                    function = tool.get("function")
                    if isinstance(function, dict):
                        _remove_cohere_lookarounds(function.get("parameters"))
        return body

    def failure_override(self, error: Exception) -> ExecutionFailure | None:
        if getattr(error, "status_code", None) != 404:
            return None
        body = getattr(error, "body", None)
        if isinstance(body, Mapping) and "error" in body:
            if any(field in body for field in ("code", "message", "metadata")):
                return None
            body = body["error"]
        if not isinstance(body, Mapping):
            return None
        code = body.get("code")
        if not isinstance(code, int) or code != 404:
            return None
        if not isinstance(body.get("message"), str):
            return None
        metadata = body.get("metadata")
        if (
            not isinstance(metadata, Mapping)
            or metadata.get("failed_routing_step") != "Filter by Image Support"
        ):
            return None
        return ExecutionFailure(
            FailureKind.INVALID_REQUEST,
            400,
            "No OpenRouter endpoint for this request supports image input. "
            "Remove the image or choose an image-capable model.",
            False,
        )

    def reasoning_disable_rejected(self, error: Exception) -> bool:
        detail = extract_upstream_error_detail(error)
        if detail.status_code not in {None, 200}:
            return False
        try:
            payload = json.loads(detail.body_text or "")
        except ValueError:
            return False
        if not isinstance(payload, dict):
            return False
        error_body = payload.get("error", payload)
        if not isinstance(error_body, dict):
            return False
        code = error_body.get("code")
        return (
            isinstance(code, int)
            and code == 400
            and reasoning_control_rejected(error_body, ("reasoning",))
        )


class OpenRouterProvider(OpenAIChatProvider):
    """OpenRouter provider using the OpenAI-compatible Chat Completions API."""

    def __init__(
        self, config: ProviderConfig, *, admission: ProviderAdmissionController
    ):
        super().__init__(
            config,
            behavior=OpenRouterChatBehavior(_PROFILE),
            admission=admission,
        )

    async def list_model_infos(self) -> frozenset[ProviderModelInfo]:
        """Advertise OpenRouter tool models with reasoning capability metadata."""
        payload = await self._list_models_payload()
        return extract_tool_capable_model_infos(
            payload, provider_name=self._provider_name
        )


_PROFILE = OpenAIChatProfile(
    _REQUEST_POLICY,
    ReasoningObject(tuple((effort, effort.value) for effort in ReasoningEffort)),
    structured_reasoning_details=True,
)


def _remove_cohere_lookarounds(value: object) -> None:
    """Remove unsupported regex lookarounds from outgoing JSON Schemas only."""
    if isinstance(value, list):
        for item in value:
            _remove_cohere_lookarounds(item)
        return
    if not isinstance(value, dict):
        return
    pattern = value.get("pattern")
    if isinstance(pattern, str) and any(
        marker in pattern for marker in ("(?=", "(?!", "(?<=", "(?<!")
    ):
        value.pop("pattern")
    for key, child in value.items():
        if key in {
            "properties",
            "patternProperties",
            "$defs",
            "definitions",
            "dependencies",
        }:
            if isinstance(child, dict):
                for subschema in child.values():
                    _remove_cohere_lookarounds(subschema)
        elif key in {
            "additionalProperties",
            "additionalItems",
            "unevaluatedProperties",
            "unevaluatedItems",
            "items",
            "contains",
            "propertyNames",
            "if",
            "then",
            "else",
            "not",
            "contentSchema",
            "allOf",
            "anyOf",
            "oneOf",
            "prefixItems",
            "dependentSchemas",
        }:
            _remove_cohere_lookarounds(child)
