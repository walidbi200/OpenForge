"""Generic LLM provider abstraction for OpenForge.

Supports multiple OpenAI-compatible chat completion providers.
All agents call ``call_llm()`` from this module — they never know which
backend is active.

Provider selection via environment variables::

    LLM_PROVIDER=openrouter  (default)
    LLM_FALLBACK=dahl
"""

from __future__ import annotations

import json
import logging
import time
import typing
from dataclasses import dataclass, field

import httpx

from openforge.config import Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider defaults
# ---------------------------------------------------------------------------

_PROVIDERS: dict[str, dict[str, str]] = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "anthropic/claude-sonnet-4.6",
    },
    "dahl": {
        "base_url": "https://inference.dahl.global/v1",
        "default_model": "moonshotai/Kimi-K2.6",
    },
}

# Transient HTTP status codes that are safe to retry.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_RETRY_BACKOFF = [1.0, 2.0, 4.0]


# ---------------------------------------------------------------------------
# Resolved provider config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMProviderConfig:
    """Resolved configuration for a single provider."""
    provider: str
    base_url: str
    api_key: str | None
    model: str

    @classmethod
    def from_provider_name(cls, provider: str, settings: Settings) -> "LLMProviderConfig":
        """Build config for a specific provider name."""
        if provider not in _PROVIDERS:
            logger.warning("Unknown provider %r, falling back to 'openrouter'", provider)
            provider = "openrouter"

        defaults = _PROVIDERS[provider]
        base_url = (settings.api_base or defaults["base_url"]).rstrip("/")

        if provider == "openrouter":
            provider_key: str | None = settings.openrouter_api_key
        elif provider == "dahl":
            provider_key = settings.dahl_api_key
        else:
            provider_key = None

        api_key = settings.api_key or provider_key or None

        if provider == "openrouter":
            provider_model = settings.openrouter_model
        elif provider == "dahl":
            provider_model = settings.dahl_model
        else:
            provider_model = defaults["default_model"]

        model = settings.model or provider_model

        return cls(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model=model,
        )

    @property
    def chat_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)


