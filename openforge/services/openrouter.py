"""OpenRouter-compatible client shim.

All actual provider logic lives in ``openforge.services.llm``.
This module is kept for backwards compatibility and for the
``summarize_conventions`` helper which is specific to the inspect step.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from openforge.config import Settings
from openforge.models import RepositoryContext
from openforge.services.llm import call_llm as call_llm  # noqa: F401

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "repo_summary.md"


def summarize_conventions(
    context: RepositoryContext, settings: Settings, timeout: float = 30.0
) -> str | None:
    """Ask an LLM for a plain-English summary of the detected repo facts.

    Returns None (never raises) if no API key is configured or the call
    fails — the RepositoryAnalyzer's output is fully usable without this.
    """
    if not settings.llm_enabled:
        logger.debug("No LLM API key set — skipping conventions summary")
        return None

    try:
        template = _PROMPT_PATH.read_text()
    except FileNotFoundError:
        logger.warning("Prompt file missing at %s — skipping summary", _PROMPT_PATH)
        return None

    prompt = template.format(repo_context_json=context.model_dump_json(exclude_none=True, indent=2))

    try:
        from openforge.services.llm import call_llm
        return call_llm(prompt, settings, timeout=timeout)
    except Exception as exc:
        logger.warning("Conventions summary call failed, continuing without it: %s", exc)
        return None
