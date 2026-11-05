"""Supply and materials: who the motor depends on, and where its material cost comes from."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import ui
from services import NON_PLM, Services, leaf_frame


def _figures(s: Services, parts: pd.DataFrame) -> None:
    exposure = [e for e in s.risk.supplier_exposure() if e.parts]
    single = int((parts["Suppliers"] == 1).sum())
    top = exposure[0] if exposure else None
    cost = parts.groupby("Material")["Cost in motor (EUR)"].sum().sort_values(ascending=False)
    cost_total = cost.sum()
    ui.figures(
        [
            ui.Figure("Suppliers", str(len(exposure)), "Delivering at least one part of the motor.", team_data=True),
            ui.Figure(
                "Single-sourced parts",
                f"{single} of {len(parts)}",
                "No alternative source recorded.",
                team_data=True,
                alert=single > 0,
                bar=(ui.Segment(single / len(parts), "team", f"{single} single-sourced"),),
            ),
            ui.Figure(
                "Largest single-source stake",
                f"{top.single_sourced_cost_share:.0%}" if top else "None",
                f"Of the material cost, held by {top.name}." if top else "",
                team_data=True,
                alert=bool(top) and top.single_sourced_cost_share > 0.25,
            ),
            ui.Figure(
                "Costliest material",
                f"{cost.iloc[0] / cost_total:.0%}" if cost_total else "Unknown",
                f"Of the material cost: {cost.index[0]}." if cost_total else "",
                team_data=True,
                bar=(ui.Segment(cost.iloc[0] / cost_total, "team"),) if cost_total else (),
            ),
        ]
    )


def _suppliers(s: Services) -> None:
    st.subheader("Supplier exposure", anchor=False)
    exposure = pd.DataFrame(
        [
            {
                "Supplier": e.name,
                "Country": e.country,
                "Parts": len(e.parts),
                "Single-sourced": len(e.single_sourced_parts),
                "Cost at stake": e.single_sourced_cost_share,
            }
            for e in s.risk.supplier_exposure()
            if e.parts
        ]
    )
    st.dataframe(
        exposure,
        hide_index=True,
        width="stretch",
        height=388,
        column_config={
            "Supplier": st.column_config.TextColumn(width=165),
            "Country": st.column_config.TextColumn(width=60),
            "Parts": st.column_config.NumberColumn(width=50),
            "Single-sourced": st.column_config.NumberColumn(width=96),
            "Cost at stake": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent", width=110),
        },
    )
    st.caption(f"Share of the material cost that stops if the supplier fails. All columns are {NON_PLM}.")


def _single_sourced(s: Services, selection: set[str]) -> None:
    st.subheader("Single-sourced parts", anchor=False)
    rows = []
    for r in s.risk.ranking():
        sourcing = s.enrichment.sourcing(r.plm_id)
        if r.plm_id in selection and len(sourcing) == 1:
            rows.append(
                {
                    "Part": r.name,
                    "Supplier": s.enrichment.supplier(sourcing[0].supplier_id).name,
                    "Make/buy": sourcing[0].make_or_buy,
                    "Lead time (days)": sourcing[0].lead_time_days,
                    "Risk level": r.level,
                }
            )
    if not rows:
        st.info("No single-sourced part in the selection.")
        return
    st.dataframe(
        pd.DataFrame(rows).style.map(ui.level_style, subset=["Risk level"]),
        hide_index=True,
        width="stretch",
        height=388,
        column_config={
            "Part": st.column_config.TextColumn(width=160),
            "Supplier": st.column_config.TextColumn(width=160),
            "Make/buy": st.column_config.TextColumn(width=72),
            "Lead time (days)": st.column_config.NumberColumn(width=110),
            "Risk level": st.column_config.TextColumn(width=76),
        },
    )
    st.caption(
        "No alternative source if the supplier fails. Team data. The two stator meshing artefacts are not scored "
        "and not listed here."
    )


def _materials(parts: pd.DataFrame) -> None:
    st.subheader("Mass and cost by material", anchor=False)
    by_material = parts.groupby("Material", as_index=False)[["Mass in motor (kg)", "Cost in motor (EUR)"]].sum()
    by_material["Mass share"] = by_material["Mass in motor (kg)"] / by_material["Mass in motor (kg)"].sum()
    cost_total = by_material["Cost in motor (EUR)"].sum()
    by_material["Cost share"] = by_material["Cost in motor (EUR)"] / cost_total if cost_total else 0.0
    by_material = by_material.sort_values("Cost share", ascending=True)

    gap = by_material.assign(Gap=by_material["Cost share"] - by_material["Mass share"]).sort_values("Gap").iloc[-1]
    if len(by_material) > 1 and gap["Gap"] > 0.05:
        ui.insight(
            f"{gap['Material']} is {gap['Mass share']:.0%} of the mass but {gap['Cost share']:.0%} of the material "
            "cost: a price move on this material weighs the most on the motor."
        )

    fig = go.Figure()
    fig.add_bar(
        y=by_material["Material"],
        x=by_material["Mass share"],
        name="Share of mass (OEM extract)",
        orientation="h",
        marker_color=ui.PLM_BLUE,
        customdata=by_material["Mass in motor (kg)"],
        hovertemplate="%{y}<br>%{x:.1%} of the mass, %{customdata:.2f} kg<extra></extra>",
    )
    fig.add_bar(
        y=by_material["Material"],
        x=by_material["Cost share"],
        name="Share of material cost (team prices)",
        orientation="h",
        marker=dict(
            color="rgba(105, 65, 198, 0.18)",
            line=dict(color=ui.TEAM_VIOLET, width=1),
            pattern=dict(shape="/", fgcolor=ui.TEAM_VIOLET, size=6, solidity=0.25),
        ),
        customdata=by_material["Cost in motor (EUR)"],
        hovertemplate="%{y}<br>%{x:.1%} of the material cost, %{customdata:.2f} EUR<extra></extra>",
    )
    ui.plotly_layout(fig, height=max(320, 40 * len(by_material) + 60))
    fig.update_layout(barmode="group", bargap=0.3, bargroupgap=0.08)
    fig.update_xaxes(tickformat=".0%", gridcolor=ui.RULE, zeroline=False)
    fig.update_yaxes(title=None, automargin=True)
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    st.caption("Prices from data/enrichment/material_prices.csv.")


def _material_dependency(s: Services) -> None:
    st.subheader("Material dependency", anchor=False)
    exposure = s.risk.material_exposure()
    table = pd.DataFrame(
        [
            {
                "Material": m.material,
                "Parts": len(m.parts),
                "Units": m.units,
                "Mass (kg)": m.mass_kg,
                "Mass share": m.mass_share,
                "Material cost (EUR)": m.cost_eur,
                "Cost share": m.cost_share,
                "Suppliers": len(m.suppliers),
                "Single-sourced parts": len(m.single_sourced_parts),
                "Volatility": m.price_volatility or "unknown",
            }
            for m in exposure
        ]
    )
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        column_config={
            "Mass (kg)": st.column_config.NumberColumn(format="%.2f"),
            "Mass share": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent"),
            "Material cost (EUR)": st.column_config.NumberColumn(format="%.2f"),
            "Cost share": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent"),
        },
    )
    st.caption(
        "What stops or gets more expensive if a material is short or rises. Whole motor, independent of the "
        "filters, stator meshing artefacts excluded. Suppliers and volatility are team data."
    )
    ui.show_sources([ref for m in exposure for ref in m.sources], "Where the material dependency comes from")


def render(s: Services) -> None:
    parts = ui.header_with_filters(
        "Supply and materials",
        "Who the motor depends on, how much of the material cost each supplier holds, and which materials weigh "
        "the most on the cost.",
        leaf_frame(s),
    )
    if parts.empty:
        st.warning("No part matches the selected filters. Remove a material or a maturity state.")
        return
    _figures(s, parts)
    left, right = st.columns([9, 11], gap="medium")
    with left:
        _suppliers(s)
    with right:
        _single_sourced(s, set(parts["PLM ID"]))
    _materials(parts)
    _material_dependency(s)
