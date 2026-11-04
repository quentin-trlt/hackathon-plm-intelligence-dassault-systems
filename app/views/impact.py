"""Change impact: change or replace a component and trace what it affects. Logic in plm.analysis.impact."""

from __future__ import annotations

import pandas as pd
import streamlit as st

import ui
from plm.analysis.impact import ChangeImpact
from plm.analysis.risk import PartRisk
from services import Services, label, leaves_below, sourcing_frame

DEFAULT_TARGET = "ECAR-3D_DifferentialSideShaft"
MODES = ("Change mass or material", "Replace by an existing part")


def _proposal(s: Services) -> ChangeImpact:
    candidates = [p for p in s.plm.products() if not p.is_root]
    default = next((i for i, p in enumerate(candidates) if p.name == DEFAULT_TARGET), 0)
    product = st.selectbox("Component to change", candidates, index=default, format_func=label)
    mode = st.radio("Proposed change", MODES, key=f"mode-{product.plm_id}")
    if mode == MODES[1]:
        replacements = s.impact.replacement_candidates(product.plm_id)
        if not replacements:
            st.info("No product of the extract can replace this one.")
            return s.impact.assess(product.plm_id)
        replacement = st.selectbox(
            "Replacement part",
            replacements,
            format_func=label,
            key=f"replacement-{product.plm_id}",
            help="Products of the same kind (part or assembly), excluding the component itself and its parents.",
        )
        return s.impact.assess(product.plm_id, replacement_plm_id=replacement.plm_id)

    new_mass = st.number_input(
        "Proposed unit mass (kg)",
        min_value=0.0,
        value=float(product.mass_kg),
        step=0.01,
        format="%.3f",
        key=f"mass-{product.plm_id}",
        help=f"Current unit mass: {product.mass_kg:.3f} kg.",
    )
    if product.material is None:
        st.text_input("Proposed material", value="None, this is an assembly", disabled=True)
        return s.impact.assess(product.plm_id, new_mass_kg=new_mass)

    materials = [p.material for p in s.enrichment.material_prices()]
    if product.material not in materials:
        materials.insert(0, product.material)
    new_material = st.selectbox(
        "Proposed material",
        materials,
        index=materials.index(product.material),
        key=f"material-{product.plm_id}",
        help=f"Current material: {product.material}.",
    )
    return s.impact.assess(product.plm_id, new_mass_kg=new_mass, new_material=new_material)


def _changed(result: ChangeImpact) -> bool:
    return (
        result.replacement is not None
        or result.proposed_unit_mass_kg != result.current_unit_mass_kg
        or result.proposed_material != result.target.material
    )


def _figures(s: Services, result: ChangeImpact) -> None:
    root = s.plm.root()
    root_cost = s.cost.unit_cost(root.plm_id)
    mass_delta = result.root_mass_delta_kg
    items = [
        ui.Figure("Impacted assemblies", str(len(result.impacted)), "Every assembly above the component."),
        ui.Figure("Units in motor", str(result.quantity_in_root), "Each unit carries the change."),
        ui.Figure(
            "Motor mass",
            f"{root.mass_kg + mass_delta:.3f} kg",
            f"Declared {root.mass_kg} kg.",
            delta=f"{mass_delta:+.3f} kg" if mass_delta else "No change",
            delta_worse=None if mass_delta == 0 else mass_delta > 0,
        ),
    ]
    cost_delta = result.root_cost_delta_eur
    if cost_delta is not None and root_cost is not None:
        items.append(
            ui.Figure(
                "Motor material cost",
                f"{root_cost.value + cost_delta:.2f} EUR",
                f"Currently {root_cost.value:.2f} EUR. Raw material only.",
                team_data=True,
                delta=f"{cost_delta:+.2f} EUR" if cost_delta else "No change",
                delta_worse=None if cost_delta == 0 else cost_delta > 0,
            )
        )
    else:
        items.append(
            ui.Figure("Motor material cost", "Not computed", "Needs every price involved to be known.", team_data=True)
        )
    ui.figures(items)


