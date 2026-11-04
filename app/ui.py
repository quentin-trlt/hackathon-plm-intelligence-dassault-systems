"""Presentation helpers shared by the views: title block, figure strip, provenance chips, where-used paths.

Data from the OEM extract is drawn plain; data written by the team in data/enrichment/ is hatched.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path

import streamlit as st

from plm.agent.evidence import describe
from plm.model import SourceRef
from services import XML_PATH, Services

STYLE = (Path(__file__).parent / "style.css").read_text(encoding="utf-8")

PLM_BLUE = "#005386"
TEAM_VIOLET = "#6941C6"
INK = "#1C2733"
MUTED = "#5B6B7A"
RULE = "#C9D2DB"
RISK_COLORS = {"high": "#B42318", "medium": "#B54708", "low": "#2E7D5B"}
RISK_BADGES = {"high": "red", "medium": "orange", "low": "green"}


@dataclass(frozen=True)
class Figure:
    label: str
    value: str
    note: str = ""
    team_data: bool = False
    alert: bool = False
    delta: str = ""
    delta_worse: bool | None = None


def inject_style() -> None:
    st.html(f"<style>{STYLE}</style>")


def title_block(s: Services) -> None:
    root = s.plm.root()
    cells = [
        ("tb-product", "Product", root.name, ""),
        ("", "PLM ID", root.plm_id, "tb-mono"),
        ("", "Revision", root.revision, ""),
        ("", "Maturity", root.maturity, ""),
        ("", "Declared mass", f"{root.mass_kg} kg", ""),
        ("", f"OEM extract, {len(s.plm.products())} products", XML_PATH.name, "tb-mono"),
    ]
    body = "".join(
        f'<div class="{cls}"><span class="tb-label">{label}</span>'
        f'<span class="tb-value {value_cls}">{escape(str(value))}</span></div>'
        for cls, label, value, value_cls in cells
    )
    st.html(f'<div class="plm-titleblock" role="group" aria-label="Root assembly">{body}</div>')


def page_header(title: str, lede: str) -> None:
    st.header(title, anchor=False)
    st.html(f'<p class="plm-lede">{escape(lede)}</p>')


def figures(items: Sequence[Figure]) -> None:
    cells = []
    for item in items:
        classes = ["plm-figure", "is-team" if item.team_data else "", "is-high" if item.alert else ""]
        delta = ""
        if item.delta:
            tone = {True: "is-worse", False: "is-better", None: ""}[item.delta_worse]
            delta = f'<div class="plm-figure-delta {tone}">{escape(item.delta)}</div>'
        note = f'<div class="plm-figure-note">{escape(item.note)}</div>' if item.note else ""
        cells.append(
            f'<div class="{" ".join(c for c in classes if c)}">'
            f'<div class="plm-figure-label">{escape(item.label)}</div>'
            f'<div class="plm-figure-value">{escape(item.value)}</div>{delta}{note}</div>'
        )
    st.html(f'<div class="plm-figures">{"".join(cells)}</div>')


def key() -> None:
    st.html(
        '<div class="plm-key">'
        '<span><i class="plm-swatch is-plm"></i>From the OEM extract</span>'
        '<span><i class="plm-swatch is-team"></i>Team data from data/enrichment/, not in the extract</span>'
        "</div>"
    )


def insight(text: str) -> None:
    st.html(f'<p class="plm-insight">{escape(text)}</p>')


def _chip(ref: SourceRef) -> str:
    team = ref.origin == "enrichment"
    if team:
        text = f"{ref.file} L{ref.line}"
    else:
        element = f" {ref.element}" if ref.element else ""
        xml_id = f" {ref.xml_id}" if ref.xml_id else ""
        text = f"XML L{ref.line}{element}{xml_id}"
    cls = "plm-chip is-team" if team else "plm-chip"
    return f'<span class="{cls}" title="{escape(describe(ref))}">{escape(text)}</span>'


def chip_row(refs: Iterable[SourceRef]) -> str:
    return f'<div class="plm-chips">{"".join(_chip(r) for r in refs)}</div>'


def source_chips(refs: Iterable[SourceRef]) -> None:
    unique = list(dict.fromkeys(refs))
    plm = [r for r in unique if r.origin != "enrichment"]
    team = [r for r in unique if r.origin == "enrichment"]
    parts = []
    if plm:
        parts.append(f'<div class="plm-chips-heading">OEM extract, {len(plm)} element(s)</div>')
        parts.append(chip_row(plm))
    if team:
        parts.append(f'<div class="plm-chips-heading">Team data, {len(team)} row(s)</div>')
        parts.append(chip_row(team))
    if parts:
        st.html("".join(parts))


def show_sources(refs: Iterable[SourceRef], title: str = "Where these figures come from") -> None:
    unique = list(dict.fromkeys(refs))
    if not unique:
        return
    with st.expander(f"{title} ({len(unique)})"):
        source_chips(unique)


def paths(rows: Iterable[tuple[Sequence[str], int]]) -> None:
    """One line per where-used path: the assembly chain, then the cumulative quantity."""
    items = []
    for names, quantity in rows:
        steps = [
            f'<span class="step{" is-last" if i == len(names) - 1 else ""}">{escape(name)}</span>'
            for i, name in enumerate(names)
        ]
        chain = '<span class="sep" aria-hidden="true">/</span>'.join(steps)
        unit = "unit" if quantity == 1 else "units"
        items.append(f'<li>{chain}<span class="qty">{quantity} {unit} in motor</span></li>')
    if items:
        st.html(f'<ul class="plm-path">{"".join(items)}</ul>')


def risk_badge(level: str, criticality: float | None = None) -> None:
    text = f"{level.capitalize()} risk" + (f", {criticality:.0f}/100" if criticality is not None else "")
    st.badge(text, color=RISK_BADGES.get(level, "gray"))


def plotly_layout(fig, height: int) -> None:
    fig.update_layout(
        height=height,
        margin=dict(t=8, l=0, r=8, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="IBM Plex Sans, sans-serif", color=INK, size=13),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0, title=None),
        hoverlabel=dict(font_family="IBM Plex Sans, sans-serif"),
    )
