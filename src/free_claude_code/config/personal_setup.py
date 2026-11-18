"""Admin UI provider visibility and automatic free-fallback provider set."""

from free_claude_code.config.provider_catalog import PROVIDER_CATALOG

PERSONAL_PROVIDER_IDS = frozenset({"gemini", "groq", "nvidia_nim", "open_router"})
ADMIN_PROVIDER_IDS = frozenset(PROVIDER_CATALOG)
PERSONAL_ADMIN_SECTIONS = frozenset({"providers", "models"})
