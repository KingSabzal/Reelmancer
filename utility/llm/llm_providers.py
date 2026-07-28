"""LLM provider registry.

The user picks exactly one provider (9Router, OpenRouter, NVIDIA NIM or Cloudflare
Workers AI). There is no fallback between providers. Inside the chosen provider the
models are discovered dynamically and fall back to one another automatically.
"""

from __future__ import annotations

from typing import Any, Dict, List

PROVIDERS: Dict[str, Dict[str, Any]] = {
    "9router": {
        "id": "9router",
        "name": "9Router (local)",
        "description": "Your local 9Router instance. Fast and private.",
        "fields": [
            {"key": "router9_url", "label": "Base URL", "type": "text",
             "placeholder": "http://localhost:9000/v1", "required": True},
            {"key": "router9_key", "label": "API key", "type": "password", "required": False},
        ],
        "signup": "",
        "notes": "Point this at the OpenAI-compatible base URL of your 9Router install.",
    },
    "openrouter": {
        "id": "openrouter",
        "name": "OpenRouter",
        "description": "Hundreds of models from many labs. Free models are tried first.",
        "fields": [
            {"key": "openrouter_key", "label": "API key", "type": "password", "required": True,
             "placeholder": "sk-or-v1-..."},
        ],
        "signup": "https://openrouter.ai/keys",
        "notes": "Free models (price 0) are prioritised, then the cheapest paid ones.",
    },
    "nvidia": {
        "id": "nvidia",
        "name": "NVIDIA NIM",
        "description": "NVIDIA-hosted open models with a generous free tier.",
        "fields": [
            {"key": "nvidia_nim_key", "label": "API key", "type": "password", "required": True,
             "placeholder": "nvapi-..."},
            {"key": "nvidia_nim_url", "label": "Base URL", "type": "text",
             "placeholder": "https://integrate.api.nvidia.com/v1", "required": True},
        ],
        "signup": "https://build.nvidia.com/",
        "notes": "Uses the OpenAI-compatible NVIDIA endpoint.",
    },
    "cloudflare": {
        "id": "cloudflare",
        "name": "Cloudflare Workers AI",
        "description": "Edge inference with dozens of models and 10,000 free neurons per day.",
        "fields": [
            {"key": "cloudflare_account_id", "label": "Account ID", "type": "text",
             "required": True, "placeholder": "023e105f4ecef8ad9ca31a8372d0c353"},
            {"key": "cloudflare_api_token", "label": "API token", "type": "password",
             "required": True, "placeholder": "Workers AI token"},
        ],
        "signup": "https://dash.cloudflare.com/profile/api-tokens",
        "notes": (
            "Find the Account ID on the right side of any Cloudflare dashboard page. "
            "Create a token with the 'Workers AI' permission. Text models are discovered "
            "automatically from the Workers AI catalog."
        ),
    },
}

DEFAULT_PROVIDER = "openrouter"


def list_providers() -> List[str]:
    """Return every provider id."""
    return list(PROVIDERS.keys())


def provider_labels() -> Dict[str, str]:
    """Map provider id to its display name."""
    return {pid: cfg["name"] for pid, cfg in PROVIDERS.items()}


def get_provider(provider_id: str) -> Dict[str, Any]:
    """Return a provider definition, defaulting to OpenRouter."""
    return PROVIDERS.get(provider_id, PROVIDERS[DEFAULT_PROVIDER])


def missing_fields(provider_id: str, config) -> List[str]:
    """Return the labels of required fields that are still empty."""
    provider = get_provider(provider_id)
    gaps = []
    for field in provider["fields"]:
        if field.get("required") and not (config.get(field["key"]) or "").strip():
            gaps.append(field["label"])
    return gaps
