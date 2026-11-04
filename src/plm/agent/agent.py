"""The assistant: a tool-calling loop over `plm.agent.tools`, with evidence-checked answers."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from plm.agent.citations import check_citations
from plm.agent.config import AgentConfig
from plm.agent.evidence import Evidence, EvidenceRegistry
from plm.agent.llm import LlmClient, LlmMessage, LlmToolCallError, Message, OpenAiCompatibleClient, Usage
from plm.agent.prompts import system_prompt
from plm.agent.tools import TOOLS_BY_NAME, ToolContext, execute, tool_schemas
from plm.contract import CostService, EnrichmentRepository, PlmRepository

FORCE_ANSWER = (
    "Stop calling tools: the step limit is reached. Answer now using only the tool results above, with citations. "
    "For anything you did not look up, say it was not checked; never present it as missing from the data."
)


@dataclass(frozen=True, slots=True)
class ToolTrace:
    name: str
    arguments: str
    seconds: float
    error: str | None


@dataclass(frozen=True, slots=True)
class AgentAnswer:
    text: str
    evidence: tuple[Evidence, ...]
    tool_calls: tuple[ToolTrace, ...]
    warnings: tuple[str, ...]
    usage: Usage


@dataclass(slots=True)
class Conversation:
    """History holds questions and final answers only; tool messages are dropped between turns to save tokens."""

    registry: EvidenceRegistry = field(default_factory=EvidenceRegistry)
    history: list[Message] = field(default_factory=list)


class PlmAgent:
    def __init__(
        self,
        plm: PlmRepository,
        enrichment: EnrichmentRepository,
        config: AgentConfig,
        client: LlmClient | None = None,
        costs: CostService | None = None,
    ) -> None:
        self._config = config
        self._client = client or OpenAiCompatibleClient(config)
        self._context = ToolContext.build(plm, enrichment, costs=costs)
        self._system_prompt = system_prompt(plm, enrichment)
        self._schemas = tool_schemas()

    @property
    def config(self) -> AgentConfig:
        return self._config

    def new_conversation(self) -> Conversation:
        return Conversation()

    def ask(self, conversation: Conversation, question: str) -> AgentAnswer:
        ctx = self._context.for_registry(conversation.registry)
        messages: list[Message] = [
            {"role": "system", "content": self._system_prompt},
            *conversation.history,
            {"role": "user", "content": question},
        ]
        traces: list[ToolTrace] = []
        warnings: list[str] = []
        usage = [0, 0]
        retried_tool_error = False
        answer: str | None = None

        def complete(**options) -> LlmMessage:
            reply = self._client.complete(messages, self._schemas, **options)
            if reply.usage:
                usage[0] += reply.usage.prompt_tokens
                usage[1] += reply.usage.completion_tokens
            return reply

        steps = 0
        while steps < self._config.max_steps:
            steps += 1
            try:
                reply = complete()
            except LlmToolCallError as exc:
                if retried_tool_error:
                    raise
                retried_tool_error = True
                warnings.append("The model produced a malformed tool call; retried once.")
                messages.append(
                    {
                        "role": "user",
                        "content": f"Your last tool call was rejected ({exc}). Valid tools: "
                        f"{', '.join(TOOLS_BY_NAME)}. Call one of them with valid JSON arguments, or answer.",
                    }
                )
                continue
            messages.append(reply.to_message())
            if not reply.tool_calls:
                answer = reply.content or ""
                break
            for call in reply.tool_calls:
                started = time.perf_counter()
                result = execute(ctx, call.name, call.arguments)
                traces.append(ToolTrace(call.name, call.arguments, time.perf_counter() - started, result.get("error")))
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    }
                )

        if answer is None:
            warnings.append(f"Stopped after {self._config.max_steps} steps; the answer uses what was gathered so far.")
            messages.append({"role": "user", "content": FORCE_ANSWER})
            answer = complete(tool_choice="none").content or ""

        if not answer.strip():
            warnings.append("The model returned an empty answer.")
        checked = check_citations(answer, conversation.registry)
        warnings.extend(checked.warnings)
        conversation.history += [
            {"role": "user", "content": question},
            {"role": "assistant", "content": checked.text},
        ]
        return AgentAnswer(
            text=checked.text,
            evidence=tuple(e for e in map(conversation.registry.get, checked.cited) if e is not None),
            tool_calls=tuple(traces),
            warnings=tuple(warnings),
            usage=Usage(prompt_tokens=usage[0], completion_tokens=usage[1]),
        )
