"""3D view embedded in the pages: the app's viewer (app/assets/viewer3d/), filled with the scene built from the extract.

The viewer sources hold three.js and the drawing code; the dataset only brings the JSON scene. Each view tells the
viewer which parts to frame, how to colour them and whether to show the full viewer or the 3D scene alone. The page
drives the viewer; a click in the 3D view stays inside it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from functools import cache
from html import escape
from pathlib import Path
from typing import Any

import streamlit as st

from plm.parser import PROFILES_FILE, ViewerData
from services import Services

ASSETS = Path(__file__).parent / "assets" / "viewer3d"
SOURCES = {
    "STYLE": "style.css",
    "THREE": "vendor/three.min.js",
    "ORBIT": "vendor/OrbitControls.js",
    "ROOM": "vendor/RoomEnvironment.js",
    "BGU": "vendor/BufferGeometryUtils.js",
    "GEOMETRY": "geometry.js",
    "APP": "app.js",
}
DATA_MARK = "/*PLM_DATA*/null"
CONFIG_MARK = "/*PLM_CONFIG*/{}"
COMPACT_HEIGHT = 380
FULL_HEIGHT = 840
CHECK_DOTS = {"ok": "low", "check": "medium"}
_SOURCE_MARK = re.compile(r"/\*(" + "|".join(SOURCES) + r")\*/")


def _files() -> list[Path]:
    return [ASSETS / "index.html", *(ASSETS / name for name in SOURCES.values())]


@cache
def _assemble(modified_ns: tuple[int, ...]) -> tuple[str, str, str]:
    index = (ASSETS / "index.html").read_text(encoding="utf-8")
    html = _SOURCE_MARK.sub(lambda m: (ASSETS / SOURCES[m.group(1)]).read_text(encoding="utf-8"), index)
    before, rest = html.split(DATA_MARK)
    middle, after = rest.split(CONFIG_MARK)
    return before, middle, after


def _template() -> tuple[str, str, str]:
    """The viewer page split at its two markers, assembled again whenever a source file changes on disk."""
    return _assemble(tuple(path.stat().st_mtime_ns for path in _files()))


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
    text = (
        "Illustrative reconstruction from the XML, not the OEM CAD: shapes come from profiles matched by PLM ID. "
        f"{viewer.placed_from_xml} of {total} positions read from the XML, {total - viewer.placed_from_xml} "
        "reconstructed."
    )
    if viewer.profiles_file is None:
        return text + f" No shape profiles ({PROFILES_FILE}): every part is a marker."
    if viewer.markers:
        return text + f" {len(viewer.markers)} part(s) without a profile are shown as markers."
    return text


def show(
    s: Services,
    *,
    full: bool = False,
    focus: Iterable[str] = (),
    colors: dict[str, str] | None = None,
    legend: list[tuple[str, str]] | None = None,
    color_label: str = "Highlight",
    ghost_others: bool = False,
    height: int | None = None,
) -> None:
    """Draw the 3D view of the dataset, or say why it cannot be drawn."""
    if s.viewer is None:
        st.info(f"The 3D view cannot be drawn for this extract: {s.viewer_problem}", icon=":material/info:")
        return
    root = s.plm.root()
    config = {
        "mode": "full" if full else "compact",
        "focus": [p for p in dict.fromkeys(focus) if s.viewer.covers(p)],
        "colors": {p: c for p, c in colors.items() if s.viewer.covers(p)} if colors is not None else None,
        "legend": legend,
        "color_label": color_label,
        "ghost_others": ghost_others,
        "title": root.name,
        "subtitle": f"3D view · built from {s.dataset.name}",
    }
    st.iframe(page(s.viewer, config), height=height or (FULL_HEIGHT if full else COMPACT_HEIGHT))
    st.caption(caption(s.viewer))
