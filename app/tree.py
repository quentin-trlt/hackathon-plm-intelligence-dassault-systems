"""Collapsible product tree, a Streamlit v2 component written in plain HTML, CSS and JavaScript (no build step).

The chevron of a node folds and unfolds it in the browser and reports the open nodes as the `expanded` state.
A click on a row reports its id as the `select` trigger.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import streamlit as st

_HERE = Path(__file__).parent
_COMPONENT = st.components.v2.component(
    "plm_structure_tree",
    css=(_HERE / "tree.css").read_text(encoding="utf-8"),
    js=(_HERE / "tree.js").read_text(encoding="utf-8"),
)


def structure_tree(
    rows: list[dict[str, Any]],
    selected: str | None,
    expanded: set[str],
    searching: bool,
    key: str,
    on_select: Callable[[], None],
    on_expanded: Callable[[], None],
) -> None:
    _COMPONENT(
        key=key,
        data={"rows": rows, "selected": selected, "expanded": sorted(expanded), "searching": searching},
        on_select_change=on_select,
        on_expanded_change=on_expanded,
    )
