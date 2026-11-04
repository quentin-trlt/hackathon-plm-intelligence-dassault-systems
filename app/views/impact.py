"""Change Impact: replace a component by one of another mass or material, and trace what it affects."""

from __future__ import annotations

from collections import defaultdict

import pandas as pd
import streamlit as st

from plm.model import Product, SourceRef
from services import NON_PLM, Services, label, leaves_below, part_risk, show_sources, sourcing_frame

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


def _proposal(s: Services, product: Product) -> tuple[float, float | None, list[SourceRef]]:
    """Proposed unit mass and unit material cost, with the price rows used."""
    c1, c2 = st.columns(2)
    new_mass = c1.number_input(
        "Proposed unit mass (kg)", min_value=0.0, value=float(product.mass_kg), step=0.01, format="%.3f"
    )
    if product.material is None:
        c2.text_input("Material", value="none (assembly)", disabled=True)
        return new_mass, None, []

    materials = [p.material for p in s.enrichment.material_prices()]
    if product.material not in materials:
        materials.insert(0, product.material)
    new_material = c2.selectbox("Proposed material", materials, index=materials.index(product.material))
    price = s.enrichment.material_price(new_material)
    if price is None:
        return new_mass, None, []
    return new_mass, new_mass * price.price_eur_per_kg, [price.source]


def _metrics(s: Services, product: Product, new_mass: float, new_unit_cost: float | None, impacted: int) -> None:
    root = s.plm.root()
    quantity = s.plm.total_quantity(product.plm_id).value
    mass_delta = (new_mass - product.mass_kg) * quantity
    current_cost = s.cost.unit_cost(product.plm_id)
    root_cost = s.cost.unit_cost(root.plm_id)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Impacted assemblies", impacted)
    m2.metric("Units in motor", quantity)
    m3.metric(
        "Motor mass",
        f"{root.mass_kg + mass_delta:.3f} kg",
        delta=f"{mass_delta:+.3f} kg",
        delta_color="inverse",
        help=f"Declared root mass {root.mass_kg} kg + unit mass change x {quantity} unit(s).",
    )
    if new_unit_cost is not None and current_cost is not None and root_cost is not None:
        cost_delta = (new_unit_cost - current_cost.value) * quantity
        m4.metric(
            "Motor material cost",
            f"{root_cost.value + cost_delta:.2f} EUR",
            delta=f"{cost_delta:+.2f} EUR",
            delta_color="inverse",
            help=f"Mass x material price ({NON_PLM}). Material only, not the purchase price.",
        )
    else:
        m4.metric("Motor material cost", "n/a", help="Only computed for a part with a priced material.")


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
                f"Units of {product.name}": units,
                "Declared mass (kg)": assembly.mass_kg,
                "Projected mass (kg)": assembly.mass_kg + unit_delta * units,
                "PLM ID": assembly.plm_id,
            }
        )
    return pd.DataFrame(rows).drop_duplicates(subset="PLM ID")


def render(s: Services) -> None:
    st.header("Change Impact")
    st.caption("Pick a component and a replacement: impacted assemblies, mass and cost roll-up, suppliers concerned.")

    candidates = [p for p in s.plm.products() if not p.is_root]
    default = next((i for i, p in enumerate(candidates) if p.name == DEFAULT_TARGET), 0)
    product = st.selectbox("Component to replace", candidates, index=default, format_func=label)
    new_mass, new_unit_cost, price_refs = _proposal(s, product)

    impacted = _impacted_frame(s, product, new_mass)
    _metrics(s, product, new_mass, new_unit_cost, len(impacted))

    st.subheader("Impacted assemblies")
    st.dataframe(
        impacted,
        hide_index=True,
        width="stretch",
        column_config={
            "Declared mass (kg)": st.column_config.NumberColumn(format="%.3f"),
            "Projected mass (kg)": st.column_config.NumberColumn(format="%.3f"),
        },
    )
    occurrences = s.plm.where_used(product.plm_id)
    st.markdown("**Where-used paths**")
    for occ in occurrences:
        st.markdown(f"- {' > '.join(s.plm.product(pid).name for pid in occ.path)} · {occ.cumulative_quantity} unit(s)")

    st.subheader(f"Suppliers concerned · `{NON_PLM}`")
    leaves = leaves_below(s, product.plm_id)
    sourcing = sourcing_frame(s, leaves)
    if sourcing.empty:
        st.info("No supplier recorded for the parts concerned.")
    else:
        st.dataframe(sourcing, hide_index=True, width="stretch")
        st.caption("Current suppliers of the parts being replaced, with their lead time to re-supply.")

    risks = [r for r in (part_risk(s, pid) for pid in leaves) if r is not None]
    if len(risks) == 1:
        risk = risks[0]
        st.caption(f"Current risk of {risk.name}: {risk.level}, criticality {risk.criticality:.1f} / 100.")

    refs: list[SourceRef] = [product.source, *price_refs]
    refs += [ref for occ in occurrences for ref in occ.sources]
    refs += [row.source for pid in leaves for row in s.enrichment.sourcing(pid)]
    show_sources(refs)
