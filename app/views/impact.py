"""Change Impact: change or replace a component and trace what it affects. Logic in plm.analysis.impact."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from plm.analysis.impact import ChangeImpact
from plm.model import PartSourcing, Product
from services import NON_PLM, Services, label, show_sources

DEFAULT_TARGET = "ECAR-3D_DifferentialSideShaft"
MODES = ("Change mass or material", "Replace by an existing part")


def _proposal(s: Services, product: Product) -> ChangeImpact:
    mode = st.radio("Proposed change", MODES, horizontal=True)
    if mode == MODES[1]:
        candidates = s.impact.replacement_candidates(product.plm_id)
        if not candidates:
            st.info("No product of the extract can replace this one.")
            return s.impact.assess(product.plm_id)
        replacement = st.selectbox("Replacement part", candidates, format_func=label)
        return s.impact.assess(product.plm_id, replacement_plm_id=replacement.plm_id)

    c1, c2 = st.columns(2)
    new_mass = c1.number_input(
        "Proposed unit mass (kg)", min_value=0.0, value=float(product.mass_kg), step=0.01, format="%.3f"
    )
    if product.material is None:
        c2.text_input("Material", value="none (assembly)", disabled=True)
        return s.impact.assess(product.plm_id, new_mass_kg=new_mass)
    materials = [p.material for p in s.enrichment.material_prices()]
    if product.material not in materials:
        materials.insert(0, product.material)
    new_material = c2.selectbox("Proposed material", materials, index=materials.index(product.material))
    return s.impact.assess(product.plm_id, new_mass_kg=new_mass, new_material=new_material)


def _metrics(s: Services, result: ChangeImpact) -> None:
    root = s.plm.root()
    root_cost = s.cost.unit_cost(root.plm_id)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Impacted assemblies", len(result.impacted))
    m2.metric("Units in motor", result.quantity_in_root)
    m3.metric(
        "Motor mass",
        f"{root.mass_kg + result.root_mass_delta_kg:.3f} kg",
        delta=f"{result.root_mass_delta_kg:+.3f} kg",
        delta_color="inverse",
        help="Declared root mass + unit mass change x units in the motor.",
    )
    if result.root_cost_delta_eur is not None and root_cost is not None:
        m4.metric(
            "Motor material cost",
            f"{root_cost.value + result.root_cost_delta_eur:.2f} EUR",
            delta=f"{result.root_cost_delta_eur:+.2f} EUR",
            delta_color="inverse",
            help=f"Mass x material price ({NON_PLM}).",
        )
    else:
        m4.metric("Motor material cost", "n/a", help="Only computed when every price involved is known.")


def _impacted(result: ChangeImpact) -> None:
    st.subheader("Impacted assemblies")
    frame = pd.DataFrame(
        [
            {
                "Assembly": a.name,
                "Kind": a.item_kind,
                "Level": a.depth,
                f"Units of {result.target.name}": a.units,
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


def _suppliers_frame(s: Services, rows: tuple[PartSourcing, ...]) -> pd.DataFrame:
    by_part: dict[str, int] = {}
    for row in rows:
        by_part[row.plm_id] = by_part.get(row.plm_id, 0) + 1
    return pd.DataFrame(
        [
            {
                "Part": s.plm.product(row.plm_id).name,
                "Supplier": s.enrichment.supplier(row.supplier_id).name,
                "Country": s.enrichment.supplier(row.supplier_id).country,
                "Make/buy": row.make_or_buy,
                "Lead time (days)": row.lead_time_days,
                "Single source": by_part[row.plm_id] == 1,
                "CSV line": row.source.line,
            }
            for row in rows
        ]
    )


def _before_after(s: Services, result: ChangeImpact) -> None:
    st.subheader(f"Before and after · `{NON_PLM}`")
    before, after = st.columns(2)
    with before:
        st.markdown(f"**Current: {result.target.name}**")
        st.caption(
            f"Material {result.target.material or 'n/a'} · volatility {result.current_volatility or 'unknown'}"
            + (
                f" · risk {result.current_risk.level} ({result.current_risk.criticality:.1f})"
                if result.current_risk
                else ""
            )
        )
        frame = _suppliers_frame(s, result.current_suppliers)
        if frame.empty:
            st.info("No supplier recorded.")
        else:
            st.dataframe(frame, hide_index=True, width="stretch")
    with after:
        name = result.replacement.name if result.replacement else f"{result.target.name} (modified)"
        st.markdown(f"**Proposed: {name}**")
        st.caption(
            f"Material {result.proposed_material or 'n/a'} · volatility {result.proposed_volatility or 'unknown'}"
            + (
                f" · risk {result.proposed_risk.level} ({result.proposed_risk.criticality:.1f})"
                if result.proposed_risk
                else ""
            )
        )
        if result.replacement is None:
            st.caption("Same suppliers as today: a supplier change is not modelled for a mass or material change.")
        frame = _suppliers_frame(s, result.proposed_suppliers)
        if frame.empty:
            st.info("No supplier recorded.")
        else:
            st.dataframe(frame, hide_index=True, width="stretch")
    if result.current_volatility != result.proposed_volatility and result.proposed_volatility:
        st.warning(
            f"Price exposure changes: {result.current_volatility or 'unknown'} volatility today, "
            f"{result.proposed_volatility} with the proposed material."
        )


def render(s: Services) -> None:
    st.header("Change Impact")
    st.caption("Pick a component and a change: impacted assemblies, mass and cost roll-up, suppliers concerned.")

    candidates = [p for p in s.plm.products() if not p.is_root]
    default = next((i for i, p in enumerate(candidates) if p.name == DEFAULT_TARGET), 0)
    product = st.selectbox("Component to change", candidates, index=default, format_func=label)
    result = _proposal(s, product)

    _metrics(s, result)
    _impacted(result)
    st.markdown("**Where-used paths**")
    for occ in s.plm.where_used(product.plm_id):
        st.markdown(f"- {' > '.join(s.plm.product(pid).name for pid in occ.path)} · {occ.cumulative_quantity} unit(s)")
    _before_after(s, result)
    st.info(
        "Costs are material costs only (mass x price per kg of the material). The data holds no purchase price, "
        "tooling, requalification or validation cost: the real cost of a change is higher."
    )
    show_sources(result.sources)
