"""SmartLLMRouter: one user-selected provider, automatic fallback across its models.

The user chooses a single provider in Settings (9Router, OpenRouter, NVIDIA NIM or
Cloudflare Workers AI). This module never falls back to a different provider. Inside
the chosen provider the model list is discovered dynamically and every model is tried
in priority order until one answers.

No model name is hardcoded anywhere.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

from utility.core.config_manager import get_config
from utility.llm.llm_providers import DEFAULT_PROVIDER, get_provider

LOGGER = logging.getLogger("llm_router")
if not LOGGER.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    LOGGER.addHandler(_handler)
LOGGER.setLevel(logging.INFO)

TIMEOUT_SECONDS = 30
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
CIRCUIT_BREAKER_LIMIT = 3

# Model ids containing these markers cannot do chat completions.
NON_TEXT_MARKERS = (
    "whisper", "bge-", "embedding", "embeddinggemma", "stable-diffusion", "dreamshaper",
    "flux", "resnet", "detr", "melotts", "aura", "bart-large-cnn", "m2m100", "llama-guard",
    "uform", "llava", "img2img", "inpainting", "text-to-image", "speech", "tts", "rerank",
    "moondream", "segment", "upscal", "sdxl",
)


class InvalidAPIKeyError(Exception):
    """Raised on an authentication failure so the UI can stop and warn the user."""


class AllProvidersFailedError(Exception):
    """Raised when every model of the selected provider failed."""


class ProviderNotConfiguredError(Exception):
    """Raised when the selected provider is missing required credentials."""


@dataclass
class ModelEntry:
    """A single candidate model in the routing queue."""

    provider: str
    model_id: str
    endpoint: str
    api_key: str
    price: float = 0.0
    extra_headers: Dict[str, str] = field(default_factory=dict)
    payload_style: str = "openai"  # openai | cloudflare

    def label(self) -> str:
        """Identifier used by the circuit breaker and the logs."""
        return f"{self.provider}:{self.model_id}"


class CircuitBreaker:
    """Removes a model from the queue after N consecutive failures in a session."""

    def __init__(self, limit: int = CIRCUIT_BREAKER_LIMIT):
        self.limit = limit
        self._failures: Dict[str, int] = {}

    def record_failure(self, key: str) -> None:
        """Count a failure and open the breaker at the limit."""
        self._failures[key] = self._failures.get(key, 0) + 1
        if self._failures[key] >= self.limit:
            LOGGER.warning(
                "Circuit breaker opened for %s after %d consecutive failures.", key, self.limit
            )

    def record_success(self, key: str) -> None:
        """Reset the failure counter for a model."""
        self._failures.pop(key, None)

    def is_open(self, key: str) -> bool:
        """True when the model is temporarily disabled."""
        return self._failures.get(key, 0) >= self.limit

    def reset(self) -> None:
        """Clear all recorded failures."""
        self._failures.clear()


def extract_json(text: str) -> Optional[Any]:
    """Extract the largest valid JSON object or array from arbitrary model output."""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    candidates: List[str] = []
    for opener, closer in (("{", "}"), ("[", "]")):
        stack: List[int] = []
        for index, char in enumerate(cleaned):
            if char == opener:
                stack.append(index)
            elif char == closer and stack:
                start = stack.pop()
                candidates.append(cleaned[start : index + 1])
    candidates.sort(key=len, reverse=True)
    for candidate in candidates:
        for attempt in (candidate, _repair_json(candidate)):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                continue
    return None


def _repair_json(text: str) -> str:
    """Best-effort repair of common JSON mistakes made by language models."""
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


def is_text_model(model_id: str, task: str = "") -> bool:
    """True when a model id looks like a text generation model."""
    haystack = f"{model_id} {task}".lower()
    if task and "text generation" in task.lower():
        return True
    return not any(marker in haystack for marker in NON_TEXT_MARKERS)


class SmartLLMRouter:
    """Routes completions to the selected provider, cycling through its models."""

    def __init__(self, config=None):
        self.config = config or get_config()
        self.breaker = CircuitBreaker()
        self._queue_cache: Dict[str, List[ModelEntry]] = {}
        self.last_used_model: Optional[str] = None

    # ------------------------------------------------------------------
    @property
    def provider_id(self) -> str:
        """The provider currently selected by the user."""
        return self.config.get("llm_provider", DEFAULT_PROVIDER) or DEFAULT_PROVIDER

    # ------------------------------------------------------------------
    # Model discovery, one method per provider
    # ------------------------------------------------------------------
    def _models_9router(self) -> List[ModelEntry]:
        """Discover the models served by the local 9Router instance."""
        base_url = (self.config.get("router9_url") or "").rstrip("/")
        api_key = self.config.get("router9_key") or ""
        if not base_url:
            raise ProviderNotConfiguredError("9Router base URL is not set. Open Settings.")
        response = requests.get(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=TIMEOUT_SECONDS,
        )
        if response.status_code in (401, 403):
            raise InvalidAPIKeyError("9Router rejected the API key.")
        response.raise_for_status()
        entries = []
        for item in response.json().get("data", []):
            model_id = item.get("id")
            if model_id and is_text_model(model_id):
                entries.append(ModelEntry("9router", model_id, f"{base_url}/chat/completions", api_key))
        return entries

    def _models_openrouter(self) -> List[ModelEntry]:
        """Discover OpenRouter models, free first then cheapest."""
        api_key = self.config.get("openrouter_key") or ""
        if not api_key:
            raise ProviderNotConfiguredError("OpenRouter API key is not set. Open Settings.")
        base_url = "https://openrouter.ai/api/v1"
        response = requests.get(f"{base_url}/models", timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        entries: List[ModelEntry] = []
        for item in response.json().get("data", []):
            model_id = item.get("id")
            if not model_id or not is_text_model(model_id):
                continue
            pricing = item.get("pricing") or {}
            try:
                price = float(pricing.get("prompt", 0) or 0) + float(pricing.get("completion", 0) or 0)
            except (TypeError, ValueError):
                price = 1.0
            entries.append(
                ModelEntry(
                    "openrouter", model_id, f"{base_url}/chat/completions", api_key, price=price,
                    extra_headers={
                        "HTTP-Referer": "http://localhost:8501",
                        "X-Title": "Reelmancer",
                    },
                )
            )
        entries.sort(key=lambda entry: (entry.price > 0, entry.price))
        return entries

    def _models_nvidia(self) -> List[ModelEntry]:
        """Discover the models available on NVIDIA NIM."""
        api_key = self.config.get("nvidia_nim_key") or ""
        base_url = (self.config.get("nvidia_nim_url") or "").rstrip("/")
        if not api_key or not base_url:
            raise ProviderNotConfiguredError("NVIDIA NIM key or URL is not set. Open Settings.")
        response = requests.get(
            f"{base_url}/models", headers={"Authorization": f"Bearer {api_key}"}, timeout=TIMEOUT_SECONDS
        )
        if response.status_code in (401, 403):
            raise InvalidAPIKeyError("NVIDIA NIM rejected the API key.")
        response.raise_for_status()
        entries = []
        for item in response.json().get("data", []):
            model_id = item.get("id")
            if model_id and is_text_model(model_id):
                entries.append(ModelEntry("nvidia", model_id, f"{base_url}/chat/completions", api_key))
        return entries

    def _models_cloudflare(self) -> List[ModelEntry]:
        """Discover Cloudflare Workers AI text models through the model search API."""
        account_id = (self.config.get("cloudflare_account_id") or "").strip()
        token = (self.config.get("cloudflare_api_token") or "").strip()
        if not account_id or not token:
            raise ProviderNotConfiguredError(
                "Cloudflare Account ID or API token is not set. Open Settings."
            )
        search_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/models/search"
        headers = {"Authorization": f"Bearer {token}"}
        entries: List[ModelEntry] = []
        page = 1
        while page <= 5:  # the catalog is small; guard against runaway pagination
            response = requests.get(
                search_url,
                headers=headers,
                params={"task": "Text Generation", "per_page": 100, "page": page,
                        "hide_experimental": "false"},
                timeout=TIMEOUT_SECONDS,
            )
            if response.status_code in (400, 401, 403):
                body = response.text.lower()
                if "auth" in body or response.status_code in (401, 403):
                    raise InvalidAPIKeyError(
                        "Cloudflare rejected the credentials. Check the Account ID and API token."
                    )
            response.raise_for_status()
            payload = response.json()
            results = payload.get("result") or []
            if not results:
                break
            for item in results:
                model_id = item.get("name")
                task = ((item.get("task") or {}).get("name")) or ""
                if not model_id or not is_text_model(model_id, task):
                    continue
                entries.append(
                    ModelEntry(
                        "cloudflare",
                        model_id,
                        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions",
                        token,
                        payload_style="openai",
                    )
                )
            info = payload.get("result_info") or {}
            total_pages = info.get("total_pages") or 1
            if page >= total_pages:
                break
            page += 1

        # Prefer larger instruct models first: they follow the JSON prompts more reliably.
        def rank(entry: ModelEntry) -> tuple:
            lowered = entry.model_id.lower()
            instruct = 0 if "instruct" in lowered or "chat" in lowered else 1
            size = 0
            match = re.search(r"(\d+)\s*b\b", lowered)
            if match:
                size = -int(match.group(1))
            return (instruct, size)

        entries.sort(key=rank)
        return entries

    # ------------------------------------------------------------------
    def build_queue(self, refresh: bool = False) -> List[ModelEntry]:
        """Build the ordered model queue for the selected provider only."""
        provider = self.provider_id
        if refresh:
            self._queue_cache.pop(provider, None)
        if provider not in self._queue_cache:
            discovery = {
                "9router": self._models_9router,
                "openrouter": self._models_openrouter,
                "nvidia": self._models_nvidia,
                "cloudflare": self._models_cloudflare,
            }.get(provider)
            if discovery is None:
                raise ProviderNotConfiguredError(f"Unknown provider: {provider}")
            entries = discovery()
            if not entries:
                raise ProviderNotConfiguredError(
                    f"{get_provider(provider)['name']} returned no usable text models."
                )
            self._queue_cache[provider] = entries
            LOGGER.info(
                "%s: %d text models discovered for automatic fallback.",
                get_provider(provider)["name"], len(entries),
            )
        return self._queue_cache[provider]

    def available_models(self, refresh: bool = False) -> List[str]:
        """Return the model ids discovered for the selected provider."""
        return [entry.model_id for entry in self.build_queue(refresh)]

    # ------------------------------------------------------------------
    def _call_model(
        self, entry: ModelEntry, messages: List[Dict[str, str]], temperature: float, max_tokens: int
    ) -> str:
        """Send one chat completion request to a specific model."""
        headers = {"Content-Type": "application/json", **entry.extra_headers}
        if entry.api_key:
            headers["Authorization"] = f"Bearer {entry.api_key}"
        body = {
            "model": entry.model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        response = requests.post(entry.endpoint, headers=headers, json=body, timeout=TIMEOUT_SECONDS)

        if response.status_code in (401, 403):
            raise InvalidAPIKeyError(
                f"Invalid credentials for {get_provider(entry.provider)['name']}. Fix them in Settings."
            )
        if response.status_code in RETRYABLE_STATUS:
            raise requests.HTTPError(f"HTTP {response.status_code}")
        if not response.ok:
            # A single model may be unsupported on this endpoint; move to the next one.
            raise requests.HTTPError(f"HTTP {response.status_code}: {response.text[:120]}")

        payload = response.json()
        # Cloudflare wraps OpenAI-shaped payloads in {"result": ...} on some routes.
        if "choices" not in payload and isinstance(payload.get("result"), dict):
            payload = payload["result"]
        if "choices" in payload:
            message = payload["choices"][0].get("message") or {}
            return message.get("content") or payload["choices"][0].get("text", "")
        if isinstance(payload.get("response"), str):
            return payload["response"]
        raise ValueError("Unrecognised response shape")

    def complete(
        self,
        prompt: str,
        system: str = "You are a helpful assistant.",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        max_models: int = 15,
    ) -> str:
        """Run a completion, cycling through the provider's models until one answers."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        queue = [e for e in self.build_queue() if not self.breaker.is_open(e.label())]
        if not queue:
            raise AllProvidersFailedError(
                "Every model of the selected provider is temporarily disabled. "
                "Refresh the model list in Settings."
            )

        provider_name = get_provider(self.provider_id)["name"]
        errors: List[str] = []
        for index, entry in enumerate(queue[:max_models]):
            try:
                text = self._call_model(entry, messages, temperature, max_tokens)
                if text and text.strip():
                    self.breaker.record_success(entry.label())
                    self.last_used_model = entry.label()
                    LOGGER.info("Response received from %s.", entry.label())
                    return text
                raise ValueError("Empty response")
            except InvalidAPIKeyError:
                raise
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError,
                    ValueError, KeyError, json.JSONDecodeError) as exc:
                reason = str(exc) or exc.__class__.__name__
                self.breaker.record_failure(entry.label())
                errors.append(f"{entry.model_id}: {reason}")
                next_label = queue[index + 1].model_id if index + 1 < len(queue) else "no further models"
                LOGGER.warning(
                    "Model %s failed (reason: %s), automatically falling back to %s...",
                    entry.model_id, reason, next_label,
                )
                time.sleep(0.15)
        raise AllProvidersFailedError(
            f"All {provider_name} models failed: " + " | ".join(errors[-5:])
        )

    def complete_json(
        self,
        prompt: str,
        system: str = "You output strictly valid JSON and nothing else.",
        temperature: float = 0.7,
        required_fields: Optional[List[str]] = None,
        retries: int = 3,
        max_tokens: int = 4096,
    ) -> Any:
        """Completion with resilient JSON parsing, validation and lowered-temperature retries."""
        last_error = "unknown"
        current_temperature = temperature
        for attempt in range(1, retries + 1):
            try:
                raw = self.complete(
                    prompt, system=system, temperature=current_temperature, max_tokens=max_tokens
                )
                data = extract_json(raw)
                if data is None:
                    raise ValueError("No JSON block found in the model response.")
                if required_fields:
                    missing = [
                        name for name in required_fields
                        if not (isinstance(data, dict) and data.get(name))
                    ]
                    if missing:
                        raise ValueError(f"Missing or empty fields: {missing}")
                return data
            except InvalidAPIKeyError:
                raise
            except Exception as exc:  # noqa: BLE001 - retry on any parsing or model issue
                last_error = str(exc)
                LOGGER.warning("JSON attempt %d/%d failed: %s", attempt, retries, last_error)
                current_temperature = 0.3
        raise AllProvidersFailedError(f"Could not obtain valid JSON: {last_error}")


_ROUTER: Optional[SmartLLMRouter] = None


def get_router() -> SmartLLMRouter:
    """Return the process-wide router singleton."""
    global _ROUTER
    if _ROUTER is None:
        _ROUTER = SmartLLMRouter()
    return _ROUTER


def reset_router() -> None:
    """Drop the cached router so a provider change takes effect immediately."""
    global _ROUTER
    _ROUTER = None
