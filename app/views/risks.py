"""Risks: which parts could stop or slow production, which ones get more expensive, and why."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import nav
import ui
from plm.analysis.risk import PartRisk
from plm.parser import MATERIAL_PRICES_FILE
from services import Services, leaf_frame

ZONE_TINTS = {"high": "rgba(180, 35, 24, 0.16)", "medium": "rgba(224, 138, 46, 0.18)", "low": "rgba(46, 125, 91, 0.11)"}


def _figures(s: Services, ranking: list[PartRisk], price: list[PartRisk]) -> None:
    counts = {level: sum(1 for r in ranking if r.level == level) for level in ("high", "medium", "low")}
    total = len(ranking) or 1
    high_price = sum(1 for r in price if r.price_level == "high")
    ui.figures(
        [
            ui.Figure(
                "High supply risk",
                str(counts["high"]),
                f"Out of {len(ranking)} scored parts.",
                alert=counts["high"] > 0,
                bar=tuple(
                    ui.Segment(counts[level] / total, level, f"{counts[level]} {level}")
                    for level in ("high", "medium", "low")
                ),
            ),
            ui.Figure("Medium supply risk", str(counts["medium"]), "Both axes medium, or one high and the other low."),
            ui.Figure(
                "Most critical part",
                f"{ranking[0].criticality:.0f}/100" if ranking else "None",
                ranking[0].name if ranking else "No scored part in the selection.",
                alert=bool(ranking) and ranking[0].level == "high",
            ),
            ui.Figure(
                "High price risk",
                str(high_price),
                "Delivered, but exposed to a material price rise.",
                team_data=True,
                alert=high_price > 0,
            )
            if s.has_prices
            else ui.missing_figure("High price risk", MATERIAL_PRICES_FILE),
        ]
    )


def _heat_map(s: Services, ranking: list[PartRisk]) -> None:
    st.subheader("Risk heat map", anchor=False)
    cfg = s.risk.config
    edges = (0.0, cfg.band_low, cfg.band_high, 1.02)
    mids = [(edges[k] + edges[k + 1]) / 2 for k in range(3)]
    fig = go.Figure()
    for pi in range(3):
        for ii in range(3):
            fig.add_shape(
                type="rect",
                x0=edges[ii],
                x1=edges[ii + 1],
                y0=edges[pi],
                y1=edges[pi + 1],
                fillcolor=ZONE_TINTS[s.risk.level(mids[pi], mids[ii])],
                line=dict(width=1, color="white"),
                layer="below",
            )
    for level in ("low", "medium", "high"):
        group = [r for r in ranking if r.level == level]
        if not group:
            continue
        fig.add_scatter(
            x=[r.impact for r in group],
            y=[r.probability for r in group],
            mode="markers",
            name=level.capitalize(),
            marker=dict(size=13, color=ui.RISK_COLORS[level], line=dict(width=1.5, color="white"), opacity=0.92),
            customdata=[(r.name, r.criticality) for r in group],
            hovertemplate="<b>%{customdata[0]}</b><br>Impact %{x:.2f}, probability %{y:.2f}"
            "<br>Criticality %{customdata[1]:.1f}/100<extra></extra>",
        )
    ui.plotly_layout(fig, height=420)
    fig.update_xaxes(title="Impact", range=[0, 1.02], showgrid=False, zeroline=False, tickvals=edges[:3] + (1.0,))
    fig.update_yaxes(title="Probability", range=[0, 1.02], showgrid=False, zeroline=False, tickvals=edges[:3] + (1.0,))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    st.caption(
        "Each dot is a scored part. Tinted zones follow the 3 x 3 rule of docs/risk_rules.md: high when one axis "
        "is high and the other at least medium."
    )


def _ranking(ranking: list[PartRisk]) -> None:
    st.subheader("Ranking by criticality", anchor=False)
    table = pd.DataFrame(
        [
            {
                "Part": r.name,
                "Level": r.level,
                "Criticality": r.criticality,
                "Probability": r.probability,
                "Impact": r.impact,
            }
            for r in ranking
        ]
    )
    st.dataframe(
        table.style.map(ui.level_style, subset=["Level"]),
        hide_index=True,
        width="stretch",
        height=420,
        column_config={
            "Part": st.column_config.TextColumn(width="medium"),
            "Level": st.column_config.TextColumn(width=72),
            "Criticality": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f", width=110),
            "Probability": st.column_config.NumberColumn(format="%.2f", width=80),
            "Impact": st.column_config.NumberColumn(format="%.2f", width=64),
        },
    )
    st.caption("Criticality = 100 x probability x impact. Open a part in Product structure to see its criteria.")
    nav.link("structure", "Open Product structure", icon=":material/account_tree:")


def _price_exposure(s: Services, price: list[PartRisk]) -> None:
    st.subheader("Price exposure", anchor=False)
    if not s.has_prices:
        ui.missing_data("price exposure", MATERIAL_PRICES_FILE)
        return
    if not price:
        st.info("None of the filtered parts has a known price volatility.")
        return
    table = pd.DataFrame(
        [
            {
                "Part": r.name,
                "Price risk": r.price_risk,
                "Level": r.price_level,
                "Volatility": r.criterion("PRICE_VOLATILITY").value,
                "Cost share": r.criterion("COST_SHARE").value if r.criterion("COST_SHARE") else "",
                "Supply risk": r.level,
            }
            for r in price
        ]
    )
    st.dataframe(
        table.style.map(ui.level_style, subset=["Level", "Supply risk"]),
        hide_index=True,
        width="stretch",
        height=388,
        column_config={"Price risk": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f")},
    )
    st.caption(
        "Price risk = 100 x material volatility x cost share: the part is delivered but costs more. "
        "Volatility classes are team data from data/enrichment/material_prices.csv."
    )


def _reuse(s: Services, ranking: list[PartRisk]) -> None:
    st.subheader("Parts used in several places", anchor=False)
    shared = []
    for r in ranking:
        occurrences = s.plm.where_used(r.plm_id)
        if len(occurrences) > 1:
            shared.append(
                {
                    "Part": r.name,
                    "Used in": len(occurrences),
                    "Parents": ", ".join(s.plm.product(o.path[-2]).name for o in occurrences),
                    "Units in motor": s.plm.total_quantity(r.plm_id).value,
                    "Risk level": r.level,
                }
            )
    if not shared:
        st.info("No scored part of the selection is used in more than one place.")
        return
    st.dataframe(
        pd.DataFrame(shared).style.map(ui.level_style, subset=["Risk level"]),
        hide_index=True,
        width="stretch",
    )
    st.caption("A problem on these parts spreads to every place they are used (REUSE criterion, OEM extract).")


def render(s: Services) -> None:
    parts = ui.header_with_filters(
        "Risks",
        "Which parts could stop or slow production, which ones would cost more if material prices rise, and why.",
        leaf_frame(s),
    )
    if parts.empty:
        st.warning("No part matches the selected filters. Remove a material or a maturity state.")
        return
    selection = set(parts["PLM ID"])
    ranking = [r for r in s.risk.ranking() if r.plm_id in selection]
    price = [r for r in s.risk.price_ranking() if r.plm_id in selection]
    if not ranking:
        st.info("None of the filtered parts is scored. Clear the filters to see the risks.")
        return

    ui.risk_basis(s)
    _figures(s, ranking, price)
    left, right = st.columns([1, 1], gap="large")
    with left:
        _heat_map(s, ranking)
    with right:
        _ranking(ranking)
    _price_exposure(s, price)
    _reuse(s, ranking)
    ui.show_sources([ref for r in ranking for ref in r.sources], "Where the risk scores come from")
