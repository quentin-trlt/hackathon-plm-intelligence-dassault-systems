"""Product structure: expanded BOM tree and component card, versions and lifecycle, side-by-side comparison."""

from __future__ import annotations

from functools import partial
from html import escape
from typing import Any

import pandas as pd
import streamlit as st

import ui
from plm.model import Occurrence, Product, SourceRef
from services import Services, criteria_frame, label, leaves_below, part_risk, sourcing_frame
from tree import structure_tree

Node = tuple[str, ...]

TREE_KEY = "structure-tree"
TREE_EXPANDED = "tree-expanded"
TREE_SELECTED = "tree-selected"
KIND_BADGES = {"assembly": "blue", "part": "gray", "wrapper": "violet"}
COMPARE_DEFAULT = "ECAR-EI_GEAR_WHEEL"


def _is_wrapper(s: Services, plm_id: str) -> bool:
    return s.plm.product(plm_id).item_kind == "wrapper"


def _visible(s: Services, occ: Occurrence, merge: bool, matches: set[str] | None) -> bool:
    if merge and _is_wrapper(s, occ.plm_id):
        return False
    if matches is None:
        return True
    merged_wrapper = merge and occ.depth > 0 and _is_wrapper(s, occ.path[-2])
    return occ.plm_id in matches or (merged_wrapper and occ.path[-2] in matches)


def _node(s: Services, occ: Occurrence, merge: bool) -> Node:
    return tuple(pid for pid in occ.path if not (merge and _is_wrapper(s, pid)))


def _plm_id(row_id: str) -> str:
    return row_id.split("#", 1)[0].rsplit("/", 1)[-1]


def _ancestor_ids(row_id: str) -> set[str]:
    steps = row_id.split("#", 1)[0].split("/")
    return {"/".join(steps[:i]) for i in range(1, len(steps))}


def _tree_rows(s: Services, merge: bool, matches: set[str] | None) -> list[dict[str, Any]]:
    """Tree rows in BOM order. With a search or filter, only the matches and the assemblies above them.

    A row id is the chain of plm_ids from the root, with `#n` appended when the same chain occurs again
    (a part used twice in the same assembly)."""
    entries = [(occ, _node(s, occ, merge)) for occ in s.plm.occurrences() if _visible(s, occ, merge, None)]
    matched = None if matches is None else {node for occ, node in entries if _visible(s, occ, merge, matches)}
    if matched is not None:
        kept = {node[:i] for node in matched for i in range(1, len(node) + 1)}
        entries = [(occ, node) for occ, node in entries if node in kept]
    parents = {node[:-1] for _, node in entries}
    seen: dict[str, int] = {}
    rows = []
    for occ, node in entries:
        product = s.plm.product(occ.plm_id)
        chain = "/".join(node)
        seen[chain] = seen.get(chain, 0) + 1
        rows.append(
            {
                "id": chain if seen[chain] == 1 else f"{chain}#{seen[chain]}",
                "parent": "/".join(node[:-1]) or None,
                "depth": len(node) - 1,
                "name": product.name,
                "kind": product.item_kind,
                "qty": occ.cumulative_quantity,
                "mass": product.mass_kg,
                "rev": product.revision,
                "plm_id": product.plm_id,
                "match": matched is None or node in matched,
                "has_children": node in parents,
            }
        )
    return rows


def _on_tree_select(expanded_key: str) -> None:
    """Open the card of the clicked row, and unfold its assemblies so that a product picked from a search
    stays in view once the search is cleared."""
    row_id = st.session_state[TREE_KEY].select
    if not row_id:
        return
    st.session_state[TREE_SELECTED] = row_id
    st.session_state[expanded_key] = st.session_state[expanded_key] | _ancestor_ids(row_id)


def _on_tree_expanded(expanded_key: str, searching: bool) -> None:
    if not searching:
        st.session_state[expanded_key] = set(st.session_state[TREE_KEY].expanded or [])


def _set_expanded(key: str, row_ids: set[str]) -> None:
    st.session_state[key] = set(row_ids)


def focus(s: Services, plm_id: str) -> None:
    """Select a product reached from another page and unfold the assemblies down to it, wrappers hidden."""
    occurrences = s.plm.where_used(plm_id)
    row_id = "/".join(_node(s, occurrences[0], merge=True)) if occurrences else plm_id
    key = f"{TREE_EXPANDED}-True"
    opened = _ancestor_ids(row_id) | ({row_id} if s.plm.children(plm_id) else set())
    st.session_state[TREE_SELECTED] = row_id
    st.session_state[key] = st.session_state.get(key, {s.plm.root().plm_id}) | opened


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
    occurrences = s.plm.where_used(plm_id)
    if occurrences and not product.is_root:
        crumbs = [s.plm.product(pid).name for pid in occurrences[0].path[:-1] if not _is_wrapper(s, pid)]
        more = f" (and {len(occurrences) - 1} other place(s))" if len(occurrences) > 1 else ""
        st.html(f'<p class="card-crumbs">{escape(" / ".join(crumbs))}{more}</p>')
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


