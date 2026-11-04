"""Phase 0 spike: check that a model chains tool calls correctly on the real extract, and compare models.

    python -m plm.agent.spike
    python -m plm.agent.spike --model openai/gpt-oss-120b --model llama-3.3-70b-versatile -v

Throwaway code: the real loop and tools come in phase 1 (agent.py, tools.py).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from plm.agent.config import AgentConfig, AgentConfigError
from plm.agent.llm import LlmClient, LlmError, LlmRateLimitError, Message, OpenAiCompatibleClient, ToolCall
from plm.contract import PlmRepository, UnknownProductError
from plm.parser import load

SEARCH_LIMIT = 15

SYSTEM_PROMPT = """You answer questions about the product structure of an electric motor, using an OEM PLM extract.
Rules:
- Only state facts returned by the tools. Never guess a quantity.
- Products are identified by plm_id. Names are not unique keys.
- Every physical part (kind "part", name ECAR-3D...) is held by a logical wrapper (kind "wrapper", name ECAR-EI...)
  with the same quantity. They are the same physical object: count parts only, never a wrapper and its part together.
- search_products does a substring match: use short queries (one or two words).
- Cite the plm_id of every product you mention.
- If the extract contains no matching product, say so plainly."""

TOOL_SCHEMAS: list[Message] = [
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "Case-insensitive substring search on product name, plm_id and material.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Short text, e.g. 'screw' or 'D10'"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_total_quantity",
            "description": "Number of units of a product in the complete motor, cumulated over the whole BOM.",
            "parameters": {
                "type": "object",
                "properties": {"plm_id": {"type": "string", "description": "e.g. prd-12535825-00000081"}},
                "required": ["plm_id"],
            },
        },
    },
]


def search_products(plm: PlmRepository, query: str) -> dict[str, Any]:
    matches = plm.search(query)
    result: dict[str, Any] = {
        "query": query,
        "total": len(matches),
        "truncated": len(matches) > SEARCH_LIMIT,
        "products": [
            {"plm_id": p.plm_id, "name": p.name, "kind": p.item_kind, "material": p.material}
            for p in matches[:SEARCH_LIMIT]
        ],
    }
    if not matches:
        result["hint"] = "No match. Try a shorter or different query before concluding."
    return result


def get_total_quantity(plm: PlmRepository, plm_id: str) -> dict[str, Any]:
    try:
        product = plm.product(plm_id)
    except UnknownProductError:
        return {"error": f"Unknown plm_id {plm_id!r}. Use search_products to find valid ids."}
    return {
        "plm_id": plm_id,
        "name": product.name,
        "kind": product.item_kind,
        "total_quantity": plm.total_quantity(plm_id).value,
        "source": f"{product.source.file} line {product.source.line}",
    }


TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "search_products": search_products,
    "get_total_quantity": get_total_quantity,
}


def execute(plm: PlmRepository, call: ToolCall) -> dict[str, Any]:
    handler = TOOLS.get(call.name)
    if handler is None:
        return {"error": f"Unknown tool {call.name!r}. Available: {', '.join(TOOLS)}"}
    try:
        arguments = json.loads(call.arguments)
    except json.JSONDecodeError as exc:
        return {"error": f"Arguments are not valid JSON: {exc}"}
    if not isinstance(arguments, dict):
        return {"error": "Arguments must be a JSON object"}
    try:
        return handler(plm, **arguments)
    except TypeError as exc:
        return {"error": f"Bad arguments for {call.name}: {exc}"}


@dataclass(frozen=True, slots=True)
class Question:
    text: str
    expected: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    required_tool: str | None = "get_total_quantity"
    manual: bool = False


QUESTIONS = (
    Question("How many ECAR-3D Screw D10 L155 are there in the complete motor?", expected=("14",)),
    Question("How many differential side shafts does the motor contain?", expected=("2",)),
    Question(
        "How many D10 screws are there in the motor in total, all lengths combined?",
        expected=("18",),
        forbidden=("36",),
    ),
    Question("How many rotor magnets are in the motor?", required_tool=None, manual=True),
)


@dataclass(slots=True)
class Run:
    answer: str = ""
    calls: list[str] = field(default_factory=list)
    steps: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    error: str | None = None


def run_question(client: LlmClient, plm: PlmRepository, question: str, max_steps: int) -> Run:
    run = Run()
    started = time.perf_counter()
    messages: list[Message] = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": question}]
    try:
        for _ in range(max_steps):
            reply = client.complete(messages, TOOL_SCHEMAS)
            run.steps += 1
            if reply.usage:
                run.prompt_tokens += reply.usage.prompt_tokens
                run.completion_tokens += reply.usage.completion_tokens
            messages.append(reply.to_message())
            if not reply.tool_calls:
                run.answer = reply.content or ""
                break
            for call in reply.tool_calls:
                run.calls.append(f"{call.name}({call.arguments})")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(execute(plm, call), ensure_ascii=False),
                    }
                )
        else:
            run.error = f"step limit ({max_steps}) reached"
    finally:
        run.seconds = time.perf_counter() - started
    return run


def verdict(question: Question, run: Run) -> str:
    if run.error:
        return "ERROR"
    if question.manual:
        return "REVIEW"

    def contains(number: str) -> bool:
        return re.search(rf"(?<![\d.]){re.escape(number)}(?![\d.])", run.answer) is not None

    called = question.required_tool is None or any(c.startswith(f"{question.required_tool}(") for c in run.calls)
    ok = called and all(contains(n) for n in question.expected) and not any(contains(n) for n in question.forbidden)
    return "PASS" if ok else "FAIL"


def benchmark(config: AgentConfig, plm: PlmRepository, verbose: bool) -> int:
    client = OpenAiCompatibleClient(config)
    print(f"\n== {config.model}")
    passed = 0
    for index, question in enumerate(QUESTIONS, start=1):
        try:
            run = run_question(client, plm, question.text, config.max_steps)
        except LlmRateLimitError as exc:
            print(f"Q{index} RATE LIMIT  {exc}\n   stopping this model")
            return passed
        except LlmError as exc:
            run = Run(error=str(exc))
        result = verdict(question, run)
        passed += result == "PASS"
        print(
            f"Q{index} {result:<6} steps={run.steps} tokens={run.prompt_tokens}+{run.completion_tokens} "
            f"{run.seconds:.1f}s  {question.text}"
        )
        if verbose or result != "PASS":
            for call in run.calls:
                print(f"   -> {call}")
            print(f"   answer: {run.error or run.answer.strip()}")
    print(f"   {passed}/{sum(not q.manual for q in QUESTIONS)} automatic checks passed")
    return passed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", action="append", help="Model id, repeatable. Default: PLM_LLM_MODEL")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show tool calls and answers for every question")
    args = parser.parse_args(argv)

    try:
        config = AgentConfig.from_env()
    except AgentConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    plm = load().plm
    for model in args.model or [config.model]:
        benchmark(dataclasses.replace(config, model=model), plm, args.verbose)
    return 0


if __name__ == "__main__":
    sys.exit(main())
