"""Thin OpenRouter client.

Deliberately minimal — one function, one responsibility. When the Planner /
Coding / Review agents show up later, they get their own functions here (or
their own modules), each with the model chosen for that role passed in
explicitly, per the "define model aliases in config, don't hardcode a model
per agent" idea from the design plan.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from openforge.config import Settings
from openforge.models import RepositoryContext

logger = logging.getLogger(__name__)

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "repo_summary.md"


def summarize_conventions(
    context: RepositoryContext, settings: Settings, timeout: float = 30.0
) -> str | None:
    """Ask an LLM for a plain-English summary of the detected repo facts.

    Returns None (never raises) if no API key is configured or the call
    fails — the RepositoryAnalyzer's output is fully usable without this.
    """
    if not settings.llm_enabled:
        logger.debug("OPENROUTER_API_KEY not set — skipping conventions summary")
        return None

    try:
        template = _PROMPT_PATH.read_text()
    except FileNotFoundError:
        logger.warning("Prompt file missing at %s — skipping summary", _PROMPT_PATH)
        return None

    prompt = template.format(repo_context_json=context.model_dump_json(exclude_none=True, indent=2))

    try:
        response = httpx.post(
            _OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.openrouter_model,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    except (httpx.HTTPError, KeyError, IndexError) as exc:
        logger.warning("Conventions summary call failed, continuing without it: %s", exc)
        return None
