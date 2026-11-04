"""Agent configuration, read from the environment and from a local `.env` file when one exists."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypeVar, get_args

from dotenv import dotenv_values

N = TypeVar("N", int, float)

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_DOTENV_PATH = Path(".env")
DEFAULT_REPLAY_PATH = Path("data/agent_replays/answers.json")

ReplayMode = Literal["off", "fallback", "only"]


class AgentConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AgentConfig:
    api_key: str = field(repr=False)
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    max_steps: int = 6
    temperature: float = 0.0
    timeout_s: float = 30.0
    max_retries: int = 5
    replay: ReplayMode = "off"
    replay_path: Path = DEFAULT_REPLAY_PATH

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        dotenv_path: Path | None = DEFAULT_DOTENV_PATH,
    ) -> AgentConfig:
        """Real environment variables take precedence over the `.env` file. Pass `env` to bypass both."""
        if env is None:
            file_values = dotenv_values(dotenv_path) if dotenv_path is not None and dotenv_path.is_file() else {}
            env = {**{k: v for k, v in file_values.items() if v is not None}, **os.environ}

        replay = env.get("PLM_AGENT_REPLAY", "").strip().lower() or "off"
        if replay not in get_args(ReplayMode):
            raise AgentConfigError(f"PLM_AGENT_REPLAY must be one of {', '.join(get_args(ReplayMode))}, got {replay!r}")
        api_key = env.get("PLM_LLM_API_KEY", "").strip()
        if not api_key and replay != "only":
            raise AgentConfigError(
                "PLM_LLM_API_KEY is not set. Copy .env.example to .env and paste your Groq key "
                "(console.groq.com/keys), or export the variable."
            )
        return cls(
            api_key=api_key,
            base_url=env.get("PLM_LLM_BASE_URL", "").strip() or DEFAULT_BASE_URL,
            model=env.get("PLM_LLM_MODEL", "").strip() or DEFAULT_MODEL,
            max_steps=_parse(env, "PLM_AGENT_MAX_STEPS", int, 6, minimum=1),
            temperature=_parse(env, "PLM_AGENT_TEMPERATURE", float, 0.0, minimum=0),
            timeout_s=_parse(env, "PLM_LLM_TIMEOUT_S", float, 30.0, minimum=1),
            max_retries=_parse(env, "PLM_LLM_MAX_RETRIES", int, 5, minimum=0),
            replay=replay,
            replay_path=Path(env.get("PLM_AGENT_REPLAY_PATH", "").strip() or DEFAULT_REPLAY_PATH),
        )


def _parse(env: Mapping[str, str], name: str, kind: type[N], default: N, minimum: N) -> N:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = kind(raw)
    except ValueError as exc:
        raise AgentConfigError(f"{name} must be a {kind.__name__}, got {raw!r}") from exc
    if value < minimum:
        raise AgentConfigError(f"{name} must be >= {minimum}, got {value}")
    return value
