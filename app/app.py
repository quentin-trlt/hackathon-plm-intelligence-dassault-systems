"""PLM Intelligence: main Streamlit application.

streamlit run app/app.py
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

import nav
import ui
from plm.parser import EnrichmentError, PlmParseError, PlmValidationError
from services import Services, get_services
from views import assistant, dashboard, explorer, impact, risks, supply

ASSETS = Path(__file__).parent / "assets"


def pages(s: Services) -> dict[str, list[st.Page]]:
    def overview() -> None:
        dashboard.render(s)

    def structure() -> None:
        explorer.render(s)

    def risk() -> None:
        risks.render(s)

    def sourcing() -> None:
        supply.render(s)

    def change() -> None:
        impact.render(s)

    def ask() -> None:
        assistant.render(s)

    nav.PAGES.update(
        overview=st.Page(
            overview, title="Overview", icon=":material/space_dashboard:", url_path="overview", default=True
        ),
        structure=st.Page(structure, title="Product structure", icon=":material/account_tree:", url_path="structure"),
        risks=st.Page(risk, title="Risks", icon=":material/crisis_alert:", url_path="risks"),
        supply=st.Page(sourcing, title="Supply and materials", icon=":material/local_shipping:", url_path="supply"),
        impact=st.Page(change, title="Change impact", icon=":material/swap_horiz:", url_path="impact"),
        assistant=st.Page(ask, title="Assistant", icon=":material/forum:", url_path="assistant"),
    )
    p = nav.PAGES
    return {
        "Understand": [p["overview"], p["structure"]],
        "Identify risks": [p["risks"], p["supply"]],
        "Assess a change": [p["impact"]],
        "Ask": [p["assistant"]],
    }


def main() -> None:
    st.set_page_config(page_title="PLM Intelligence", page_icon=str(ASSETS / "logo_icon.svg"), layout="wide")
    st.logo(str(ASSETS / "logo.svg"), size="large", icon_image=str(ASSETS / "logo_icon.svg"))
    ui.inject_style()
    try:
        services = get_services()
    except (FileNotFoundError, PlmParseError, PlmValidationError, EnrichmentError) as exc:
        st.error(f"The PLM data could not be loaded: {exc}. Check that data/raw/ and data/enrichment/ are present.")
        st.stop()

    page = st.navigation(pages(services), position="sidebar", expanded=True)
    with st.sidebar:
        ui.product_sheet(services)
    for warning in services.plm.warnings:
        st.warning(warning)
    page.run()


main()
