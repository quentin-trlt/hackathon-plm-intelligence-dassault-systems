"""Registry of the app pages, filled by app.py on every run so that views can link to each other."""

from __future__ import annotations

import streamlit as st

PAGES: dict[str, st.Page] = {}


def page(name: str) -> st.Page:
    if name not in PAGES:
        raise KeyError(f"Unknown page {name!r}: app.py registers {sorted(PAGES)}")
    return PAGES[name]


def link(name: str, label: str, icon: str | None = None) -> None:
    """A link to another page, or nothing when the view runs alone (tests, standalone pages)."""
    if name in PAGES:
        st.page_link(PAGES[name], label=label, icon=icon)
