"""Assistant: questions in natural language, every figure cited back to its XML element or CSV row.

The chat functions are shared with the standalone test page, app/assistant.py.
"""

from __future__ import annotations

import dataclasses
import re

import streamlit as st

from plm.agent import AgentAnswer, AgentConfig, AgentConfigError, LlmError, PlmAgent, describe
from plm.agent.eval import DEMO_QUESTIONS
from plm.agent.replay import ReplayingAgent, build_assistant
from services import ENRICHMENT_DIR, NON_PLM, REPO_ROOT, XML_PATH, Services

MAX_REFS_SHOWN = 5
_CITATION = re.compile(r"\[(E\d+)\]")


@st.cache_resource
def get_agent() -> PlmAgent | ReplayingAgent:
    config = AgentConfig.from_env(dotenv_path=REPO_ROOT / ".env")
    config = dataclasses.replace(config, replay_path=REPO_ROOT / config.replay_path)
    return build_assistant(config, XML_PATH, ENRICHMENT_DIR)


def load_agent() -> PlmAgent | ReplayingAgent | None:
    """The agent, or None after showing why it cannot be built (missing key, invalid setting)."""
    try:
        return get_agent()
    except AgentConfigError as exc:
        st.error(str(exc))
        st.caption("Set PLM_AGENT_REPLAY=only in .env to replay the recorded answers without a key.")
        return None


def agent_status(agent: PlmAgent | ReplayingAgent) -> str:
    status = f"Model: {agent.config.model}"
    if isinstance(agent, ReplayingAgent):
        status += f" · replay mode: {agent.mode}, {len(agent.store)} recorded answer(s)"
        stale = agent.store.stale_count(agent.fingerprint)
        if stale:
            status += f", {stale} recorded on older data and ignored"
    return status


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
                badge = f" · `{NON_PLM}`" if evidence.is_enrichment else ""
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


def new_conversation(agent: PlmAgent | ReplayingAgent) -> None:
    st.session_state.assistant_conversation = agent.new_conversation()
    st.session_state.assistant_turns = []


def example_buttons(columns: int = 1) -> str | None:
    """One button per demo question; returns the question clicked in this run, if any."""
    clicked = None
    slots = st.columns(columns)
    for index, example in enumerate(DEMO_QUESTIONS):
        if slots[index % columns].button(example, key=f"example-{index}", width="stretch"):
            clicked = example
    return clicked


def chat(agent: PlmAgent | ReplayingAgent, pending: str | None, show_tool_calls: bool) -> None:
    if "assistant_conversation" not in st.session_state:
        new_conversation(agent)

    for question, answer in st.session_state.assistant_turns:
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
                answer = agent.ask(st.session_state.assistant_conversation, question)
        except LlmError as exc:
            st.error(str(exc))
            return
        st.session_state.assistant_turns.append((question, answer))
        render_answer(answer, show_tool_calls)


def render(s: Services) -> None:
    st.header("Assistant")
    st.caption(
        "Questions in English or French. Figures are computed by the analysis code, never by the model, and every "
        "answer lists the XML elements and CSV rows it comes from."
    )
    agent = load_agent()
    if agent is None:
        return

    c1, c2, c3 = st.columns([3, 1, 1], vertical_alignment="center")
    c1.caption(agent_status(agent))
    show_tool_calls = c2.toggle("Show tool calls", value=False)
    if c3.button("New conversation", width="stretch"):
        new_conversation(agent)
    with st.expander("Example questions"):
        pending = example_buttons(columns=2)

    chat(agent, pending, show_tool_calls)
