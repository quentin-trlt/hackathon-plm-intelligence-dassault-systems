"""Dashboard: mass, material cost and risk of the leaf parts, with material and maturity slicers."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from services import NON_PLM, Services, leaf_frame

LEVEL_COLORS = {"high": "#DC2626", "medium": "#F59E0B", "low": "#16A34A"}


def _short_id(plm_id: str) -> str:
    return plm_id.rsplit("-", 1)[-1]


def _kpis(s: Services, parts: pd.DataFrame) -> None:
    root = s.plm.root()
    root_cost = s.cost.unit_cost(root.plm_id)
    cost_complete = parts["Cost in motor (EUR)"].notna().all()
    cost_help = f"Mass x material price ({NON_PLM}). Material only, not the purchase price."
    if root_cost is not None:
        cost_help += f" Whole motor: {root_cost.value:.2f} EUR."
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric(
        "Mass in motor",
        f"{parts['Mass in motor (kg)'].sum():.2f} kg",
        help=f"Unit mass x quantity of the selected leaf parts. Declared root mass: {root.mass_kg} kg.",
    )
    k2.metric("Leaf parts", len(parts), help=f"{int(parts['Qty in motor'].sum())} physical units in the motor.")
    k3.metric(
        "Material cost",
        f"{parts['Cost in motor (EUR)'].sum():.2f} EUR" if cost_complete else "incomplete",
        help=cost_help,
    )
    k4.metric("High-risk parts", int((parts["Risk level"] == "high").sum()))
    k5.metric("Single-sourced parts", int((parts["Suppliers"] == 1).sum()), help=NON_PLM)


def _charts(parts: pd.DataFrame) -> None:
    visual = parts.assign(Label=parts["Part"] + " · " + parts["PLM ID"].map(_short_id))
    left, right = st.columns(2)
    with left:
        st.subheader("Mass by material")
        fig = px.treemap(
            visual,
            path=["Material", "Label"],
            values="Mass in motor (kg)",
            hover_data={"PLM ID": True, "Qty in motor": True},
        )
        fig.update_layout(margin=dict(t=10, l=0, r=0, b=0), height=420)
        st.plotly_chart(fig, width="stretch")
    with right:
        st.subheader("Material cost by material")
        by_material = (
            visual.groupby("Material", as_index=False)["Cost in motor (EUR)"]
            .sum()
            .sort_values("Cost in motor (EUR)", ascending=False)
        )
        fig = px.bar(by_material, x="Cost in motor (EUR)", y="Material", orientation="h", text_auto=".1f")
        fig.update_layout(margin=dict(t=10, l=0, r=0, b=0), height=420, yaxis=dict(autorange="reversed", title=None))
        st.plotly_chart(fig, width="stretch")
        st.caption(f"Prices: {NON_PLM} (data/enrichment/material_prices.csv).")


def _risk(s: Services, plm_ids: set[str]) -> None:
    st.subheader("Critical parts")
    ranking = [r for r in s.risk.ranking() if r.plm_id in plm_ids]
    if not ranking:
        st.info("No scored part in the selection.")
        return
    table = pd.DataFrame(
        [
            {
                "Part": r.name,
                "PLM ID": r.plm_id,
                "Level": r.level,
                "Criticality": r.criticality,
                "Probability": r.probability,
                "Impact": r.impact,
                "Price risk": r.price_risk,
            }
            for r in ranking
        ]
    )
    st.dataframe(
        table.style.map(lambda v: f"color: {LEVEL_COLORS.get(v, 'inherit')}; font-weight: 600", subset=["Level"]),
        hide_index=True,
        width="stretch",
        column_config={
            "Criticality": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f"),
            "Probability": st.column_config.NumberColumn(format="%.2f"),
            "Impact": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    st.caption(
        "Criticality = 100 x probability x impact, rules in docs/risk_rules.md. Supplier, lead time and price data "
        f"are {NON_PLM} team assumptions. Open a part in the Product Explorer to see why it is critical."
    )

    st.subheader("Supplier exposure")
    exposure = pd.DataFrame(
        [
            {
                "Supplier": e.name,
                "ID": e.supplier_id,
                "Country": e.country,
                "Parts delivered": len(e.parts),
                "Single-sourced parts": len(e.single_sourced_parts),
                "Material cost at stake": e.single_sourced_cost_share,
            }
            for e in s.risk.supplier_exposure()
            if e.parts
        ]
    )
    st.dataframe(
        exposure,
        hide_index=True,
        width="stretch",
        column_config={"Material cost at stake": st.column_config.NumberColumn(format="percent")},
    )
    st.caption(f"Share of the material cost that stops if the supplier fails ({NON_PLM}).")


def render(s: Services) -> None:
    st.header("Dashboard")
    st.caption("Leaf parts of the motor: physical parts carrying the material, the suppliers and the prices.")

    parts = leaf_frame(s)
    c1, c2 = st.columns(2)
    materials = c1.multiselect("Material", sorted(parts["Material"].unique()), placeholder="All materials")
    maturities = c2.multiselect("Maturity", sorted(parts["Maturity"].unique()), placeholder="All states")
    if materials:
        parts = parts[parts["Material"].isin(materials)]
    if maturities:
        parts = parts[parts["Maturity"].isin(maturities)]
    if parts.empty:
        st.warning("No part matches the selected filters.")
        return

    _kpis(s, parts)
    _charts(parts)
    _risk(s, set(parts["PLM ID"]))

    st.subheader("Leaf parts")
    st.dataframe(
        parts,
        hide_index=True,
        width="stretch",
        column_config={
            "Unit mass (kg)": st.column_config.NumberColumn(format="%.3f"),
            "Mass in motor (kg)": st.column_config.NumberColumn(format="%.3f"),
            "Unit cost (EUR)": st.column_config.NumberColumn(format="%.2f"),
            "Cost in motor (EUR)": st.column_config.NumberColumn(format="%.2f"),
        },
    )
