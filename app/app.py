"""PLM Intelligence: main Streamlit application.

streamlit run app/app.py
"""

from __future__ import annotations

import streamlit as st

import ui
from plm.parser import EnrichmentError, PlmParseError, PlmValidationError
from services import Services, get_services
from views import assistant, dashboard, explorer, impact


def pages(s: Services) -> list[st.Page]:
    def overview() -> None:
        dashboard.render(s)

    def explore() -> None:
        explorer.render(s)

    def change() -> None:
        impact.render(s)

    def ask() -> None:
        assistant.render(s)

    return [
        st.Page(overview, title="Overview", icon=":material/space_dashboard:", url_path="overview", default=True),
        st.Page(explore, title="Product structure", icon=":material/account_tree:", url_path="structure"),
        st.Page(change, title="Change impact", icon=":material/swap_horiz:", url_path="impact"),
        st.Page(ask, title="Assistant", icon=":material/forum:", url_path="assistant"),
    ]


def main() -> None:
    st.set_page_config(page_title="PLM Intelligence", page_icon=":material/settings_input_component:", layout="wide")
    ui.inject_style()
    try:
        services = get_services()
    except (FileNotFoundError, PlmParseError, PlmValidationError, EnrichmentError) as exc:
        st.error(f"The PLM data could not be loaded: {exc}. Check that data/raw/ and data/enrichment/ are present.")
        st.stop()

    page = st.navigation(pages(services), position="top")
    ui.title_block(services)
    for warning in services.plm.warnings:
        st.warning(warning)
    page.run()


main()
