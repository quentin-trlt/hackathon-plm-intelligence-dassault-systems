"""Test page for the AI assistant, independent from the main app. Same chat as the app's Assistant view.

streamlit run app/assistant.py
"""

from __future__ import annotations

import streamlit as st

from views.assistant import agent_status, chat, example_buttons, load_agent, new_conversation


def main() -> None:
    st.set_page_config(page_title="PLM assistant", layout="centered")
    st.title("PLM assistant")

    agent = load_agent()
    if agent is None:
        st.stop()

    with st.sidebar:
        st.caption(agent_status(agent))
        show_tool_calls = st.toggle("Show tool calls", value=True)
        if st.button("New conversation", width="stretch"):
            new_conversation(agent)
        st.subheader("Examples")
        pending = example_buttons()

    chat(agent, pending, show_tool_calls)


main()
