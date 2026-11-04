"""PLM Intelligence: main Streamlit application.

streamlit run app/app.py
"""

from __future__ import annotations

import streamlit as st

from plm.parser import EnrichmentError, PlmParseError, PlmValidationError
from services import NON_PLM, XML_PATH, Services, get_services
from views import assistant, dashboard, explorer, impact

PAGES = {
    "Dashboard": dashboard.render,
    "Product Explorer": explorer.render,
    "Change Impact": impact.render,
    "Assistant": assistant.render,
}

STYLE = """
<style>
    h1, h2, h3, h4 { color: #005686 !important; letter-spacing: -0.01em; }
    [data-testid="stMetricValue"] { color: #005686; font-weight: 700; }
    .plm-banner {
        display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;
        border-bottom: 2px solid #E2E8F0; padding-bottom: 0.6rem; margin-bottom: 1rem;
    }
    .plm-title { font-size: 1.3rem; font-weight: 800; color: #005686; }
    .plm-sub { font-size: 0.75rem; font-weight: 700; color: #0072B2; letter-spacing: 0.08em; text-transform: uppercase; }
    .plm-pill {
        background: #EBF4F9; color: #005686; border: 1px solid #BFDBFE;
        padding: 4px 10px; border-radius: 6px; font-size: 0.8rem; font-weight: 600;
    }
</style>
"""


def banner(s: Services) -> None:
    root = s.plm.root()
    st.markdown(
        f"""
        <div class="plm-banner">
            <div>
                <div class="plm-title">PLM Intelligence</div>
                <div class="plm-sub">OEM 3DEXPERIENCE extract · traceable engineering decisions</div>
            </div>
            <span class="plm-pill">{root.name} · {root.mass_kg} kg</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def sidebar(s: Services) -> str:
    root = s.plm.root()
    with st.sidebar:
        page = st.radio("View", list(PAGES), label_visibility="collapsed")
        st.divider()
        st.markdown(
            f"**Root assembly**  \n`{root.name}`  \n"
            f"PLM ID `{root.plm_id}`  \n"
            f"Rev. {root.revision} · {root.maturity} · {root.mass_kg} kg"
        )
        st.caption(
            f"Source: {XML_PATH.name} ({len(s.plm.products())} products, export {s.plm.export_info.completion}). "
            f"Prices, suppliers and sourcing come from data/enrichment/ and are labelled {NON_PLM}."
        )
        for warning in s.plm.warnings:
            st.warning(warning)
    return page


def main() -> None:
    st.set_page_config(page_title="PLM Intelligence", layout="wide")
    st.markdown(STYLE, unsafe_allow_html=True)
    try:
        services = get_services()
    except (FileNotFoundError, PlmParseError, PlmValidationError, EnrichmentError) as exc:
        st.error(f"Cannot load the PLM data: {exc}")
        st.stop()

    page = sidebar(services)
    banner(services)
    PAGES[page](services)


main()
