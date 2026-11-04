"""Product Explorer: expanded BOM tree, search, and a component card with its PLM and non-PLM data."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from plm.model import Occurrence, Product, SourceRef
from services import (
    NON_PLM,
    Services,
    criteria_frame,
    leaves_below,
    part_risk,
    show_sources,
    sourcing_frame,
)

INDENT = " "


def _is_wrapper(s: Services, plm_id: str) -> bool:
    return s.plm.product(plm_id).item_kind == "wrapper"


def _visible(s: Services, occ: Occurrence, merge: bool, matches: set[str] | None) -> bool:
    if merge and _is_wrapper(s, occ.plm_id):
        return False
    if matches is None:
        return True
    merged_wrapper = merge and occ.depth > 0 and _is_wrapper(s, occ.path[-2])
    return occ.plm_id in matches or (merged_wrapper and occ.path[-2] in matches)


def _tree_frame(s: Services, merge: bool, matches: set[str] | None) -> pd.DataFrame:
    rows = []
    for occ in s.plm.occurrences():
        if not _visible(s, occ, merge, matches):
            continue
        product = s.plm.product(occ.plm_id)
        ancestors = [pid for pid in occ.path[:-1] if not (merge and _is_wrapper(s, pid))]
        row = {
            "Structure": product.name if matches is not None else INDENT * len(ancestors) + product.name,
            "Kind": product.item_kind,
            "Qty in motor": occ.cumulative_quantity,
            "Unit mass (kg)": product.mass_kg,
            "Material": product.material,
            "Rev": product.revision,
            "Maturity": product.maturity,
            "PLM ID": product.plm_id,
            "XML line": product.source.line,
        }
        if matches is not None:
            row["Path"] = " > ".join(s.plm.product(pid).name for pid in ancestors)
        rows.append(row)
    return pd.DataFrame(rows)


def _attributes(s: Services, product: Product) -> pd.DataFrame:
    representation = s.plm.representation(product.plm_id)
    attributes = {
        "PLM ID": product.plm_id,
        "XID": product.xid,
        "Kind": product.item_kind,
        "Material": product.material or "none (assembly)",
        "Description": product.description or "",
        "Revision": f"{product.revision} (index {product.revision_index})",
        "Maturity": product.maturity,
        "Lifecycle": product.lifecycle,
        "Owner": product.owner,
        "Organization": product.organization,
        "Project": product.project,
        "Created": f"{product.created_at:%Y-%m-%d %H:%M} UTC",
        "Modified": f"{product.modified_at:%Y-%m-%d %H:%M} UTC",
        "3D representation": representation.name if representation else "",
    }
    return pd.DataFrame({"Attribute": list(attributes), "Value": list(attributes.values())})


def _structure(s: Services, product: Product) -> list[SourceRef]:
    refs: list[SourceRef] = []
    lines = s.plm.children(product.plm_id)
    if lines:
        st.markdown("**Children**")
        children = []
        for line in lines:
            child = s.plm.product(line.child_plm_id)
            children.append(
                {
                    "Child": child.name,
                    "Kind": child.item_kind,
                    "Qty": line.quantity,
                    "Unit mass (kg)": child.mass_kg,
                    "PLM ID": child.plm_id,
                }
            )
            refs.extend(line.sources)
        st.dataframe(pd.DataFrame(children), hide_index=True, width="stretch")

    occurrences = s.plm.where_used(product.plm_id)
    if occurrences and not product.is_root:
        st.markdown("**Where used**")
        for occ in occurrences:
            path = " > ".join(s.plm.product(pid).name for pid in occ.path[:-1])
            st.markdown(f"- {path} · {occ.cumulative_quantity} unit(s)")
            refs.extend(occ.sources)
    return refs


def _enrichment(s: Services, product: Product) -> list[SourceRef]:
    refs: list[SourceRef] = []
    st.markdown(f"**Cost and sourcing** · `{NON_PLM}`")
    cost = s.cost.unit_cost(product.plm_id)
    price = s.enrichment.material_price(product.material) if product.material else None
    c1, c2 = st.columns(2)
    c1.metric("Material cost per unit", f"{cost.value:.2f} EUR" if cost else "unknown", help="Mass x material price.")
    if price:
        c2.metric("Material price", f"{price.price_eur_per_kg:.2f} EUR/kg", help=price.source_note)
        refs.append(price.source)
        if price.price_volatility:
            st.caption(f"Price volatility of {product.material}: {price.price_volatility}.")

    leaves = leaves_below(s, product.plm_id)
    if len(leaves) == 1:
        sourcing = sourcing_frame(s, leaves)
        if sourcing.empty:
            st.warning("No supplier recorded for this part.")
        else:
            st.dataframe(sourcing.drop(columns="Part"), hide_index=True, width="stretch")
            refs.extend(row.source for row in s.enrichment.sourcing(leaves[0]))
    return refs


def _risk(s: Services, product: Product) -> list[SourceRef]:
    leaves = leaves_below(s, product.plm_id)
    if len(leaves) == 1:
        risk = part_risk(s, leaves[0])
        if risk is None:
            st.info("Not scored: stator meshing artefact, not a physical part (docs/risk_rules.md).")
            return []
        st.markdown(f"**Risk** · level **{risk.level}**, criticality {risk.criticality:.1f} / 100")
        c1, c2, c3 = st.columns(3)
        c1.metric("Probability", f"{risk.probability:.2f}")
        c2.metric("Impact", f"{risk.impact:.2f}")
        c3.metric("Price risk", f"{risk.price_risk:.1f}" if risk.price_risk is not None else "unknown")
        st.dataframe(
            criteria_frame(risk.criteria),
            hide_index=True,
            width="stretch",
            column_config={"Score": st.column_config.NumberColumn(format="%.2f")},
        )
        return list(risk.sources)

    assembly = s.risk.assembly_risk(product.plm_id)
    counts = assembly.count_by_level
    st.markdown(f"**Risk of the parts below** · {counts['high']} high, {counts['medium']} medium, {counts['low']} low")
    if assembly.worst:
        st.caption(f"Most critical: {assembly.worst.name} ({assembly.worst.criticality:.1f} / 100).")
    return []


def _card(s: Services, plm_id: str) -> None:
    product = s.plm.product(plm_id)
    st.subheader(product.name)
    st.caption(f"{product.plm_id} · {product.item_kind} · XML line {product.source.line}")
    total_quantity = s.plm.total_quantity(plm_id).value
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Unit mass", f"{product.mass_kg:.3f} kg")
    m2.metric("Qty in motor", total_quantity)
    m3.metric("Revision", product.revision)
    m4.metric("Maturity", product.maturity)

    left, right = st.columns(2)
    with left:
        st.markdown("**PLM attributes**")
        st.dataframe(_attributes(s, product), hide_index=True, width="stretch")
        refs = [product.source, *_structure(s, product)]
    with right:
        refs += _enrichment(s, product)
        refs += _risk(s, product)
    show_sources(refs)


def render(s: Services) -> None:
    st.header("Product Explorer")
    st.caption("Expanded product structure from the OEM extract. Select a row to open the component card.")

    c1, c2 = st.columns([3, 1])
    query = c1.text_input("Search", placeholder="Name, PLM ID or material, e.g. bearing, 20MnCr5, 00000081").strip()
    merge = c2.toggle(
        "Merge wrappers", value=True, help="Show each ECAR-EI wrapper merged with the ECAR-3D part it holds."
    )
    matches = {p.plm_id for p in s.plm.search(query)} if query else None

    tree = _tree_frame(s, merge, matches)
    if tree.empty:
        st.warning(f"No product matches '{query}'.")
        return
    if matches is not None:
        st.caption(f"{len(tree)} occurrence(s) match '{query}'.")
    event = st.dataframe(
        tree,
        hide_index=True,
        width="stretch",
        height=420 if matches is None else "auto",
        on_select="rerun",
        selection_mode="single-row",
        key=f"tree-{merge}-{query}",
        column_config={"Unit mass (kg)": st.column_config.NumberColumn(format="%.3f")},
    )
    rows = event.selection.rows
    selected = tree.iloc[rows[0]]["PLM ID"] if rows else tree.iloc[0]["PLM ID"]
    st.divider()
    _card(s, selected)
