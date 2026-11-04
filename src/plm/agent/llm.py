"""Thin client over any OpenAI-compatible chat completions endpoint (Groq by default).

Retries on 429 and 5xx are handled by the SDK, which honours the `retry-after` header.
This module only converts SDK objects into plain dataclasses and SDK errors into `LlmError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import openai
from openai import OpenAI

from plm.agent.config import AgentConfig

Message = dict[str, Any]


class LlmError(RuntimeError):
    pass


class LlmRateLimitError(LlmError):
    pass


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True, slots=True)
class LlmMessage:
    content: str | None
    tool_calls: tuple[ToolCall, ...]
    usage: Usage | None = None

    def to_message(self) -> Message:
        """The assistant message to append to the conversation before the tool results."""
        message: Message = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [
                {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": call.arguments}}
                for call in self.tool_calls
            ]
        return message


class LlmClient(Protocol):
    def complete(self, messages: list[Message], tools: list[Message] | None = None) -> LlmMessage: ...


class OpenAiCompatibleClient:
    """Implements `LlmClient`. `client` can be injected for tests."""

    def __init__(self, config: AgentConfig, client: OpenAI | None = None) -> None:
        self._config = config
        self._client = client or OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_s,
            max_retries=2,
        )

    def complete(self, messages: list[Message], tools: list[Message] | None = None) -> LlmMessage:
        options: dict[str, Any] = {"tools": tools, "tool_choice": "auto"} if tools else {}
        where = f"{self._config.model} at {self._config.base_url}"
        try:
            response = self._client.chat.completions.create(
                model=self._config.model,
                messages=messages,
                temperature=self._config.temperature,
                **options,
            )
        except openai.RateLimitError as exc:
            raise LlmRateLimitError(f"Rate limit reached on {where}: {exc.message}") from exc
        except openai.APIStatusError as exc:
            raise LlmError(f"HTTP {exc.status_code} from {where}: {exc.message}") from exc
        except openai.APIConnectionError as exc:
            raise LlmError(f"Cannot reach {where}: {exc}") from exc

        if not response.choices:
            raise LlmError(f"Empty response from {where}")
        message = response.choices[0].message
        tool_calls = tuple(
            ToolCall(id=call.id, name=call.function.name, arguments=call.function.arguments or "{}")
            for call in message.tool_calls or ()
            if call.type == "function"
        )
        usage = (
            Usage(prompt_tokens=response.usage.prompt_tokens, completion_tokens=response.usage.completion_tokens)
            if response.usage
            else None
        )
        return LlmMessage(content=message.content, tool_calls=tool_calls, usage=usage)
