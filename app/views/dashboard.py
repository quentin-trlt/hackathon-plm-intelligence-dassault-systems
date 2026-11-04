"""Overview: where the material cost, the mass and the risk of the motor sit, across its leaf parts."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import ui
from plm.analysis.risk import MESHING_ARTEFACTS
from plm.analysis.summary import subassembly_summary
from services import NON_PLM, Services, leaf_frame


def _level_style(value: object) -> str:
    return f"color: {ui.RISK_COLORS.get(value, 'inherit')}; font-weight: 600"


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


def _subassemblies(s: Services) -> None:
    st.subheader("By subassembly", anchor=False)
    rows = [
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
        for sub in subassembly_summary(s.plm, s.cost, s.risk)
    ]
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            "Mass (kg)": st.column_config.NumberColumn(format="%.2f"),
            "Mass share": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent"),
            "Material cost (EUR)": st.column_config.NumberColumn(format="%.2f"),
            "Cost share": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent"),
        },
    )
    st.caption("Whole motor, independent of the filters. Mass from the OEM extract, cost from team prices.")


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
        table.style.map(_level_style, subset=["Level"]),
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


def _heat_map(s: Services, plm_ids: set[str]) -> None:
    st.subheader("Risk heat map", anchor=False)
    ranking = [r for r in s.risk.ranking() if r.plm_id in plm_ids]
    if not ranking:
        st.info("None of the filtered parts is scored.")
        return
    fig = go.Figure()
    for level in ("low", "medium", "high"):
        group = [r for r in ranking if r.level == level]
        if not group:
            continue
        fig.add_scatter(
            x=[r.impact for r in group],
            y=[r.probability for r in group],
            mode="markers",
            name=level.capitalize(),
            marker=dict(size=12, color=ui.RISK_COLORS[level], line=dict(width=1, color="white")),
            customdata=[(r.name, r.criticality) for r in group],
            hovertemplate="%{customdata[0]}<br>Impact %{x:.2f}, probability %{y:.2f}"
            "<br>Criticality %{customdata[1]:.1f}<extra></extra>",
        )
    for value in (0.33, 0.66):
        fig.add_vline(x=value, line=dict(color=ui.MUTED, dash="dot", width=1))
        fig.add_hline(y=value, line=dict(color=ui.MUTED, dash="dot", width=1))
    ui.plotly_layout(fig, height=388)
    fig.update_xaxes(title="Impact", range=[0, 1.02], gridcolor=ui.RULE, zeroline=False)
    fig.update_yaxes(title="Probability", range=[0, 1.02], gridcolor=ui.RULE, zeroline=False)
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    st.caption(
        "Each dot is a scored part. Dotted lines mark the low, medium and high bands of each axis. Top right first."
    )


def _price_exposure(s: Services, plm_ids: set[str]) -> None:
    st.subheader("Price exposure", anchor=False)
    ranking = [r for r in s.risk.price_ranking() if r.plm_id in plm_ids]
    if not ranking:
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
            for r in ranking
        ]
    )
    st.dataframe(
        table.style.map(_level_style, subset=["Level", "Supply risk"]),
        hide_index=True,
        width="stretch",
        height=388,
        column_config={"Price risk": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f")},
    )
    st.caption(
        "Price risk = 100 x material volatility x cost share: the part is delivered but costs more. "
        "Volatility classes from data/enrichment/material_prices.csv."
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
    ui.plotly_layout(fig, height=max(320, 46 * len(by_material) + 60))
    fig.update_layout(barmode="group", bargap=0.28, bargroupgap=0.08)
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


def _structural_dependency(s: Services) -> None:
    st.subheader("Structural dependencies", anchor=False)
    shared, single = [], []
    for r in s.risk.ranking():
        occurrences = s.plm.where_used(r.plm_id)
        sourcing = s.enrichment.sourcing(r.plm_id)
        if len(occurrences) > 1:
            shared.append(
                {
                    "Part": r.name,
                    "Used in": len(occurrences),
                    "Parents": ", ".join(s.plm.product(o.path[-2]).name for o in occurrences),
                    "Units in motor": s.plm.total_quantity(r.plm_id).value,
                    "Suppliers": len(sourcing),
                    "Risk level": r.level,
                }
            )
        if len(sourcing) == 1:
            single.append(
                {
                    "Part": r.name,
                    "Supplier": s.enrichment.supplier(sourcing[0].supplier_id).name,
                    "Make/buy": sourcing[0].make_or_buy,
                    "Lead time (days)": sourcing[0].lead_time_days,
                    "Risk level": r.level,
                }
            )
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown(f"**Parts used in several places ({len(shared)})**")
        st.dataframe(
            pd.DataFrame(shared).style.map(_level_style, subset=["Risk level"]) if shared else pd.DataFrame(),
            hide_index=True,
            width="stretch",
        )
        st.caption("A problem on these parts spreads to every place they are used (REUSE criterion, OEM extract).")
    with right:
        st.markdown(f"**Single-sourced parts ({len(single)})**")
        st.dataframe(
            pd.DataFrame(single).style.map(_level_style, subset=["Risk level"]) if single else pd.DataFrame(),
            hide_index=True,
            width="stretch",
        )
        st.caption(
            "No alternative source if the supplier fails. Team data. The two stator meshing artefacts are not "
            "counted here, unlike the single-sourced figure above."
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
                "none" if not s.plm.warnings else "shown at the top of the page",
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

    selection = set(parts["PLM ID"])
    _figures(s, parts)
    _subassemblies(s)
    left, right = st.columns([1, 1], gap="large")
    with left:
        _critical_parts(s, selection)
    with right:
        _suppliers(s)
    left, right = st.columns([1, 1], gap="large")
    with left:
        _heat_map(s, selection)
    with right:
        _price_exposure(s, selection)
    _materials(parts)
    _material_dependency(s)
    _structural_dependency(s)

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
    _data_quality(s)
