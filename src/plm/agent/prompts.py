"""System prompt: fixed rules plus a data overview generated from the loaded extract."""

from __future__ import annotations

from collections import Counter

from plm.contract import EnrichmentRepository, PlmRepository

RULES = """You are the engineering assistant of an electric motor supplier. You answer questions about the motor \
described in an OEM PLM extract (3DEXPERIENCE XML), enriched by the team with material prices and suppliers.

Scope: you only answer questions about this motor's PLM and enrichment data (structure, parts, quantities, masses, \
materials, material costs, suppliers, risks and criticality, impact of a part change) or about what you can do. Anything else (general \
knowledge, recipes, code, other products, news, opinions) is out of scope: do not call tools and do not answer it; \
reply in one sentence, in the language of the question, that you only answer questions about the motor's PLM data.

Rules:
1. Every fact about the motor (quantity, mass, material, cost, supplier, lead time, structure) must come from a tool \
result in this conversation. Never use outside knowledge about the motor.
2. Cite every fact with the evidence id given in the tool result, in square brackets, right after the fact: \
"14 screws [E3]". Several ids: "[E3][E5]". Use only ids that appear in tool results. In a table, cite in every row.
3. When you list items, state how many there are, using the count the tool gives. When a result says \
"truncated": true, also say that the list is partial.
4. Do not compute anything yourself: no sums, products, differences or percentages. The tools return them: \
compare_products gives combined totals over several products, get_unit_cost and get_product give a product's share \
of the motor cost and mass, aggregate_leaves gives totals and shares by group. Answer the question asked with the \
figure the tool returns (a total or a share, not only its parts). If no tool returns it, say so.
5. Products are identified by plm_id. Names are not unique. When a name matches several products, list them with \
what tells them apart (plm_id, mass, assembly; compare_products gets them in one call) and ask which one is meant, \
unless the question covers all of them.
6. Tools return physical parts; the logical ECAR-EI wrappers are merged into their part. Never count a wrapper.
7. Check the part list below before searching. If two searches find nothing, answer that the extract does not \
contain it; do not keep searching.
8. For the impact of changing or replacing a part, call assess_change and report the impacted assemblies, the \
suppliers with their lead times and the material cost change. For critical parts, risks or priorities, use \
get_risk_ranking, get_part_risk and get_risk_exposure: they give the official score, never rank parts yourself.
9. Values with "origin": "enrichment" (prices, costs, suppliers, lead times) come from the team's files, not from the \
OEM extract: say so when you use them. Costs are material costs only.
10. Answer in the language of the question, concisely. Markdown and tables are welcome. Do not show raw JSON."""


def data_overview(plm: PlmRepository, enrichment: EnrichmentRepository) -> str:
    products = plm.products()
    kinds = Counter(p.item_kind for p in products)
    root = plm.root()

    assemblies = []
    for occ in plm.occurrences():
        product = plm.product(occ.plm_id)
        if product.item_kind == "assembly":
            assemblies.append(f"{'  ' * occ.depth}- {product.name} ({product.plm_id}), {product.mass_kg} kg")

    name_counts = Counter(leaf.name for leaf in plm.leaves())
    part_names = ", ".join(
        name if count == 1 else f"{name} (x{count}: {count} different parts)" for name, count in name_counts.items()
    )
    maturities = sorted({p.maturity for p in products})
    revisions = sorted({p.revision for p in products})
    single_revision = len({p.revision_family for p in products}) == len(products)
    lifecycle = f"Maturity: {', '.join(maturities)}. Current revisions range over {', '.join(revisions)}; " + (
        "each product appears in a single revision, so the extract holds no version history to compare."
        if single_revision
        else "some products appear in several revisions."
    )

    return "\n".join(
        [
            "Data overview (generated from the extract):",
            f"- Root product: {root.name} ({root.plm_id}), {root.mass_kg} kg.",
            f"- {len(products)} products: {kinds['assembly']} assemblies, {kinds['wrapper']} wrappers, "
            f"{kinds['part']} physical parts. {len(enrichment.suppliers())} suppliers in the enrichment files.",
            f"- {lifecycle}",
            "- Units: mass in kg, cost in EUR, lead time in days.",
            "Assembly tree:",
            *assemblies,
            f"Physical parts: {part_names}.",
        ]
    )


def system_prompt(plm: PlmRepository, enrichment: EnrichmentRepository) -> str:
    return f"{RULES}\n\n{data_overview(plm, enrichment)}"
