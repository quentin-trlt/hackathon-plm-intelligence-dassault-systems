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
SCOPE_MESSAGES = {
    "fr": (
        "Je réponds uniquement aux questions sur le moteur, à partir de l'extrait PLM et des données d'enrichissement : "
        "structure et sous-ensembles, pièces, quantités, masses, matériaux, coûts matière, fournisseurs et impact "
        "d'un changement de pièce. Je ne réponds pas aux demandes que ces données ne permettent pas de sourcer. "
        "Exemple de question : « Quelles pièces n'ont qu'un seul fournisseur ? »"
    ),
    "en": (
        "I only answer questions about the motor, based on the PLM extract and the enrichment data: structure and "
        "subassemblies, parts, quantities, masses, materials, material costs, suppliers and the impact of a part "
        "change. I do not answer requests that this data cannot source. "
        'Example question: "Which parts have a single supplier?"'
    ),
}
_FRENCH_WORDS = frozenset(
    "le la les un une des du de au aux et est sont quel quelle quels quelles combien comment pourquoi "
    "moi je tu vous nous pour sur dans avec qui que quoi donne donner fais peux".split()
)
_FRENCH_LETTERS = frozenset("àâçéèêëîïôûùüÿœ")


def is_grounded(cited: tuple[str, ...], used_tools: bool, uncited_figures: int) -> bool:
    """An answer is kept if it cites evidence, or if it relied on tools without stating unsourced figures."""
    return bool(cited) or (used_tools and uncited_figures == 0)


def scope_message(question: str) -> str:
    lowered = question.casefold()
    words = set(lowered.replace("'", " ").replace("’", " ").split())
    french = bool(words & _FRENCH_WORDS) or any(c in _FRENCH_LETTERS for c in lowered)
    return SCOPE_MESSAGES["fr" if french else "en"]


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
        text, cited = checked.text, checked.cited
        if not is_grounded(checked.cited, bool(traces), checked.uncited_figures):
            warnings.append("Answer not grounded in the PLM data (no citation): replaced by the scope message.")
            text, cited = scope_message(question), ()
        conversation.history += [
            {"role": "user", "content": question},
            {"role": "assistant", "content": text},
        ]
        return AgentAnswer(
            text=text,
            evidence=tuple(e for e in map(conversation.registry.get, cited) if e is not None),
            tool_calls=tuple(traces),
            warnings=tuple(warnings),
            usage=Usage(prompt_tokens=usage[0], completion_tokens=usage[1]),
        )
