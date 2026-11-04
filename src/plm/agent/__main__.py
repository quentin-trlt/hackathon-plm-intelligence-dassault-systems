"""Ask the assistant from the terminal.

python -m plm.agent "Which parts are single-sourced?"
python -m plm.agent --trace          # interactive session, empty line or Ctrl-D to quit
"""

from __future__ import annotations

import argparse
import sys

from plm.agent.agent import AgentAnswer, Conversation, PlmAgent
from plm.agent.config import AgentConfig, AgentConfigError
from plm.agent.evidence import describe
from plm.agent.llm import LlmError
from plm.parser import load

MAX_REFS_SHOWN = 3


def render(answer: AgentAnswer, trace: bool) -> str:
    lines = [answer.text.strip(), ""]
    if trace:
        lines.append("Tool calls:")
        lines += [
            f"  {t.name}({t.arguments}) {t.seconds * 1000:.0f} ms{f'  ERROR {t.error}' if t.error else ''}"
            for t in answer.tool_calls
        ]
        lines.append("")
    if answer.evidence:
        lines.append("Sources:")
        for evidence in answer.evidence:
            flag = "  [non-PLM source]" if evidence.is_enrichment else ""
            product = f" ({evidence.plm_id})" if evidence.plm_id and evidence.plm_id not in evidence.label else ""
            lines.append(f"  [{evidence.id}] {evidence.label}{product}{flag}")
            lines += [f"       {describe(ref)}" for ref in evidence.refs[:MAX_REFS_SHOWN]]
            if len(evidence.refs) > MAX_REFS_SHOWN:
                lines.append(f"       ... and {len(evidence.refs) - MAX_REFS_SHOWN} more")
            if evidence.note:
                lines.append(f"       note: {evidence.note}")
    lines += [f"Warning: {w}" for w in answer.warnings]
    lines.append(f"({answer.usage.prompt_tokens}+{answer.usage.completion_tokens} tokens)")
    return "\n".join(lines)


def ask(agent: PlmAgent, conversation: Conversation, question: str, trace: bool) -> bool:
    try:
        answer = agent.ask(conversation, question)
    except LlmError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return False
    print(render(answer, trace))
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", nargs="?", help="Question to ask. Omit it for an interactive session")
    parser.add_argument("--trace", action="store_true", help="Show the tool calls behind each answer")
    args = parser.parse_args(argv)

    try:
        config = AgentConfig.from_env()
    except AgentConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    data = load()
    agent = PlmAgent(data.plm, data.enrichment, config)
    conversation = agent.new_conversation()

    if args.question:
        return 0 if ask(agent, conversation, args.question, args.trace) else 1
    while True:
        try:
            question = input("\n> ").strip()
        except EOFError:
            return 0
        if not question:
            return 0
        ask(agent, conversation, question, args.trace)


if __name__ == "__main__":
    sys.exit(main())
