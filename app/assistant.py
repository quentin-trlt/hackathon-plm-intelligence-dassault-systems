"""Test page for the AI assistant, independent from the main app.

streamlit run app/assistant.py
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import streamlit as st

from plm.agent import AgentAnswer, AgentConfig, AgentConfigError, LlmError, PlmAgent, describe
from plm.agent.eval import DEMO_QUESTIONS
from plm.agent.replay import ReplayingAgent, build_assistant

REPO_ROOT = Path(__file__).resolve().parents[1]
MAX_REFS_SHOWN = 5
_CITATION = re.compile(r"\[(E\d+)\]")


@st.cache_resource
def get_agent() -> PlmAgent | ReplayingAgent:
    config = AgentConfig.from_env(dotenv_path=REPO_ROOT / ".env")
    config = dataclasses.replace(config, replay_path=REPO_ROOT / config.replay_path)
    return build_assistant(
        config, REPO_ROOT / "data" / "raw" / "XPDMXML_ECAR_PLM.xml", REPO_ROOT / "data" / "enrichment"
    )


def numbered_text(answer: AgentAnswer) -> str:
    """Replace evidence ids by their rank in the source list, [E12] -> [1]."""
    numbers = {evidence.id: rank for rank, evidence in enumerate(answer.evidence, start=1)}
    return _CITATION.sub(lambda m: f"[{numbers[m[1]]}]" if m[1] in numbers else "", answer.text)


def render_answer(answer: AgentAnswer, show_tool_calls: bool) -> None:
    if answer.replayed_at:
        st.caption(f"Recorded answer, replayed (recorded {answer.replayed_at} on the same data)")
    st.markdown(numbered_text(answer))
    for warning in answer.warnings:
        st.caption(f"Warning: {warning}")

    if answer.evidence:
        with st.expander(f"Sources ({len(answer.evidence)})"):
            for rank, evidence in enumerate(answer.evidence, start=1):
                badge = " · `non-PLM source`" if evidence.is_enrichment else ""
                lines = [f"**[{rank}]** {evidence.label}{badge}"]
                lines += [f"- {describe(ref)}" for ref in evidence.refs[:MAX_REFS_SHOWN]]
                if len(evidence.refs) > MAX_REFS_SHOWN:
                    lines.append(f"- ... and {len(evidence.refs) - MAX_REFS_SHOWN} more")
                if evidence.note:
                    lines.append(f"- note: {evidence.note}")
                st.markdown("\n".join(lines))

    if show_tool_calls and answer.tool_calls:
        with st.expander(f"Tool calls ({len(answer.tool_calls)})"):
            st.code(
                "\n".join(
                    f"{t.name}({t.arguments}){f'  -> ERROR {t.error}' if t.error else ''}" for t in answer.tool_calls
                ),
                language=None,
            )
    if show_tool_calls and answer.rejected_text:
        with st.expander("Rejected model answer (not grounded in the data)"):
            st.text(answer.rejected_text)
    st.caption(f"{answer.usage.prompt_tokens} + {answer.usage.completion_tokens} tokens")


def main() -> None:
    st.set_page_config(page_title="PLM assistant", layout="centered")
    st.title("PLM assistant")

    try:
        agent = get_agent()
    except AgentConfigError as exc:
        st.error(str(exc))
        st.stop()

    if "conversation" not in st.session_state:
        st.session_state.conversation = agent.new_conversation()
        st.session_state.turns = []

    pending = None
    with st.sidebar:
        st.caption(f"Model: {agent.config.model}")
        if isinstance(agent, ReplayingAgent):
            st.caption(f"Replay mode: {agent.mode}, {len(agent.store)} recorded answer(s)")
        show_tool_calls = st.toggle("Show tool calls", value=True)
        if st.button("New conversation", width="stretch"):
            st.session_state.conversation = agent.new_conversation()
            st.session_state.turns = []
        st.subheader("Examples")
        for index, example in enumerate(DEMO_QUESTIONS):
            if st.button(example, key=f"example-{index}", width="stretch"):
                pending = example

    for question, answer in st.session_state.turns:
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            render_answer(answer, show_tool_calls)

    question = st.chat_input("Ask a question about the motor") or pending
    if not question:
        return
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            with st.spinner("Querying the PLM data..."):
                answer = agent.ask(st.session_state.conversation, question)
        except LlmError as exc:
            st.error(str(exc))
            return
        st.session_state.turns.append((question, answer))
        render_answer(answer, show_tool_calls)


main()
