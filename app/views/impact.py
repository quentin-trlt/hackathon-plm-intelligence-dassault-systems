"""Change impact: replace a component by one of another mass or material, and trace what it affects."""

from __future__ import annotations

from collections import defaultdict

import pandas as pd
import streamlit as st

import ui
from plm.model import Product, SourceRef
from services import Services, label, leaves_below, part_risk, sourcing_frame

DEFAULT_TARGET = "ECAR-3D_DifferentialSideShaft"


def _units_below(s: Services, plm_id: str) -> dict[str, int]:
    """For every assembly above the product: units of the product inside one unit of that assembly."""
    by_path = {occ.path: occ for occ in s.plm.occurrences()}
    per_ancestor: dict[str, dict[tuple[str, ...], int]] = defaultdict(lambda: defaultdict(int))
    for occ in s.plm.where_used(plm_id):
        for depth, ancestor in enumerate(occ.path[:-1]):
            ancestor_path = occ.path[: depth + 1]
            ratio = occ.cumulative_quantity // by_path[ancestor_path].cumulative_quantity
            per_ancestor[ancestor][ancestor_path] += ratio
    return {ancestor: next(iter(paths.values())) for ancestor, paths in per_ancestor.items()}


def _proposal(s: Services) -> tuple[Product, float, str | None, float | None, list[SourceRef]]:
    """Component, proposed unit mass and material, proposed unit material cost, with the price rows used."""
    candidates = [p for p in s.plm.products() if not p.is_root]
    default = next((i for i, p in enumerate(candidates) if p.name == DEFAULT_TARGET), 0)
    product = st.selectbox("Component to replace", candidates, index=default, format_func=label)
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
        return product, new_mass, None, None, []

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
    price = s.enrichment.material_price(new_material)
    if price is None:
        return product, new_mass, new_material, None, []
    return product, new_mass, new_material, new_mass * price.price_eur_per_kg, [price.source]


def _figures(s: Services, product: Product, new_mass: float, new_unit_cost: float | None, impacted: int) -> None:
    root = s.plm.root()
    quantity = s.plm.total_quantity(product.plm_id).value
    mass_delta = (new_mass - product.mass_kg) * quantity
    current_cost = s.cost.unit_cost(product.plm_id)
    root_cost = s.cost.unit_cost(root.plm_id)

    items = [
        ui.Figure("Impacted assemblies", str(impacted), "Every assembly above the component."),
        ui.Figure("Units in motor", str(quantity), "Each unit carries the change."),
        ui.Figure(
            "Motor mass",
            f"{root.mass_kg + mass_delta:.3f} kg",
            f"Declared {root.mass_kg} kg.",
            delta=f"{mass_delta:+.3f} kg" if mass_delta else "No change",
            delta_worse=None if mass_delta == 0 else mass_delta > 0,
        ),
    ]
    if new_unit_cost is not None and current_cost is not None and root_cost is not None:
        cost_delta = (new_unit_cost - current_cost.value) * quantity
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
            ui.Figure("Motor material cost", "Not computed", "Needs a part with a priced material.", team_data=True)
        )
    ui.figures(items)


def _impacted_frame(s: Services, product: Product, new_mass: float) -> pd.DataFrame:
    unit_delta = new_mass - product.mass_kg
    units_below = _units_below(s, product.plm_id)
    rows = []
    for occ in s.plm.occurrences():
        units = units_below.get(occ.plm_id)
        if not units:
            continue
        assembly = s.plm.product(occ.plm_id)
        rows.append(
            {
                "Assembly": assembly.name,
                "Kind": assembly.item_kind,
                "Level": occ.depth,
                "Units of the component": units,
                "Declared mass (kg)": assembly.mass_kg,
                "Projected mass (kg)": assembly.mass_kg + unit_delta * units,
                "PLM ID": assembly.plm_id,
            }
        )
    return pd.DataFrame(rows).drop_duplicates(subset="PLM ID")


def render(s: Services) -> None:
    ui.page_header(
        "Change impact",
        "Propose a replacement for a component and see which assemblies, suppliers, mass and material cost it touches.",
    )
    form_col, result_col = st.columns([1, 2.4], gap="large")
    with form_col:
        with st.container(border=True):
            st.markdown("**Proposed change**")
            product, new_mass, new_material, new_unit_cost, price_refs = _proposal(s)
        changed = new_mass != product.mass_kg or (new_material is not None and new_material != product.material)
        if not changed:
            st.caption("Change the mass or the material to see the roll-up on the motor.")

    leaves = leaves_below(s, product.plm_id)
    occurrences = s.plm.where_used(product.plm_id)
    with result_col:
        impacted = _impacted_frame(s, product, new_mass)
        _figures(s, product, new_mass, new_unit_cost, len(impacted))
        ui.key()

        risks = [r for r in (part_risk(s, pid) for pid in leaves) if r is not None]
        if len(risks) == 1:
            st.markdown(f"Current risk of **{risks[0].name}**")
            ui.risk_badge(risks[0].level, risks[0].criticality)

        st.subheader("Where the component sits", anchor=False)
        ui.paths(([s.plm.product(pid).name for pid in occ.path], occ.cumulative_quantity) for occ in occurrences)

        st.subheader("Impacted assemblies", anchor=False)
        st.dataframe(
            impacted,
            hide_index=True,
            width="stretch",
            column_config={
                "Declared mass (kg)": st.column_config.NumberColumn(format="%.3f"),
                "Projected mass (kg)": st.column_config.NumberColumn(format="%.3f"),
            },
        )

        st.subheader("Suppliers to consult", anchor=False)
        sourcing = sourcing_frame(s, leaves)
        if sourcing.empty:
            st.info("No supplier is recorded for the parts concerned in data/enrichment/part_sourcing.csv.")
        else:
            st.dataframe(
                sourcing.drop(columns=["CSV line"]),
                hide_index=True,
                width="stretch",
                column_config={"Single source": st.column_config.CheckboxColumn()},
            )
            st.caption("Current suppliers of the parts being replaced, with their lead time to re-supply. Team data.")

        refs: list[SourceRef] = [product.source, *price_refs]
        refs += [ref for occ in occurrences for ref in occ.sources]
        refs += [row.source for pid in leaves for row in s.enrichment.sourcing(pid)]
        ui.show_sources(refs)