class RetryManager:
    """Centralized retry controller managing backoff, budgets, and provider failover."""
    def __init__(self, settings: Settings, timeout: float):
        self.settings = settings
        self.timeout = timeout
        
        # Primary provider
        self.primary_config = LLMProviderConfig.from_provider_name(settings.llm_provider, settings)
        
        # Fallback provider
        self.fallback_config = None
        if settings.llm_fallback:
            self.fallback_config = LLMProviderConfig.from_provider_name(settings.llm_fallback, settings)
            if not self.fallback_config.enabled:
                self.fallback_config = None
                
        self.current_config = self.primary_config
        self.retry_count = 0
        self.failover_count = 0
        self.start_time = time.time()
        self.timeout_status = False
        self.termination_reason = "success"

    def execute(self, messages: list[dict], extra: dict, metrics_callback: typing.Callable[[dict], None] | None) -> str:
        if not self.primary_config.enabled:
            raise RuntimeError(f"No API key configured for provider '{self.primary_config.provider}'.")

        prompt_tokens = sum(len(m.get("content", "")) // 4 for m in messages)
        last_exc: Exception | None = None
        
        for failover_attempt in range(2): # Try primary, then fallback
            for attempt in range(_MAX_RETRIES):
                try:
                    payload = {
                        "model": self.current_config.model,
                        "messages": messages,
                        "stream": True,
                        **extra,
                    }
                    headers = {
                        "Authorization": f"Bearer {self.current_config.api_key}",
                        "Content-Type": "application/json",
                    }
                    
                    time_to_first_token = 0.0
                    generated_text = ""
                    request_id = None
                    
                    with httpx.Client(timeout=self.timeout) as client:
                        with client.stream("POST", self.current_config.chat_url, headers=headers, json=payload) as response:
                            if response.status_code in _RETRYABLE_STATUS or response.status_code in (401, 403, 429):
                                response.read()
                                if response.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES - 1:
                                    self._backoff(attempt, response.status_code)
                                    continue
                                else:
                                    # Need to failover
                                    raise httpx.HTTPStatusError(f"HTTP {response.status_code}", request=response.request, response=response)
                            
                            response.raise_for_status()
                            
                            for line in response.iter_lines():
                                if not line: continue
                                if line.startswith("data: "):
                                    data_str = line[6:]
                                    if data_str.strip() == "[DONE]":
                                        break
                                    try:
                                        chunk = json.loads(data_str)
                                        if not request_id and "id" in chunk:
                                            request_id = chunk["id"]
                                        if not time_to_first_token:
                                            time_to_first_token = time.time() - self.start_time
                                        delta = chunk["choices"][0].get("delta", {})
                                        if "content" in delta:
                                            generated_text += delta["content"]
                                    except (json.JSONDecodeError, KeyError, IndexError):
                                        pass
                                        
                    completion_tokens = len(generated_text) // 4
                    generation_duration = time.time() - self.start_time - time_to_first_token
                    
                    if metrics_callback:
                        metrics_callback({
                            "provider_used": self.current_config.provider,
                            "fallback_provider": self.fallback_config.provider if self.fallback_config else None,
                            "model": self.current_config.model,
                            "request_id": request_id,
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "total_tokens": prompt_tokens + completion_tokens,
                            "request_duration": round(time.time() - self.start_time, 2),
                            "retry_count": self.retry_count,
                            "failover_count": self.failover_count,
                            "timeout_status": self.timeout_status,
                            "streaming": True,
                            "time_to_first_token": round(time_to_first_token, 2),
                            "generation_duration": round(generation_duration, 2),
                            "completion_rate": round(completion_tokens / max(generation_duration, 0.1), 2),
                            "termination_reason": self.termination_reason
                        })
                    return generated_text.strip()

                except httpx.HTTPStatusError as exc:
                    code = exc.response.status_code
                    last_exc = exc
                    # Don't failover on 400 (Bad Request) - likely a prompt issue
                    if code == 400:
                        raise RuntimeError(f"[{self.current_config.provider}] HTTP {code}: {exc.response.text[:400]}") from exc
                    
                    # Log and break retry loop to trigger failover
                    logger.warning("[%s] HTTP %d. Triggering failover if available.", self.current_config.provider, code)
                    break
                    
                except httpx.RequestError as exc:
                    if isinstance(exc, httpx.TimeoutException):
                        self.timeout_status = True
                    last_exc = exc
                    if attempt < _MAX_RETRIES - 1:
                        self._backoff(attempt, "Network Error")
                        continue
                    break # Trigger failover
            
            # If we reached here, we exhausted retries or hit a hard failover trigger
            if failover_attempt == 0 and self.fallback_config:
                logger.warning("Failing over from %s to %s", self.current_config.provider, self.fallback_config.provider)
                self.current_config = self.fallback_config
                self.failover_count += 1
                self.retry_count = 0
                continue
            else:
                break
                
        self.termination_reason = "error"
        if metrics_callback:
            metrics_callback({
                "provider_used": self.current_config.provider,
                "fallback_provider": self.fallback_config.provider if self.fallback_config else None,
                "model": self.current_config.model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": 0,
                "total_tokens": prompt_tokens,
                "request_duration": round(time.time() - self.start_time, 2),
                "retry_count": self.retry_count,
                "failover_count": self.failover_count,
                "timeout_status": self.timeout_status,
                "streaming": True,
                "time_to_first_token": 0.0,
                "generation_duration": 0.0,
                "completion_rate": 0.0,
                "termination_reason": self.termination_reason
            })
        raise RuntimeError(f"[{self.current_config.provider}] All retry/failover attempts exhausted. Last Error: {last_exc}") from last_exc

    def _backoff(self, attempt: int, reason: str | int):
        wait = _RETRY_BACKOFF[attempt]
        logger.warning(
            "LLM provider %r returned %s — retrying in %.0fs (attempt %d/%d)",
            self.current_config.provider,
            reason,
            wait,
            attempt + 1,
            _MAX_RETRIES,
        )
        time.sleep(wait)
        self.retry_count += 1


def call_llm(
    prompt: str,
    settings: Settings,
    timeout: float = 60.0,
    metrics_callback: typing.Callable[[dict], None] | None = None,
    **kwargs,
) -> str:
    manager = RetryManager(settings, timeout)
    messages = [{"role": "user", "content": prompt}]
    return manager.execute(messages, kwargs, metrics_callback)