def _filtered(s: Services, query: str, revisions: list[str], maturities: list[str]) -> set[str] | None:
    """PLM IDs matching the search and the revision and maturity filters, None when nothing is set."""
    matches = {p.plm_id for p in s.plm.search(query)} if query else None
    if revisions or maturities:
        filtered = {
            p.plm_id
            for p in s.plm.products()
            if (not revisions or p.revision in revisions) and (not maturities or p.maturity in maturities)
        }
        matches = filtered if matches is None else matches & filtered
    return matches


def _browse(s: Services) -> None:
    products = s.plm.products()
    tree_col, card_col = st.columns([2, 3], gap="large")
    with tree_col:
        query = st.text_input(
            "Search the structure",
            placeholder="Name, PLM ID or material, e.g. bearing, 20MnCr5, 00000081",
        ).strip()
        c1, c2 = st.columns(2)
        revisions = c1.multiselect("Revision", sorted({p.revision for p in products}), placeholder="All")
        maturities = c2.multiselect("Maturity", sorted({p.maturity for p in products}), placeholder="All")
        matches = _filtered(s, query, revisions, maturities)
        root = s.plm.root().plm_id
        with st.container(horizontal=True, vertical_alignment="center"):
            merge = st.toggle(
                "Hide wrappers",
                value=True,
                help="Each ECAR-EI wrapper holds exactly one ECAR-3D part. Hiding them shows the part in its place.",
            )
            expanded_key = f"{TREE_EXPANDED}-{merge}"
            rows = _tree_rows(s, merge, matches)
            if matches is None:
                st.space("stretch")
                st.button(
                    "Expand all",
                    type="tertiary",
                    icon=":material/unfold_more:",
                    on_click=_set_expanded,
                    args=(expanded_key, {row["id"] for row in rows if row["has_children"]}),
                )
                st.button(
                    "Collapse all",
                    type="tertiary",
                    icon=":material/unfold_less:",
                    on_click=_set_expanded,
                    args=(expanded_key, {root}),
                )
        if not rows:
            st.warning("No product matches the search and filters. Try part of a name, a material or fewer filters.")
            return
        expanded = st.session_state.setdefault(expanded_key, {root})
        selected = st.session_state.get(TREE_SELECTED, root)
        if matches is None:
            st.caption("Click a row to open its card, click its chevron to fold or unfold it.")
        else:
            st.caption(f"{sum(row['match'] for row in rows)} occurrence(s) match the search and filters.")
            if selected not in {row["id"] for row in rows}:
                selected = next(row["id"] for row in rows if row["match"])
        structure_tree(
            rows,
            selected,
            expanded if matches is None else {row["id"] for row in rows if row["has_children"]},
            searching=matches is not None,
            key=TREE_KEY,
            on_select=partial(_on_tree_select, expanded_key),
            on_expanded=partial(_on_tree_expanded, expanded_key, matches is not None),
        )
    with card_col:
        _card(s, _plm_id(selected))


def _subassembly(s: Services, plm_id: str) -> str:
    """Name of the root child the product sits under (the root itself for the root)."""
    occurrences = s.plm.where_used(plm_id)
    if not occurrences or len(occurrences[0].path) < 2:
        return s.plm.product(plm_id).name
    return s.plm.product(occurrences[0].path[1]).name


def _consistency(s: Services) -> pd.DataFrame:
    rows = []
    for leaf in s.plm.leaves():
        rep = s.plm.representation(leaf.plm_id)
        wrappers = [s.plm.product(line.parent_plm_id) for line in s.plm.parents(leaf.plm_id)]
        wrappers = [w for w in wrappers if w.item_kind == "wrapper"]
        states = {(leaf.revision, leaf.maturity)}
        if rep:
            states.add((rep.revision, rep.maturity))
        states.update((w.revision, w.maturity) for w in wrappers)
        rows.append(
            {
                "Part": leaf.name,
                "PLM ID": leaf.plm_id,
                "Part rev / state": f"{leaf.revision} / {leaf.maturity}",
                "Wrapper rev / state": ", ".join(f"{w.revision} / {w.maturity}" for w in wrappers) or "No wrapper",
                "3D rev / state": f"{rep.revision} / {rep.maturity}" if rep else "No representation",
                "Consistent": len(states) == 1,
            }
        )
    return pd.DataFrame(rows)


