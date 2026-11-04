"""Product structure: expanded BOM tree, search, and a component card with its PLM and team data."""

from __future__ import annotations

import pandas as pd
import streamlit as st

import ui
from plm.model import Occurrence, Product, SourceRef
from services import Services, criteria_frame, leaves_below, part_risk, sourcing_frame

INDENT = "    "
KIND_BADGES = {"assembly": "blue", "part": "gray", "wrapper": "violet"}


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
            "Qty": occ.cumulative_quantity,
            "Unit mass (kg)": product.mass_kg,
            "Material": product.material,
            "PLM ID": product.plm_id,
        }
        if matches is not None:
            row["Path"] = " / ".join(s.plm.product(pid).name for pid in ancestors)
        rows.append(row)
    return pd.DataFrame(rows)


def _attributes(s: Services, product: Product) -> pd.DataFrame:
    representation = s.plm.representation(product.plm_id)
    attributes = {
        "PLM ID": product.plm_id,
        "XID": product.xid,
        "Kind": product.item_kind,
        "Material": product.material or "None (assembly)",
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
        st.markdown(f"**Children** ({len(lines)})")
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
        st.dataframe(
            pd.DataFrame(children),
            hide_index=True,
            width="stretch",
            column_config={"Unit mass (kg)": st.column_config.NumberColumn(format="%.3f")},
        )

    occurrences = s.plm.where_used(product.plm_id)
    if occurrences and not product.is_root:
        st.markdown("**Where used**")
        ui.paths(([s.plm.product(pid).name for pid in occ.path[:-1]], occ.cumulative_quantity) for occ in occurrences)
        refs.extend(ref for occ in occurrences for ref in occ.sources)
    if not lines and product.is_root:
        st.info("The root assembly has no recorded children.")
    return refs


def _sourcing(s: Services, product: Product) -> list[SourceRef]:
    refs: list[SourceRef] = []
    cost = s.cost.unit_cost(product.plm_id)
    price = s.enrichment.material_price(product.material) if product.material else None
    items = [
        ui.Figure(
            "Material cost per unit",
            f"{cost.value:.2f} EUR" if cost else "Unknown",
            "Mass x material price.",
            team_data=True,
        )
    ]
    if price:
        volatility = f"Volatility {price.price_volatility}." if price.price_volatility else ""
        items.append(ui.Figure("Material price", f"{price.price_eur_per_kg:.2f} EUR/kg", volatility, team_data=True))
        refs.append(price.source)
    ui.figures(items)

    leaves = leaves_below(s, product.plm_id)
    if len(leaves) != 1:
        st.caption(f"Assembly of {len(leaves)} leaf parts. Open a leaf part to see its suppliers.")
        return refs
    sourcing = sourcing_frame(s, leaves)
    if sourcing.empty:
        st.warning("No supplier is recorded for this part in data/enrichment/part_sourcing.csv.")
        return refs
    st.dataframe(
        sourcing.drop(columns=["Part", "CSV line"]),
        hide_index=True,
        width="stretch",
        column_config={"Single source": st.column_config.CheckboxColumn()},
    )
    refs.extend(row.source for row in s.enrichment.sourcing(leaves[0]))
    return refs


def _risk(s: Services, product: Product) -> list[SourceRef]:
    leaves = leaves_below(s, product.plm_id)
    if len(leaves) == 1:
        risk = part_risk(s, leaves[0])
        if risk is None:
            st.info("Not scored: this is a stator meshing artefact, not a physical part (docs/risk_rules.md).")
            return []
        ui.figures(
            [
                ui.Figure("Criticality", f"{risk.criticality:.1f}", "Out of 100.", alert=risk.level == "high"),
                ui.Figure("Probability", f"{risk.probability:.2f}", "Chance of a supply disruption, 0 to 1."),
                ui.Figure("Impact", f"{risk.impact:.2f}", "Consequence of that disruption, 0 to 1."),
                ui.Figure(
                    "Price risk",
                    f"{risk.price_risk:.1f}" if risk.price_risk is not None else "Unknown",
                    "Exposure to material price moves, out of 100.",
                    team_data=True,
                ),
            ]
        )
        st.dataframe(
            criteria_frame(risk.criteria),
            hide_index=True,
            width="stretch",
            column_config={
                "Score": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f"),
                "Explanation": st.column_config.TextColumn(width="large"),
            },
        )
        return list(risk.sources)

    assembly = s.risk.assembly_risk(product.plm_id)
    counts = assembly.count_by_level
    ui.figures(
        [
            ui.Figure("High-risk parts below", str(counts["high"]), alert=counts["high"] > 0),
            ui.Figure("Medium", str(counts["medium"])),
            ui.Figure("Low", str(counts["low"])),
        ]
    )
    if assembly.worst:
        st.caption(f"Most critical part below: {assembly.worst.name}, {assembly.worst.criticality:.1f} / 100.")
    return []


def _card(s: Services, plm_id: str) -> None:
    product = s.plm.product(plm_id)
    st.subheader(product.name, anchor=False)
    with st.container(horizontal=True, gap="small"):
        st.badge(product.item_kind.capitalize(), color=KIND_BADGES.get(product.item_kind, "gray"))
        st.badge(f"Rev {product.revision}", color="gray")
        st.badge(product.maturity, color="gray")
        if product.material:
            st.badge(product.material, color="gray")
        leaves = leaves_below(s, plm_id)
        risk = part_risk(s, leaves[0]) if len(leaves) == 1 else None
        if risk is not None:
            ui.risk_badge(risk.level, risk.criticality)
    st.caption(f"{product.plm_id}, XML line {product.source.line}")

    total_quantity = s.plm.total_quantity(plm_id).value
    ui.figures(
        [
            ui.Figure("Unit mass", f"{product.mass_kg:.3f} kg"),
            ui.Figure("Quantity in motor", str(total_quantity)),
            ui.Figure("Mass in motor", f"{product.mass_kg * total_quantity:.3f} kg"),
        ]
    )

    t_attr, t_struct, t_source, t_risk = st.tabs(["PLM attributes", "Structure", "Cost and sourcing", "Risk"])
    refs: list[SourceRef] = [product.source]
    with t_attr:
        st.dataframe(_attributes(s, product), hide_index=True, width="stretch")
    with t_struct:
        refs += _structure(s, product)
    with t_source:
        refs += _sourcing(s, product)
    with t_risk:
        refs += _risk(s, product)
    ui.show_sources(refs)


def render(s: Services) -> None:
    ui.page_header(
        "Product structure",
        "The expanded product structure from the OEM extract. Select a row to open its card.",
    )
    tree_col, card_col = st.columns([2, 3], gap="large")
    with tree_col:
        query = st.text_input(
            "Search the structure",
            placeholder="Name, PLM ID or material, e.g. bearing, 20MnCr5, 00000081",
        ).strip()
        merge = st.toggle(
            "Hide wrappers",
            value=True,
            help="Each ECAR-EI wrapper holds exactly one ECAR-3D part. Hiding them shows the part in its place.",
        )
        matches = {p.plm_id for p in s.plm.search(query)} if query else None
        tree = _tree_frame(s, merge, matches)
        if tree.empty:
            st.warning(f"No product matches '{query}'. Try part of a name, a material or the end of a PLM ID.")
            return
        if matches is not None:
            st.caption(f"{len(tree)} occurrence(s) match '{query}'.")
        columns = ["Structure", "Qty", "Unit mass (kg)"] + (["Path"] if matches is not None else [])
        event = st.dataframe(
            tree,
            hide_index=True,
            width="stretch",
            height=640 if matches is None else "auto",
            on_select="rerun",
            selection_mode="single-row",
            key=f"tree-{merge}-{query}",
            column_order=columns,
            column_config={
                "Unit mass (kg)": st.column_config.NumberColumn(format="%.3f"),
            },
        )
    rows = event.selection.rows
    selected = tree.iloc[rows[0]]["PLM ID"] if rows else tree.iloc[0]["PLM ID"]
    with card_col:
        _card(s, selected)
