"""Dashboard: mass, material cost and risk of the leaf parts, with material and maturity slicers."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from plm.analysis.risk import MESHING_ARTEFACTS
from plm.analysis.summary import subassembly_summary
from services import NON_PLM, Services, leaf_frame, show_sources

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
            "Price risk": st.column_config.NumberColumn(format="%.1f"),
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


def _subassemblies(s: Services) -> None:
    st.subheader("By subassembly")
    rows = []
    for sub in subassembly_summary(s.plm, s.cost, s.risk):
        rows.append(
            {
                "Subassembly": sub.name,
                "Mass (kg)": sub.mass_kg,
                "Mass share": sub.mass_share,
                "Material cost (EUR)": sub.cost_eur,
                "Cost share": sub.cost_share,
                "Scored parts": sub.parts,
                "High risk": sub.count_by_level["high"],
                "Medium risk": sub.count_by_level["medium"],
                "High price risk": sub.high_price_risk_parts,
                "Most critical part": sub.worst.name if sub.worst else "",
            }
        )
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            "Mass (kg)": st.column_config.NumberColumn(format="%.2f"),
            "Mass share": st.column_config.NumberColumn(format="percent"),
            "Material cost (EUR)": st.column_config.NumberColumn(format="%.2f"),
            "Cost share": st.column_config.NumberColumn(format="percent"),
        },
    )
    st.caption(
        f"Whole product, independent of the filters. Mass from the XML; cost = mass x material price ({NON_PLM})."
    )


def _heat_map(s: Services, plm_ids: set[str]) -> None:
    st.subheader("Risk heat map")
    ranking = [r for r in s.risk.ranking() if r.plm_id in plm_ids]
    if not ranking:
        return
    frame = pd.DataFrame(
        [
            {
                "Part": r.name,
                "PLM ID": r.plm_id,
                "Probability": r.probability,
                "Impact": r.impact,
                "Level": r.level,
                "Criticality": r.criticality,
            }
            for r in ranking
        ]
    )
    fig = px.scatter(
        frame,
        x="Impact",
        y="Probability",
        color="Level",
        color_discrete_map=LEVEL_COLORS,
        hover_data={"Part": True, "PLM ID": True, "Criticality": ":.1f"},
        range_x=[0, 1.02],
        range_y=[0, 1.02],
    )
    for value in (0.33, 0.66):
        fig.add_shape(type="line", x0=value, x1=value, y0=0, y1=1.02, line=dict(color="#94A3B8", dash="dot"))
        fig.add_shape(type="line", y0=value, y1=value, x0=0, x1=1.02, line=dict(color="#94A3B8", dash="dot"))
    fig.update_traces(marker=dict(size=12, line=dict(width=1, color="white")))
    fig.update_layout(margin=dict(t=10, l=0, r=0, b=0), height=420)
    st.plotly_chart(fig, width="stretch")
    st.caption(
        "Each dot is a scored part. Dotted lines are the low / medium / high bands of each axis "
        "(docs/risk_rules.md). Top right = act first."
    )


def _price_exposure(s: Services, plm_ids: set[str]) -> None:
    st.subheader("Price exposure")
    ranking = [r for r in s.risk.price_ranking() if r.plm_id in plm_ids]
    if not ranking:
        st.info("No part with a known price volatility in the selection.")
        return
    table = pd.DataFrame(
        [
            {
                "Part": r.name,
                "PLM ID": r.plm_id,
                "Price risk": r.price_risk,
                "Level": r.price_level,
                "Material volatility": r.criterion("PRICE_VOLATILITY").value,
                "Cost share": r.criterion("COST_SHARE").value if r.criterion("COST_SHARE") else "",
                "Supply risk": r.level,
            }
            for r in ranking
        ]
    )
    st.dataframe(
        table.style.map(lambda v: f"color: {LEVEL_COLORS.get(v, 'inherit')}; font-weight: 600", subset=["Level"]),
        hide_index=True,
        width="stretch",
        column_config={"Price risk": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f")},
    )
    st.caption(
        "Price risk = 100 x material volatility x cost share: the part is delivered but costs more. "
        f"Volatility classes in material_prices.csv ({NON_PLM})."
    )


def _material_dependency(s: Services) -> None:
    st.subheader("Material dependency")
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
            "Mass share": st.column_config.NumberColumn(format="percent"),
            "Material cost (EUR)": st.column_config.NumberColumn(format="%.2f"),
            "Cost share": st.column_config.NumberColumn(format="percent"),
        },
    )
    st.caption(
        "What stops or gets more expensive if a material is short or rises. Whole product, independent of the "
        f"filters, stator meshing artefacts excluded. Suppliers and volatility: {NON_PLM}."
    )
    show_sources([ref for m in exposure for ref in m.sources], "Material dependency sources")


def _structural_dependency(s: Services) -> None:
    st.subheader("Structural dependencies")
    shared, single = [], []
    for r in s.risk.ranking():
        occurrences = s.plm.where_used(r.plm_id)
        sourcing = s.enrichment.sourcing(r.plm_id)
        if len(occurrences) > 1:
            shared.append(
                {
                    "Part": r.name,
                    "PLM ID": r.plm_id,
                    "Used in": len(occurrences),
                    "Parents": ", ".join(s.plm.product(o.path[-2]).name for o in occurrences),
                    "Units in product": s.plm.total_quantity(r.plm_id).value,
                    "Suppliers": len(sourcing),
                    "Risk level": r.level,
                }
            )
        if len(sourcing) == 1:
            single.append(
                {
                    "Part": r.name,
                    "PLM ID": r.plm_id,
                    "Supplier": s.enrichment.supplier(sourcing[0].supplier_id).name,
                    "Make/buy": sourcing[0].make_or_buy,
                    "Lead time (days)": sourcing[0].lead_time_days,
                    "Risk level": r.level,
                }
            )
    left, right = st.columns(2)
    with left:
        st.markdown(f"**Parts used in several places ({len(shared)})**")
        st.dataframe(pd.DataFrame(shared), hide_index=True, width="stretch")
        st.caption("A problem on these parts spreads to every place they are used (REUSE criterion, PLM XML).")
    with right:
        st.markdown(f"**Single-sourced parts ({len(single)})**")
        st.dataframe(pd.DataFrame(single), hide_index=True, width="stretch")
        st.caption(
            f"No alternative source if the supplier fails ({NON_PLM}). The two stator meshing artefacts are not "
            "counted here, unlike the single-sourced KPI above."
        )


def _data_quality(s: Services) -> None:
    with st.expander("Data quality", expanded=False):
        products = s.plm.products()
        leaves = s.plm.leaves()
        report = s.enrichment.coverage(s.plm)
        rolled = sum(p.mass_kg * s.plm.total_quantity(p.plm_id).value for p in leaves)
        reps_consistent = sum(
            1
            for p in leaves
            if (rep := s.plm.representation(p.plm_id)) is not None
            and rep.revision == p.revision
            and rep.maturity == p.maturity
        )
        checks = [
            ("Structure", "Single root, no broken reference, no cycle, unique identifiers", "passed at load"),
            ("Mass roll-up", f"Leaves x quantities = {rolled:.2f} kg vs root {s.plm.root().mass_kg} kg", "consistent"),
            (
                "Load warnings",
                f"{len(s.plm.warnings)} non-blocking anomaly",
                "none" if not s.plm.warnings else "see sidebar",
            ),
            (
                "3D representations",
                f"{reps_consistent}/{len(leaves)} leaves with a representation of the same revision and maturity",
                "consistent" if reps_consistent == len(leaves) else "check",
            ),
            (
                "Descriptions",
                f"{sum(1 for p in products if not p.description)}/{len(products)} products without description",
                "gap in the extract",
            ),
            (
                "Lifecycle",
                f"{sum(1 for p in products if p.maturity == 'IN_WORK')}/{len(products)} products IN_WORK, "
                "one revision per product",
                "no history",
            ),
            (
                "Meshing artefacts",
                f"{len(MESHING_ARTEFACTS)} stator parts modelled for meshing "
                f"({sum(s.plm.product(p).mass_kg for p in MESHING_ARTEFACTS):.2f} kg counted in the stator mass)",
                "excluded from risk",
            ),
            (
                "Enrichment coverage",
                f"{len(report.materials_without_price)} material without price, "
                f"{len(report.leaves_without_sourcing)} leaf without supplier, "
                f"{len(report.unfilled_rows)} unfilled row",
                "complete" if report.complete else "incomplete",
            ),
        ]
        st.dataframe(pd.DataFrame(checks, columns=["Check", "Finding", "Status"]), hide_index=True, width="stretch")
        st.caption(
            "The extract holds no product quality data (defects, tests, deviations, tolerances): quality decisions "
            "rest on the quality of the data itself, checked here."
        )


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
    _subassemblies(s)
    _charts(parts)
    selection = set(parts["PLM ID"])
    _risk(s, selection)
    _heat_map(s, selection)
    _price_exposure(s, selection)
    _material_dependency(s)
    _structural_dependency(s)

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
    _data_quality(s)
