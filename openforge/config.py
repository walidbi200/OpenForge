"""Configuration for OpenForge.

Deliberately plain: a dataclass populated from environment variables (loaded
from a .env file if present), not a framework. There's exactly one Settings
object, built once, passed to whatever needs it. When we add the Planner /
Coding / Testing agents later, their API keys and knobs go here too — this
file is meant to be the one place that reads os.environ in the whole project.
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
    openrouter_api_key: str | None = field(default=None)
    openrouter_model: str = "anthropic/claude-sonnet-4.6"
    github_token: str | None = field(default=None)
    workspace_dir: Path = field(default=Path("workspace"))
    runs_dir: Path = field(default=Path("runs"))
    log_level: str = "INFO"

    @property
    def llm_enabled(self) -> bool:
        """Whether we have a key to call OpenRouter for the optional
        conventions-summary step. Everything else in the Repository Agent
        works with this set to False — it just runs heuristics."""
        return bool(self.openrouter_api_key)

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
            openrouter_model=os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.6"),
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
