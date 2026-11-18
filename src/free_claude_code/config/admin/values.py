"""Admin config value state and API response assembly."""

from enum import Enum

from free_claude_code.config.custom_providers import REASONING_FORMATS
from free_claude_code.config.loader import ConfigSource, ManagedConfigSnapshot
from free_claude_code.config.personal_setup import (
    ADMIN_PROVIDER_IDS,
    PERSONAL_ADMIN_SECTIONS,
)
from free_claude_code.core.json_types import JsonObject

from .manifest import FIELDS, SECTIONS
from .specs import ConfigFieldSpec, ConfigOptionSpec
from .state import ConfigValueState, ValueState
from .status import provider_config_status

MASKED_SECRET = "********"


def normalize_for_env(value: object) -> str | None:
    """Normalize a submitted Admin value for sparse dotenv persistence."""

    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, tuple) and all(isinstance(item, str) for item in value):
        return ",".join(value)
    return str(value).strip()


def display_value(field: ConfigFieldSpec, value: str | None) -> str | None:
    """Return the Admin UI display value for a canonical config value."""

    if field.secret and value is not None:
        return MASKED_SECRET
    return value


def is_locked_source(source: str | ConfigSource) -> bool:
    """Return whether process ownership makes an Admin field read-only."""

    return str(source) == ConfigSource.PROCESS.value


def load_value_state(snapshot: ManagedConfigSnapshot) -> ValueState:
    """Load effective Admin values from the canonical Settings resolver."""

    managed = snapshot.managed
    state: ValueState = {}
    for field in FIELDS:
        if field.settings_attr is not None:
            value = normalize_for_env(getattr(snapshot.settings, field.settings_attr))
            source = snapshot.sources[field.settings_attr].value
        elif field.key in snapshot.process:
            value = snapshot.process[field.key].strip() or None
            source = ConfigSource.PROCESS.value
        elif field.key in managed:
            value = managed[field.key].strip() or None
            source = ConfigSource.MANAGED.value
        else:
            value = field.resolved_default()
            source = ConfigSource.DEFAULT.value
        state[field.key] = ConfigValueState(value=value, source=source)
    return state


def load_config_response(snapshot: ManagedConfigSnapshot) -> JsonObject:
    """Return manifest and current config values for the Admin UI."""

    state = load_value_state(snapshot)
    fields: list[JsonObject] = []
    for field in FIELDS:
        if field.section_id not in PERSONAL_ADMIN_SECTIONS:
            continue
        if field.section_id == "providers" and not (
            ADMIN_PROVIDER_IDS.intersection(field.provider_ids)
        ):
            continue
        entry = state[field.key]
        source = entry.source
        raw_value = entry.value
        fields.append(
            {
                "key": field.key,
                "label": field.label,
                "section": field.section_id,
                "type": field.field_type,
                "value": display_value(field, raw_value),
                "configured": raw_value is not None,
                "source": source,
                "locked": is_locked_source(source),
                "nullable": field.nullable,
                "secret": field.secret,
                "advanced": field.advanced,
                "restart_required": field.restart_required,
                "session_sensitive": field.session_sensitive,
                "options": [
                    (
                        {"value": option.value, "label": option.label}
                        if isinstance(option, ConfigOptionSpec)
                        else {"value": option, "label": option}
                    )
                    for option in field.options
                ],
                "description": field.description,
            }
        )

    return {
        "sections": [
            {
                "id": section.section_id,
                "label": section.label,
                "description": section.description,
                "advanced": section.advanced,
            }
            for section in SECTIONS
            if section.section_id in PERSONAL_ADMIN_SECTIONS
        ],
        "fields": fields,
        "paths": {"managed": str(snapshot.path)},
        "provider_status": [
            provider
            for provider in provider_config_status(state)
            if provider["provider_id"] in ADMIN_PROVIDER_IDS
        ],
        "custom_providers": [
            {
                "provider_id": provider.provider_id,
                "display_name": provider.display_name,
                "kind": "custom",
                "status": "configured",
                "label": "Configured",
                "base_url": provider.base_url,
                "api_format": provider.api_format,
                "reasoning_format": provider.reasoning_format,
                "reasoning_history_format": provider.reasoning_history_format,
                "model_ids": list(provider.model_ids),
                "api_key": (MASKED_SECRET if provider.api_key is not None else None),
                "settings_keys": [],
            }
            for provider in snapshot.settings.custom_providers
        ],
        "custom_providers_locked": snapshot.sources["custom_providers"]
        == ConfigSource.PROCESS,
        "custom_reasoning_formats": {
            key: list(values) for key, values in REASONING_FORMATS.items()
        },
    }
