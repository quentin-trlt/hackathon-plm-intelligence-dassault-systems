"""Assistant: questions in natural language, every figure cited back to its XML element or CSV row.

The chat functions are shared with the standalone test page, app/assistant.py.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from html import escape
from pathlib import Path

import streamlit as st

import ui
from plm.agent import AgentAnswer, AgentConfig, AgentConfigError, LlmError, PlmAgent
from plm.agent.eval import DEMO_QUESTIONS, QUESTIONS
from plm.agent.replay import ReplayingAgent, build_assistant
from services import MAX_CACHED_DATASETS, REFERENCE, REPO_ROOT, Dataset, Services

MAX_REFS_SHOWN = 5
_CITATION = re.compile(r"\[(E\d+)\]")
GENERIC_QUESTIONS = tuple(
    QUESTIONS[i] for i in ("subassemblies", "most_critical", "heaviest_materials", "single_source", "top_supplier")
)


@st.cache_resource(max_entries=MAX_CACHED_DATASETS)
def get_agent(xml_path: str, enrichment_dir: str) -> PlmAgent | ReplayingAgent:
    config = AgentConfig.from_env(dotenv_path=REPO_ROOT / ".env")
    config = dataclasses.replace(config, replay_path=REPO_ROOT / config.replay_path)
    return build_assistant(config, Path(xml_path), Path(enrichment_dir))


def load_agent(dataset: Dataset = REFERENCE) -> PlmAgent | ReplayingAgent | None:
    """The agent on the dataset, or None after showing why it cannot be built (missing key, invalid setting)."""
    try:
        return get_agent(str(dataset.xml_path), str(dataset.enrichment_dir))
    except AgentConfigError as exc:
        st.error(str(exc))
        st.caption("Set PLM_AGENT_REPLAY=only in .env to replay the recorded answers without a key.")
        return None


def agent_status(agent: PlmAgent | ReplayingAgent, dataset: Dataset = REFERENCE) -> str:
    status = f"Model {agent.config.model}"
    if isinstance(agent, ReplayingAgent):
        status += f", replay mode {agent.mode} with {len(agent.store)} recorded answer(s)"
        stale = agent.store.stale_count(agent.fingerprint)
        if stale and not dataset.reference:
            status += ", none for this dataset"
        elif stale:
            status += f", {stale} recorded on older data and ignored"
    return status


def numbered_text(answer: AgentAnswer) -> str:
    """Replace evidence ids by their rank in the source list, [E12] -> [1]."""
    numbers = {evidence.id: rank for rank, evidence in enumerate(answer.evidence, start=1)}
    return _CITATION.sub(lambda m: f"[{numbers[m[1]]}]" if m[1] in numbers else "", answer.text)


def _evidence_html(rank: int, evidence) -> str:
    refs = list(evidence.refs[:MAX_REFS_SHOWN])
    more = len(evidence.refs) - len(refs)
    origin = "Team data" if evidence.is_enrichment else "OEM extract"
    note = f" {escape(evidence.note)}." if evidence.note else ""
    extra = f", {more} more not shown" if more > 0 else ""
    return (
        f'<div class="plm-chips-heading"><strong>[{rank}]</strong> {escape(evidence.label)} '
        f"({origin}{extra}).{note}</div>{ui.chip_row(refs)}"
    )


def render_answer(answer: AgentAnswer, show_tool_calls: bool) -> None:
    if answer.replayed_at:
        st.caption(f"Recorded answer, replayed. Recorded {answer.replayed_at} on the same data.")
    st.markdown(numbered_text(answer))
    for warning in answer.warnings:
        st.caption(f"Warning: {warning}")

    if answer.evidence:
        with st.expander(f"Sources ({len(answer.evidence)})"):
            st.html("".join(_evidence_html(rank, ev) for rank, ev in enumerate(answer.evidence, start=1)))

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
    if show_tool_calls:
        st.caption(f"{answer.usage.prompt_tokens} + {answer.usage.completion_tokens} tokens")


def new_conversation(agent: PlmAgent | ReplayingAgent) -> None:
    st.session_state.assistant_conversation = agent.new_conversation()
    st.session_state.assistant_turns = []


def example_buttons(columns: int = 1, questions: Sequence[str] = DEMO_QUESTIONS) -> str | None:
    """One button per demo question; returns the question clicked in this run, if any."""
    clicked = None
    slots = st.columns(columns)
    for index, example in enumerate(questions):
        if slots[index % columns].button(example, key=f"example-{index}", width="stretch"):
            clicked = example
    return clicked


def _queue_example() -> None:
    st.session_state.assistant_pending = st.session_state.assistant_example
    st.session_state.assistant_example = None


def example_pills(questions: Sequence[str] = DEMO_QUESTIONS) -> str | None:
    """Demo questions as pills, folded away once a conversation has started; returns the question picked."""
    if st.session_state.get("assistant_turns"):
        with st.expander("Example questions", icon=":material/lightbulb:"):
            st.pills("Ask one of these", questions, key="assistant_example", on_change=_queue_example)
    else:
        st.pills("Try a question", questions, key="assistant_example", on_change=_queue_example)
    return st.session_state.pop("assistant_pending", None)


def chat(agent: PlmAgent | ReplayingAgent, pending: str | None, show_tool_calls: bool) -> None:
    if "assistant_conversation" not in st.session_state:
        new_conversation(agent)

    for question, answer in st.session_state.assistant_turns:
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            render_answer(answer, show_tool_calls)

    question = st.chat_input("Ask about the motor, in English or French") or pending
    if not question:
        return
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            with st.spinner("Querying the PLM data..."):
                answer = agent.ask(st.session_state.assistant_conversation, question)
        except LlmError as exc:
            st.error(f"The model could not answer. {exc}")
            return
        st.session_state.assistant_turns.append((question, answer))
        render_answer(answer, show_tool_calls)


def render(s: Services) -> None:
    head, tools = st.columns([3, 2], vertical_alignment="bottom")
    with head:
        ui.page_header(
            "Assistant",
            "Figures are computed by the analysis code, never by the model. Every answer lists the XML elements and "
            "CSV rows it comes from.",
        )
    agent = load_agent(s.dataset)
    if agent is None:
        return
    with tools, st.container(horizontal=True, horizontal_alignment="right", vertical_alignment="center"):
        show_tool_calls = st.toggle("Show tool calls", value=False)
        if st.button("New conversation", icon=":material/add_comment:"):
            new_conversation(agent)
    st.caption(agent_status(agent, s.dataset))
    if isinstance(agent, ReplayingAgent) and agent.mode == "only" and not s.dataset.reference:
        st.info(
            "Recorded answers only exist for the reference dataset. To ask about this dataset, set "
            "PLM_LLM_API_KEY and PLM_AGENT_REPLAY=fallback (or off) in .env.",
            icon=":material/info:",
        )
    missing = s.enrichment.missing_files()
    if missing:
        st.caption(f"Not provided with this dataset: {', '.join(missing)}. The assistant says so when asked.")

    pending = example_pills(DEMO_QUESTIONS if s.dataset.reference else GENERIC_QUESTIONS)
    chat(agent, pending, show_tool_calls)