def _impacted(result: ChangeImpact) -> None:
    st.subheader("Impacted assemblies", anchor=False)
    frame = pd.DataFrame(
        [
            {
                "Assembly": a.name,
                "Kind": a.item_kind,
                "Level": a.depth,
                "Units of the component": a.units,
                "Declared mass (kg)": a.declared_mass_kg,
                "Projected mass (kg)": a.projected_mass_kg,
                "Material cost (EUR)": a.current_cost_eur,
                "Projected cost (EUR)": a.projected_cost_eur,
                "PLM ID": a.plm_id,
            }
            for a in result.impacted
        ]
    )
    money = st.column_config.NumberColumn(format="%.2f")
    mass = st.column_config.NumberColumn(format="%.3f")
    st.dataframe(
        frame,
        hide_index=True,
        width="stretch",
        column_config={
            "Declared mass (kg)": mass,
            "Projected mass (kg)": mass,
            "Material cost (EUR)": money,
            "Projected cost (EUR)": money,
        },
    )
    st.caption("Projected cost per assembly: current material cost plus the unit cost change x units. Team prices.")


def _side(title: str, material: str | None, volatility: str | None) -> None:
    st.markdown(f"**{title}**")
    with st.container(horizontal=True, gap="small"):
        st.badge(material or "No material", color="gray")
        st.badge(f"Volatility {volatility or 'unknown'}", color="violet")


def _risk(risk: PartRisk | None) -> None:
    if risk is not None:
        ui.risk_badge(risk.level, risk.criticality)


def _suppliers(s: Services, plm_id: str) -> None:
    frame = sourcing_frame(s, leaves_below(s, plm_id))
    if frame.empty:
        st.info("No supplier is recorded in data/enrichment/part_sourcing.csv.")
        return
    st.dataframe(
        frame.drop(columns=["Source note", "CSV line"]),
        hide_index=True,
        width="stretch",
        column_config={"Single source": st.column_config.CheckboxColumn()},
    )


def _before_after(s: Services, result: ChangeImpact) -> None:
    st.subheader("Before and after", anchor=False)
    before, after = st.columns(2, gap="large")
    with before:
        _side(f"Current: {result.target.name}", result.target.material, result.current_volatility)
        _risk(result.current_risk)
        _suppliers(s, result.target.plm_id)
    with after:
        proposed = result.replacement or result.target
        title = label(result.replacement) if result.replacement else f"{result.target.name}, modified"
        _side(f"Proposed: {title}", result.proposed_material, result.proposed_volatility)
        _risk(result.proposed_risk)
        if result.replacement is None:
            st.caption("Same suppliers as today: a mass or material change does not model a supplier change.")
        _suppliers(s, proposed.plm_id)
    if result.proposed_volatility and result.current_volatility != result.proposed_volatility:
        st.warning(
            f"Price exposure changes: {result.current_volatility or 'unknown'} volatility today, "
            f"{result.proposed_volatility} with the proposed material."
        )
    st.caption("Suppliers, lead times and volatility classes are team data from data/enrichment/.")


def render(s: Services) -> None:
    ui.page_header(
        "Change impact",
        "Change a component or replace it by another part of the extract, and see which assemblies, suppliers, "
        "mass and material cost it touches.",
    )
    form_col, result_col = st.columns([1, 2.4], gap="large")
    with form_col:
        with st.container(border=True):
            st.markdown("**Proposed change**")
            result = _proposal(s)
        if not _changed(result):
            st.caption("Change the mass, the material or pick a replacement to see the roll-up on the motor.")

    with result_col:
        _figures(s, result)
        ui.key()
        st.caption(
            "Costs are material costs only (mass x price per kg). The data holds no purchase price, tooling, "
            "requalification or validation cost: the real cost of a change is higher."
        )

        st.subheader("Where the component sits", anchor=False)
        ui.paths(
            ([s.plm.product(pid).name for pid in occ.path], occ.cumulative_quantity)
            for occ in s.plm.where_used(result.target.plm_id)
        )
        _impacted(result)
        _before_after(s, result)
        ui.show_sources(result.sources)
