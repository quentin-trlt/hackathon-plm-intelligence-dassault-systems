"""3D view embedded in the pages: the app's own viewer template, filled with the checked data of the dataset.

The template (app/assets/viewer_3d_template.html) holds three.js and the drawing code; the dataset only brings the
JSON parts, positions and shapes. Each view tells the template which parts to frame, how to colour them and whether to
show the full viewer or the 3D scene alone. The page drives the viewer; a click in the 3D view stays inside it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from functools import cache
from html import escape
from pathlib import Path
from typing import Any

import streamlit as st

import ui
from plm.parser import VIEWER_FILE, ViewerData
from services import Services

TEMPLATE = Path(__file__).parent / "assets" / "viewer_3d_template.html"
DATA_MARK = "/*PLM_DATA*/null"
CONFIG_MARK = "/*PLM_CONFIG*/{}"
COMPACT_HEIGHT = 380
FULL_HEIGHT = 840
CHECK_DOTS = {"ok": "low", "check": "medium"}


@cache
def _split_template(modified_ns: int) -> tuple[str, str, str]:
    text = TEMPLATE.read_text(encoding="utf-8")
    before, rest = text.split(DATA_MARK)
    middle, after = rest.split(CONFIG_MARK)
    return before, middle, after


def _template() -> tuple[str, str, str]:
    """The template split at its two markers, read again whenever the file changes on disk."""
    return _split_template(TEMPLATE.stat().st_mtime_ns)


def _script_json(value: Any) -> str:
    """JSON safe inside a <script> element: no character can close the element or start markup."""
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def page(viewer: ViewerData, config: dict[str, Any]) -> str:
    before, middle, after = _template()
    return before + _script_json(viewer.data) + middle + _script_json(config) + after


def consistency_checks(s: Services) -> None:
    """The checks of the 3D view against the extract, as a list under the viewer."""
    if s.viewer is None:
        return
    st.markdown("**Consistency checks**")
    rows = "".join(
        f'<p class="att-text"><i class="dot is-{CHECK_DOTS[c.status]}" title="{c.status}"></i>'
        f"<span>{escape(c.text)}</span></p>"
        for c in s.viewer.checks(s.plm.root().mass_kg)
    )
    st.html(f'<div class="viewer-checks">{rows}</div>')


def caption(viewer: ViewerData) -> str:
    total = len(viewer.items)
    return (
        "Schematic shapes sized from mass and typical density, not the OEM CAD. "
        f"{viewer.placed_from_xml} of {total} positions read from the XML, {total - viewer.placed_from_xml} estimated."
    )


def show(
    s: Services,
    *,
    what: str = "the 3D view",
    full: bool = False,
    focus: Iterable[str] = (),
    colors: dict[str, str] | None = None,
    legend: list[tuple[str, str]] | None = None,
    color_label: str = "Highlight",
    ghost_others: bool = False,
    height: int | None = None,
) -> None:
    """Draw the 3D view of the dataset, or say that it was not provided."""
    if s.viewer is None:
        ui.missing_data(what, VIEWER_FILE)
        return
    root = s.plm.root()
    config = {
        "mode": "full" if full else "compact",
        "focus": [p for p in dict.fromkeys(focus) if s.viewer.covers(p)],
        "colors": {p: c for p, c in colors.items() if s.viewer.covers(p)} if colors is not None else None,
        "legend": legend,
        "color_label": color_label,
        "ghost_others": ghost_others,
        "title": f"{root.name} · 3D view",
        "subtitle": f"Built from {s.dataset.name}",
        "root_mass": root.mass_kg,
    }
    st.iframe(page(s.viewer, config), height=height or (FULL_HEIGHT if full else COMPACT_HEIGHT))
    st.caption(caption(s.viewer))
