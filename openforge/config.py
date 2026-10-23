"""Configuration for OpenForge.

Deliberately plain: a dataclass populated from environment variables (loaded
from a .env file if present), not a framework. There's exactly one Settings
object, built once, passed to whatever needs it. This is the one place that
reads os.environ in the whole project.

Provider selection::

    LLM_PROVIDER=agentrouter  (default)
    LLM_PROVIDER=openrouter
    LLM_PROVIDER=dahl

Per-provider overrides::

    API_KEY=<key>
    API_BASE=<url>
    MODEL=<model-id>
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from rich.logging import RichHandler

# Load .env from the current working directory (or any parent of it) once,
# at import time. If it's missing that's fine — real env vars still work,
# and every field below has a sane default.
load_dotenv()


@dataclass(frozen=True)
class Settings:
    # --- AgentRouter ---
    agentrouter_api_key: str | None = field(default=None)
    agentrouter_model: str = "gpt-5.6-sol"

    # --- OpenRouter (preserved for backwards compatibility) ---
    openrouter_api_key: str | None = field(default=None)
    openrouter_model: str = "anthropic/claude-sonnet-4.6"

    # --- Dahl provider ---
    dahl_api_key: str | None = field(default=None)
    dahl_model: str = "moonshotai/Kimi-K2.6"

    # --- Provider selection ---
    llm_provider: str = "agentrouter"
    llm_fallback: str | None = field(default=None)

    # --- Generic overrides (highest priority) ---
    api_key: str | None = field(default=None)
    api_base: str | None = field(default=None)
    model: str | None = field(default=None)

    # --- Infrastructure ---
    github_token: str | None = field(default=None)
    workspace_dir: Path = field(default=Path("workspace"))
    runs_dir: Path = field(default=Path("runs"))
    log_level: str = "INFO"

    @property
    def llm_enabled(self) -> bool:
        """Whether the selected provider has an API key configured."""
        provider_keys = {
            "agentrouter": self.agentrouter_api_key,
            "openrouter": self.openrouter_api_key,
            "dahl": self.dahl_api_key,
        }
        return bool(self.api_key or provider_keys.get(self.llm_provider))

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            agentrouter_api_key=os.getenv("AGENTROUTER_API_KEY") or None,
            agentrouter_model=os.getenv("AGENTROUTER_MODEL", "gpt-5.6-sol"),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
            openrouter_model=os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.6"),
            dahl_api_key=os.getenv("DAHL_API_KEY") or None,
            dahl_model=os.getenv("DAHL_MODEL", "moonshotai/Kimi-K2.6"),
            llm_provider=os.getenv("LLM_PROVIDER", "agentrouter").lower(),
            llm_fallback=os.getenv("LLM_FALLBACK") or None,
            api_key=os.getenv("API_KEY") or None,
            api_base=os.getenv("API_BASE") or None,
            model=os.getenv("MODEL") or None,
            github_token=os.getenv("GITHUB_TOKEN") or None,
            workspace_dir=Path(os.getenv("OPENFORGE_WORKSPACE_DIR", "workspace")),
            runs_dir=Path(os.getenv("OPENFORGE_RUNS_DIR", "runs")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )


def configure_logging(level: str = "INFO") -> None:
    """Rich-formatted logging to stderr. Called once from the CLI entrypoint."""
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
        force=True,
    )


def get_settings() -> Settings:
    """Single entrypoint the rest of the codebase should import."""
    return Settings.load()
