"""Generic LLM provider abstraction for OpenForge.

Supports multiple OpenAI-compatible chat completion providers.
All agents call ``call_llm()`` from this module — they never know which
backend is active.

Provider selection via environment variables::

    LLM_PROVIDER=agentrouter  (default)
    LLM_PROVIDER=openrouter
    LLM_FALLBACK=dahl
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
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
    "agentrouter": {
        "base_url": "https://agentrouter.org/v1",
        "default_model": "gpt-5.6-sol",
    },
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
    def from_settings(cls, settings: Settings) -> "LLMProviderConfig":
        """Resolve the selected OpenAI-compatible provider."""
        provider = settings.llm_provider.lower()
        if provider not in _PROVIDERS:
            raise ValueError(
                f"Unknown LLM provider {provider!r}. "
                f"Expected one of: {', '.join(sorted(_PROVIDERS))}."
            )
        provider_keys = {
            "agentrouter": settings.agentrouter_api_key,
            "openrouter": settings.openrouter_api_key,
            "dahl": settings.dahl_api_key,
        }
        provider_models = {
            "agentrouter": settings.agentrouter_model,
            "openrouter": settings.openrouter_model,
            "dahl": settings.dahl_model,
        }
        return cls(
            provider=provider,
            base_url=(settings.api_base or _PROVIDERS[provider]["base_url"]).rstrip("/"),
            api_key=settings.api_key or provider_keys[provider],
            model=settings.model or provider_models[provider],
        )

    @property
    def chat_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)


class RetryManager:
    """Centralized retry controller managing backoff and budgets."""

    def __init__(self, settings: Settings, timeout: float, raw_callback: typing.Callable[[str], None] | None = None):
        self.settings = settings
        self.timeout = timeout
        self.raw_callback = raw_callback
        # Dahl is the sole provider.
        self.current_config = LLMProviderConfig.from_settings(settings)
        self.retry_count = 0
        self.start_time = time.time()
        self.timeout_status = False
        self.termination_reason = "success"

    def execute(self, messages: list[dict], extra: dict, metrics_callback: typing.Callable[[dict], None] | None) -> str:
        if not self.current_config.enabled:
            raise RuntimeError(
                f"No API key is configured for {self.current_config.provider}."
            )

        if self.current_config.provider == "agentrouter":
            return self._execute_agentrouter(messages, metrics_callback)

        prompt_tokens = sum(len(m.get("content", "")) // 4 for m in messages)
        last_exc: Exception | None = None

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
                if self.current_config.provider == "agentrouter":
                    headers.update(
                        {
                            "User-Agent": "claude-code/0.2.29",
                            "anthropic-version": "2023-06-01",
                            "x-app-name": "claude-code",
                        }
                    )

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
                        "model": self.current_config.model,
                        "request_id": request_id,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": prompt_tokens + completion_tokens,
                        "request_duration": round(time.time() - self.start_time, 2),
                        "retry_count": self.retry_count,
                        "timeout_status": self.timeout_status,
                        "streaming": True,
                        "time_to_first_token": round(time_to_first_token, 2),
                        "generation_duration": round(generation_duration, 2),
                        "completion_rate": round(completion_tokens / max(generation_duration, 0.1), 2),
                        "termination_reason": self.termination_reason
                    })
                if self.raw_callback:
                    self.raw_callback(generated_text)
                return generated_text.strip()

            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                last_exc = exc
                # 400 is a client-side prompt issue; don't retry.
                if code == 400:
                    raise RuntimeError(f"[{self.current_config.provider}] HTTP {code}: {exc.response.text[:400]}") from exc

                logger.warning(
                    "[%s] HTTP %d. Retrying if budget allows.",
                    self.current_config.provider,
                    code,
                )
                if attempt < _MAX_RETRIES - 1:
                    self._backoff(attempt, code)
                    continue
                break

            except httpx.RequestError as exc:
                if isinstance(exc, httpx.TimeoutException):
                    self.timeout_status = True
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    self._backoff(attempt, "Network Error")
                    continue
                break

        self.termination_reason = "error"
        if metrics_callback:
            metrics_callback({
                "provider_used": self.current_config.provider,
                "model": self.current_config.model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": 0,
                "total_tokens": prompt_tokens,
                "request_duration": round(time.time() - self.start_time, 2),
                "retry_count": self.retry_count,
                "timeout_status": self.timeout_status,
                "streaming": True,
                "time_to_first_token": 0.0,
                "generation_duration": 0.0,
                "completion_rate": 0.0,
                "termination_reason": self.termination_reason
            })
        raise RuntimeError(
            f"[{self.current_config.provider}] All retry attempts exhausted. "
            f"Last Error: {last_exc}"
        ) from last_exc

    def _execute_agentrouter(
        self,
        messages: list[dict],
        metrics_callback: typing.Callable[[dict], None] | None,
    ) -> str:
        """Call AgentRouter through its supported OpenCode client transport."""
        prompt = "\n\n".join(m.get("content", "") for m in messages)
        prompt_tokens = len(prompt) // 4
        config = {
            "provider": {
                "agentrouter": {
                    "name": "AgentRouter",
                    "package": "@ai-sdk/openai-compatible",
                    "options": {
                        "baseURL": self.current_config.base_url,
                        "apiKey": "{env:AGENTROUTER_API_KEY}",
                        "headers": {
                            "User-Agent": "claude-code/0.2.29",
                            "anthropic-version": "2023-06-01",
                            "x-app-name": "claude-code",
                        },
                    },
                    "models": {
                        self.current_config.model: {
                            "name": self.current_config.model,
                        }
                    },
                }
            },
            "permission": {"*": "deny"},
        }
        env = os.environ.copy()
        env.update(
            {
                "AGENTROUTER_API_KEY": typing.cast(str, self.current_config.api_key),
                "OPENCODE_CONFIG_CONTENT": json.dumps(config),
                "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
            }
        )
        last_error = "unknown error"

        for attempt in range(_MAX_RETRIES):
            attempt_start = time.time()
            try:
                result = subprocess.run(
                    [
                        "opencode",
                        "run",
                        "--pure",
                        "--model",
                        f"agentrouter/{self.current_config.model}",
                        "--format",
                        "json",
                    ],
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    env=env,
                )
            except FileNotFoundError as exc:
                raise RuntimeError(
                    "AgentRouter requires the `opencode` executable on PATH."
                ) from exc
            except subprocess.TimeoutExpired as exc:
                self.timeout_status = True
                last_error = f"OpenCode timed out after {self.timeout}s"
                if attempt < _MAX_RETRIES - 1:
                    self._backoff(attempt, "Timeout")
                    continue
                break

            text_parts: list[str] = []
            usage: dict = {}
            event_error = ""
            for line in result.stdout.splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "text":
                    text_parts.append(event.get("part", {}).get("text", ""))
                elif event.get("type") == "step_finish":
                    usage = event.get("part", {}).get("tokens", {})
                elif event.get("type") == "error":
                    event_error = str(event.get("error") or event)

            generated_text = "".join(text_parts).strip()
            if result.returncode == 0 and generated_text:
                completion_tokens = int(usage.get("output", len(generated_text) // 4))
                actual_prompt_tokens = int(usage.get("input", prompt_tokens))
                duration = time.time() - attempt_start
                if metrics_callback:
                    metrics_callback(
                        {
                            "provider_used": self.current_config.provider,
                            "model": self.current_config.model,
                            "prompt_tokens": actual_prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "total_tokens": int(
                                usage.get(
                                    "total", actual_prompt_tokens + completion_tokens
                                )
                            ),
                            "request_duration": round(duration, 2),
                            "retry_count": self.retry_count,
                            "timeout_status": self.timeout_status,
                            "streaming": True,
                            "time_to_first_token": 0.0,
                            "generation_duration": round(duration, 2),
                            "completion_rate": round(
                                completion_tokens / max(duration, 0.1), 2
                            ),
                            "termination_reason": "success",
                        }
                    )
                if self.raw_callback:
                    self.raw_callback(generated_text)
                return generated_text

            last_error = event_error or result.stderr.strip() or "empty response"
            if attempt < _MAX_RETRIES - 1:
                self._backoff(attempt, f"OpenCode exit {result.returncode}")

        self.termination_reason = "error"
        if metrics_callback:
            metrics_callback(
                {
                    "provider_used": self.current_config.provider,
                    "model": self.current_config.model,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": 0,
                    "total_tokens": prompt_tokens,
                    "request_duration": round(time.time() - self.start_time, 2),
                    "retry_count": self.retry_count,
                    "timeout_status": self.timeout_status,
                    "streaming": True,
                    "time_to_first_token": 0.0,
                    "generation_duration": 0.0,
                    "completion_rate": 0.0,
                    "termination_reason": self.termination_reason,
                }
            )
        raise RuntimeError(
            f"[agentrouter] OpenCode transport failed after {_MAX_RETRIES} "
            f"attempts: {last_error[:400]}"
        )

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
    raw_callback: typing.Callable[[str], None] | None = None,
    **kwargs,
) -> str:
    manager = RetryManager(settings, timeout, raw_callback=raw_callback)
    messages = [{"role": "user", "content": prompt}]
    return manager.execute(messages, kwargs, metrics_callback)
