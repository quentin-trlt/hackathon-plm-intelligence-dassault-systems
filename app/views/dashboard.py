"""Overview: where the material cost, the mass and the risk of the motor sit, across its leaf parts."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import ui
from services import NON_PLM, Services, leaf_frame


def _filters(parts: pd.DataFrame) -> pd.DataFrame:
    with st.popover("Filter parts", icon=":material/filter_list:"):
        materials = st.multiselect("Material", sorted(parts["Material"].unique()), placeholder="All materials")
        maturities = st.multiselect("Maturity", sorted(parts["Maturity"].unique()), placeholder="All states")
    if materials:
        parts = parts[parts["Material"].isin(materials)]
    if maturities:
        parts = parts[parts["Maturity"].isin(maturities)]
    return parts


def _figures(s: Services, parts: pd.DataFrame) -> None:
    root = s.plm.root()
    cost_complete = parts["Cost in motor (EUR)"].notna().all()
    single = int((parts["Suppliers"] == 1).sum())
    high = int((parts["Risk level"] == "high").sum())
    ui.figures(
        [
            ui.Figure(
                "Mass of the leaf parts",
                f"{parts['Mass in motor (kg)'].sum():.2f} kg",
                f"Unit mass x quantity. Declared root mass {root.mass_kg} kg.",
            ),
            ui.Figure(
                "Leaf parts", str(len(parts)), f"{int(parts['Qty in motor'].sum())} physical units in the motor."
            ),
            ui.Figure(
                "Material cost",
                f"{parts['Cost in motor (EUR)'].sum():.2f} EUR" if cost_complete else "Incomplete",
                "Mass x material price. Raw material only, not the purchase price.",
                team_data=True,
            ),
            ui.Figure(
                "High-risk parts", str(high), "High probability or impact, the other at least medium.", alert=high > 0
            ),
            ui.Figure("Single-sourced parts", str(single), "One supplier recorded.", team_data=True, alert=single > 0),
        ]
    )
    ui.key()


def _critical_parts(s: Services, plm_ids: set[str]) -> None:
    st.subheader("Critical parts", anchor=False)
    ranking = [r for r in s.risk.ranking() if r.plm_id in plm_ids]
    if not ranking:
        st.info("None of the filtered parts is scored. Clear the filters to see the ranking.")
        return
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
        table.style.map(lambda v: f"color: {ui.RISK_COLORS.get(v, 'inherit')}; font-weight: 600", subset=["Level"]),
        hide_index=True,
        width="stretch",
        height=388,
        column_config={
            "Criticality": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
            "Probability": st.column_config.NumberColumn(format="%.2f"),
            "Impact": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    st.caption(
        "Criticality = 100 x probability x impact, rules in docs/risk_rules.md. Open a part in Product structure "
        "to see the criteria behind its score."
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
        column_config={"Cost at stake": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent")},
    )
    st.caption(f"Share of the material cost that stops if the supplier fails. All columns are {NON_PLM}.")


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
    ui.plotly_layout(fig, height=max(320, 46 * len(by_material) + 60))
    fig.update_layout(barmode="group", bargap=0.28, bargroupgap=0.08)
    fig.update_xaxes(tickformat=".0%", gridcolor=ui.RULE, zeroline=False)
    fig.update_yaxes(title=None, automargin=True)
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    st.caption("Prices from data/enrichment/material_prices.csv.")


def render(s: Services) -> None:
    head, action = st.columns([5, 1], vertical_alignment="bottom")
    with head:
        ui.page_header(
            "Overview",
            "The leaf parts of the motor, the physical parts that carry the material, the suppliers and the prices.",
        )
    with action:
        parts = _filters(leaf_frame(s))
    if parts.empty:
        st.warning("No part matches the selected filters. Remove a material or a maturity state.")
        return

    _figures(s, parts)
    left, right = st.columns([1, 1], gap="large")
    with left:
        _critical_parts(s, set(parts["PLM ID"]))
    with right:
        _suppliers(s)
    _materials(parts)

    with st.expander(f"All leaf parts ({len(parts)})"):
        st.dataframe(
            parts,
            hide_index=True,
            width="stretch",
            column_config={
                "Unit mass (kg)": st.column_config.NumberColumn(format="%.3f"),
                "Mass in motor (kg)": st.column_config.NumberColumn(format="%.3f"),
                "Unit cost (EUR)": st.column_config.NumberColumn(format="%.2f"),
                "Cost in motor (EUR)": st.column_config.NumberColumn(format="%.2f"),
                "Criticality": st.column_config.NumberColumn(format="%.1f"),
            },
        )