def _versions(s: Services) -> None:
    products = s.plm.products()
    revisions = sorted({p.revision for p in products})
    maturities = sorted({p.maturity for p in products})
    families = {p.revision_family for p in products}
    frame = pd.DataFrame(
        [
            {
                "Product": p.name,
                "PLM ID": p.plm_id,
                "Kind": p.item_kind,
                "Subassembly": _subassembly(s, p.plm_id),
                "Rev": p.revision,
                "Revision index": p.revision_index,
                "Maturity": p.maturity,
            }
            for p in products
        ]
    )
    iterated = frame[frame["Revision index"] >= 3].sort_values(["Revision index", "Kind"], ascending=[False, True])
    consistency = _consistency(s)
    consistent = int(consistency["Consistent"].sum())

    ui.figures(
        [
            ui.Figure("Revision families", str(len(families)), f"For {len(products)} products, one revision each."),
            ui.Figure("Maturity states", ", ".join(maturities), "Every product of the extract."),
            ui.Figure("Iterated designs", str(len(iterated)), "Products at revision C or later."),
            ui.Figure(
                "Consistent leaf parts",
                f"{consistent} / {len(consistency)}",
                "Same revision and state on the part, its wrapper and its 3D.",
                alert=consistent < len(consistency),
            ),
        ]
    )
    ui.insight(
        "The extract holds a single revision per product and no lifecycle history: this view compares the "
        "current revisions and states across products, not versions over time."
    )

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("**Revisions by kind**")
        st.dataframe(
            pd.crosstab(frame["Kind"], frame["Rev"]).reindex(columns=revisions, fill_value=0),
            width="stretch",
        )
    with right:
        st.markdown("**Revisions by subassembly**")
        st.dataframe(
            pd.crosstab(frame["Subassembly"], frame["Rev"]).reindex(columns=revisions, fill_value=0),
            width="stretch",
        )

    st.subheader("Most iterated designs", anchor=False)
    st.dataframe(iterated.drop(columns="Revision index"), hide_index=True, width="stretch")
    st.caption("A part or assembly that went through several revisions has the least stable design.")

    st.subheader("Revision and maturity consistency", anchor=False)
    st.dataframe(
        consistency,
        hide_index=True,
        width="stretch",
        column_config={"Consistent": st.column_config.CheckboxColumn()},
    )
    st.caption("Each leaf part against the ECAR-EI wrapper that holds it and its 3D representation.")


def _compare(s: Services) -> None:
    products = s.plm.products()
    chosen = st.multiselect(
        "Products to compare (2 to 4)",
        products,
        default=[p for p in products if p.name == COMPARE_DEFAULT][:4],
        format_func=label,
        max_selections=4,
    )
    if len(chosen) < 2:
        st.info("Select at least two products to compare them side by side.")
        return
    columns = {}
    refs: list[SourceRef] = []
    for product in chosen:
        leaves = leaves_below(s, product.plm_id)
        cost = s.cost.unit_cost(product.plm_id)
        quantity = s.plm.total_quantity(product.plm_id).value
        sourcing = [row for leaf in leaves for row in s.enrichment.sourcing(leaf)]
        risk = part_risk(s, leaves[0]) if len(leaves) == 1 else None
        price = s.enrichment.material_price(product.material) if product.material else None
        columns[label(product)] = {
            "PLM ID": product.plm_id,
            "Kind": product.item_kind,
            "Subassembly": _subassembly(s, product.plm_id),
            "Revision": f"{product.revision} (index {product.revision_index})",
            "Maturity": product.maturity,
            "Unit mass (kg)": f"{product.mass_kg:.3f}",
            "Qty in motor": quantity,
            "Material": product.material or "None (assembly)",
            "Material volatility": (price.price_volatility or "Unknown") if price else "n/a",
            "Unit material cost (EUR)": f"{cost.value:.2f}" if cost else "Unknown",
            "Suppliers": ", ".join(sorted({s.enrichment.supplier(r.supplier_id).name for r in sourcing})) or "None",
            "Shortest lead time (days)": min(
                (r.lead_time_days for r in sourcing if r.lead_time_days is not None), default="n/a"
            ),
            "Risk level": risk.level if risk else "Not scored",
            "Criticality": f"{risk.criticality:.1f}" if risk else "",
            "Price risk": f"{risk.price_risk:.1f}" if risk and risk.price_risk is not None else "",
            "XML line": product.source.line,
        }
        refs.append(product.source)
        refs += [r.source for r in sourcing]
        if cost:
            refs += cost.sources
    table = pd.DataFrame(columns).astype(str)
    st.dataframe(table, width="stretch", height=35 * (len(table) + 1) + 3)
    st.caption(
        f"By default the four {COMPARE_DEFAULT}: four different gears sharing one name. Volatility, cost, suppliers, "
        "lead time and risk are team data."
    )
    ui.show_sources(refs)


def render(s: Services) -> None:
    ui.page_header(
        "Product structure",
        "The expanded product structure from the OEM extract. Select a row to open its card.",
    )
    browse, versions, compare = st.tabs(["Structure", "Versions and lifecycle", "Compare"])
    with browse:
        _browse(s)
    with versions:
        _versions(s)
    with compare:
        _compare(s)
