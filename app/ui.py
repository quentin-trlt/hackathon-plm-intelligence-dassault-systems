"""Presentation helpers shared by the views: product sheet, figures, provenance chips, paths, filters, charts.

Data from the OEM extract is drawn plain; data written by the team in data/enrichment/ is hatched.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

from plm.agent.evidence import describe
from plm.model import SourceRef
from plm.parser import MATERIAL_PRICES_FILE, PART_SOURCING_FILE
from services import Services

STYLE = (Path(__file__).parent / "style.css").read_text(encoding="utf-8")

PLM_BLUE = "#005386"
TEAM_VIOLET = "#6941C6"
INK = "#172331"
MUTED = "#5A6878"
RULE = "#D3DAE2"
RISK_COLORS = {"high": "#B42318", "medium": "#B54708", "low": "#2E7D5B"}
RISK_TINTS = {"high": "#FDECEA", "medium": "#FEF3E2", "low": "#E8F4EE"}
RISK_BADGES = {"high": "red", "medium": "orange", "low": "green"}


@dataclass(frozen=True)
class Segment:
    share: float
    tone: str  # plm, team, high, medium, low, muted
    label: str = ""


@dataclass(frozen=True)
class Figure:
    label: str
    value: str
    note: str = ""
    team_data: bool = False
    alert: bool = False
    delta: str = ""
    delta_worse: bool | None = None
    bar: tuple[Segment, ...] = field(default=())


def inject_style() -> None:
    st.html(f"<style>{STYLE}</style>")


def product_sheet(s: Services) -> None:
    """The root assembly and the provenance key, pinned in the sidebar on every page."""
    root = s.plm.root()
    specs = [
        ("Revision", root.revision),
        ("Maturity", root.maturity),
        ("Declared mass", f"{root.mass_kg} kg"),
        ("Products", str(len(s.plm.products()))),
    ]
    rows = "".join(f"<div><dt>{label}</dt><dd>{escape(value)}</dd></div>" for label, value in specs)
    origin = "reference extract" if s.dataset.reference else "uploaded"
    missing = list(s.enrichment.missing_files()) + ([] if s.viewer else ["3D view"])
    team = f"Not provided: {', '.join(missing)}" if missing else "Prices, suppliers, sourcing and 3D view provided"
    st.html(
        '<section class="sb-sheet" aria-label="Root assembly">'
        f'<p class="sb-caption">Root assembly</p><p class="sb-name">{escape(root.name)}</p>'
        f'<p class="sb-id">{escape(root.plm_id)}</p><dl class="sb-specs">{rows}</dl>'
        f'<p class="sb-file">Source: {escape(s.dataset.name)} ({origin})</p>'
        f'<p class="sb-file{" is-missing" if missing else ""}">{escape(team)}</p></section>'
        '<div class="sb-key">'
        '<span><i class="plm-swatch is-plm"></i>OEM extract</span>'
        '<span><i class="plm-swatch is-team"></i>Team data, not in the extract</span>'
        "</div>"
    )


def missing_data(what: str, file: str) -> None:
    """Stands in for a chart or table that needs an enrichment file the active dataset does not have."""
    st.info(f"Missing data: {what} needs {file}, not provided with this dataset.", icon=":material/database_off:")


def missing_figure(label: str, file: str) -> Figure:
    return Figure(label, "Missing data", f"Needs {file}.", team_data=True)


def risk_basis(s: Services) -> None:
    """States which criteria the risk score rests on when enrichment data is missing."""
    groups = (
        (PART_SOURCING_FILE, s.risk.has_sourcing, ["supply", "concentration", "lead time"]),
        (MATERIAL_PRICES_FILE, s.has_prices, ["cost share", "price volatility"]),
    )
    absent = [file for file, loaded, _ in groups if not loaded]
    if not absent:
        return
    scored = ["maturity", "reuse"] + [c for _, loaded, codes in groups if loaded for c in codes]
    left_out = [c for _, loaded, codes in groups if not loaded for c in codes]
    st.info(
        f"Partial risk score: it rests on {', '.join(scored)} only. {' and '.join(absent)} not provided, so "
        f"{', '.join(left_out)} are not scored.",
        icon=":material/database_off:",
    )


def page_header(title: str, lede: str) -> None:
    st.header(title, anchor=False)
    st.html(f'<p class="plm-lede">{escape(lede)}</p>')


def microbar(segments: Sequence[Segment], label: str = "") -> str:
    spans = []
    for seg in segments:
        title = f' title="{escape(seg.label)}"' if seg.label else ""
        spans.append(f'<span class="mb-{seg.tone}" style="width:{max(seg.share, 0) * 100:.2f}%"{title}></span>')
    aria = f' role="img" aria-label="{escape(label)}"' if label else ' aria-hidden="true"'
    return f'<div class="plm-microbar"{aria}>{"".join(spans)}</div>'


def figures(items: Sequence[Figure]) -> None:
    cells = []
    for item in items:
        classes = ["plm-figure", "is-team" if item.team_data else "", "is-high" if item.alert else ""]
        delta = ""
        if item.delta:
            tone = {True: "is-worse", False: "is-better", None: ""}[item.delta_worse]
            delta = f'<div class="plm-figure-delta {tone}">{escape(item.delta)}</div>'
        bar = microbar(item.bar, ", ".join(seg.label for seg in item.bar if seg.label)) if item.bar else ""
        note = f'<div class="plm-figure-note">{escape(item.note)}</div>' if item.note else ""
        cells.append(
            f'<div class="{" ".join(c for c in classes if c)}">'
            f'<div class="plm-figure-label">{escape(item.label)}</div>'
            f'<div class="plm-figure-value">{escape(item.value)}</div>{delta}{bar}{note}</div>'
        )
    st.html(f'<div class="plm-figures">{"".join(cells)}</div>')


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
    with st.expander(f"{title} ({len(unique)})", icon=":material/link:"):
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


def level_style(value: object) -> str:
    if value not in RISK_COLORS:
        return ""
    return f"color: {RISK_COLORS[value]}; background-color: {RISK_TINTS[value]}; font-weight: 600"


def part_filters(parts: pd.DataFrame) -> pd.DataFrame:
    """Material and maturity filters in a popover. Shared keys, so the choice follows the user across pages."""
    with st.popover("Filter parts", icon=":material/filter_list:"):
        materials = st.multiselect(
            "Material", sorted(parts["Material"].unique()), placeholder="All materials", key="filter-materials"
        )
        maturities = st.multiselect(
            "Maturity", sorted(parts["Maturity"].unique()), placeholder="All states", key="filter-maturities"
        )
    if materials:
        parts = parts[parts["Material"].isin(materials)]
    if maturities:
        parts = parts[parts["Maturity"].isin(maturities)]
    return parts


def header_with_filters(title: str, lede: str, parts: pd.DataFrame) -> pd.DataFrame:
    head, action = st.columns([5, 1], vertical_alignment="bottom")
    with head:
        page_header(title, lede)
    with action:
        return part_filters(parts)


def plotly_layout(fig, height: int) -> None:
    fig.update_layout(
        height=height,
        margin=dict(t=8, l=0, r=8, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="IBM Plex Sans, sans-serif", color=INK, size=13),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0, title=None),
        hoverlabel=dict(font_family="IBM Plex Sans, sans-serif", bgcolor="white", bordercolor=RULE),
    )
