"""Tools the assistant calls. Each one wraps `plm`, `enrichment` or an analysis service and returns compact JSON.

Rules shared by every tool:
- products are addressed by `plm_id`; a wrapper id is resolved to its physical part, and results never list
  wrappers, so the model cannot count a wrapper and its part twice;
- every fact carries an `evidence` id registered in the conversation's `EvidenceRegistry`;
- totals, shares and costs are computed here, never by the model;
- domain errors are returned as `{"error": ...}` so the model can recover; programming errors propagate.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from plm.agent.evidence import EvidenceRegistry
from plm.analysis.cost import MaterialCostService
from plm.contract import (
    CostService,
    EnrichmentRepository,
    PlmRepository,
    UnknownProductError,
    UnknownSupplierError,
)
from plm.model import Occurrence, Product, SourceRef

DEFAULT_LIMIT = 10
MAX_LIMIT = 30
MAX_RESULT_CHARS = 12_000
COST_CAVEAT = "Material cost only (mass x material price). The purchase price of bought parts is not included."
ENRICHMENT_ORIGIN = "enrichment"

Result = dict[str, Any]


class ToolInputError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ToolContext:
    plm: PlmRepository
    enrichment: EnrichmentRepository
    costs: CostService
    registry: EvidenceRegistry
    wrapper_to_part: dict[str, str] = field(repr=False)
    part_to_wrappers: dict[str, tuple[str, ...]] = field(repr=False)

    @classmethod
    def build(
        cls,
        plm: PlmRepository,
        enrichment: EnrichmentRepository,
        registry: EvidenceRegistry | None = None,
        costs: CostService | None = None,
    ) -> ToolContext:
        wrapper_to_part = {
            p.plm_id: plm.children(p.plm_id)[0].child_plm_id for p in plm.products() if p.item_kind == "wrapper"
        }
        part_to_wrappers: dict[str, list[str]] = defaultdict(list)
        for wrapper_id, part_id in wrapper_to_part.items():
            part_to_wrappers[part_id].append(wrapper_id)
        return cls(
            plm=plm,
            enrichment=enrichment,
            costs=costs or MaterialCostService(plm, enrichment),
            registry=registry or EvidenceRegistry(),
            wrapper_to_part=wrapper_to_part,
            part_to_wrappers={k: tuple(v) for k, v in part_to_wrappers.items()},
        )

    def for_registry(self, registry: EvidenceRegistry) -> ToolContext:
        return dataclasses.replace(self, registry=registry)

    def physical(self, plm_id: str) -> Product:
        """The product itself, or the part held by a wrapper. Raises UnknownProductError."""
        product = self.plm.product(plm_id)
        if product.item_kind == "wrapper":
            return self.plm.product(self.wrapper_to_part[plm_id])
        return product

    def cite(self, label: str, refs: Iterable[SourceRef], plm_id: str | None = None, note: str | None = None) -> str:
        return self.registry.register(label, refs, plm_id=plm_id, note=note)

    def cite_product(self, product: Product) -> str:
        return self.cite(f"PLM element of {product.name} ({product.plm_id})", (product.source,), product.plm_id)

    def summary(self, product: Product) -> Result:
        result: Result = {
            "plm_id": product.plm_id,
            "name": product.name,
            "kind": product.item_kind,
            "material": product.material,
            "mass_kg": product.mass_kg,
            "evidence": self.cite_product(product),
        }
        if wrappers := self.part_to_wrappers.get(product.plm_id):
            result["wrapper_plm_ids"] = list(wrappers)
        return result

    def leaf_occurrences(self, under: str | None = None) -> list[Occurrence]:
        return [
            occ
            for occ in self.plm.occurrences()
            if self.plm.product(occ.plm_id).is_leaf and (under is None or under in occ.path)
        ]


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Result]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


def _params(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


_PLM_ID = {"type": "string", "description": "plm_id of the product, e.g. prd-12535825-00000081"}
_LIMIT = {"type": "integer", "description": f"Maximum number of items (default {DEFAULT_LIMIT}, max {MAX_LIMIT})"}


def _limit(value: int | None, default: int = DEFAULT_LIMIT) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ToolInputError(f"limit must be a positive integer, got {value!r}")
    return min(value, MAX_LIMIT)


def _compact(text: str) -> str:
    return re.sub(r"[\s_\-]+", "", text.casefold())


def _matches_all_words(product: Product, words: list[str]) -> bool:
    haystack = f"{product.name_normalized} {product.plm_id} {product.material or ''}".casefold()
    compact = _compact(haystack)
    return all(word in haystack or _compact(word) in compact for word in words)


def search_products(ctx: ToolContext, query: str, kind: str | None = None, limit: int | None = None) -> Result:
    if kind not in (None, "part", "assembly"):
        raise ToolInputError(f"kind must be 'part' or 'assembly', got {kind!r}")
    limit = _limit(limit)
    query = " ".join(query.split())
    if not query:
        raise ToolInputError("query must not be empty")

    raw = {p.plm_id: p for p in ctx.plm.search(query)}
    match = "substring"
    if not raw:
        words = query.casefold().split()
        raw = {p.plm_id: p for p in ctx.plm.products() if _matches_all_words(p, words)}
        match = "all_words"

    physical = list({p.plm_id: p for p in (ctx.physical(pid) for pid in raw)}.values())
    if kind:
        physical = [p for p in physical if p.item_kind == kind]
    result: Result = {
        "query": query,
        "match": match if physical else "none",
        "total": len(physical),
        "truncated": len(physical) > limit,
        "products": [ctx.summary(p) for p in physical[:limit]],
    }
    if not physical:
        result["hint"] = (
            "No product matches. Check the part list in the instructions; if one more short query finds nothing, "
            "answer that the extract does not contain it."
        )
    return result


def get_product(ctx: ToolContext, plm_id: str) -> Result:
    product = ctx.physical(plm_id)
    quantity = ctx.plm.total_quantity(product.plm_id)
    card = ctx.summary(product) | {
        "revision": product.revision,
        "revision_index": product.revision_index,
        "maturity": product.maturity,
        "lifecycle": product.lifecycle,
        "description": product.description,
        "modified_at": product.modified_at.isoformat(),
        "is_root": product.is_root,
        "total_quantity_in_motor": quantity.value,
        "total_quantity_evidence": ctx.cite(
            f"Total quantity of {product.name} in the motor", quantity.sources, product.plm_id
        ),
        "used_in_assemblies": [
            {"plm_id": a.plm_id, "name": a.name, "evidence": ctx.cite_product(a)}
            for a in _assemblies_above(ctx, product)
        ],
    }
    if product.plm_id != plm_id:
        card["resolved_from_wrapper"] = plm_id
    if product.is_leaf:
        representation = ctx.plm.representation(product.plm_id)
        if representation is not None:
            card["representation"] = {
                "name": representation.name,
                "evidence": ctx.cite(f"3D representation of {product.name}", (representation.source,), product.plm_id),
            }
        card["supplier_count"] = len(ctx.enrichment.sourcing(product.plm_id))
        card["supplier_count_origin"] = ENRICHMENT_ORIGIN
    else:
        card["direct_children_count"] = len(ctx.plm.children(product.plm_id))
    return card


def _assemblies_above(ctx: ToolContext, product: Product) -> list[Product]:
    """Real assemblies on any path from the root to the product, root first, the product excluded."""
    found: dict[str, Product] = {}
    for occ in ctx.plm.where_used(product.plm_id):
        for pid in occ.path[:-1]:
            ancestor = ctx.plm.product(pid)
            if ancestor.item_kind == "assembly":
                found.setdefault(pid, ancestor)
    return list(found.values())


def get_children(ctx: ToolContext, plm_id: str, limit: int | None = None) -> Result:
    parent = ctx.physical(plm_id)
    limit = _limit(limit, default=MAX_LIMIT)
    lines = ctx.plm.children(parent.plm_id)
    children = []
    for line in lines[:limit]:
        child = ctx.physical(line.child_plm_id)
        refs = list(line.sources)
        if child.plm_id != line.child_plm_id:
            refs += [s for wline in ctx.plm.children(line.child_plm_id) for s in wline.sources]
        children.append(
            ctx.summary(child)
            | {
                "quantity": line.quantity,
                "bom_line_evidence": ctx.cite(f"BOM line {parent.name} > {child.name}", refs, child.plm_id),
            }
        )
    return {
        "parent": ctx.summary(parent),
        "total": len(lines),
        "truncated": len(lines) > limit,
        "children": children,
        **({"note": "Leaf part: it has no children."} if parent.is_leaf else {}),
    }


def get_where_used(ctx: ToolContext, plm_id: str) -> Result:
    product = ctx.physical(plm_id)
    quantity = ctx.plm.total_quantity(product.plm_id)
    occurrences = []
    for occ in ctx.plm.where_used(product.plm_id):
        on_path = [ctx.plm.product(pid) for pid in occ.path]
        path = " > ".join(p.name for p in on_path if p.item_kind != "wrapper")
        wrappers = [p.name for p in on_path if p.item_kind == "wrapper"]
        occurrences.append(
            {
                "path": path,
                **({"position": wrappers[-1]} if wrappers else {}),
                "cumulative_quantity": occ.cumulative_quantity,
                "evidence": ctx.cite(f"Occurrence {' > '.join(p.name for p in on_path)}", occ.sources, product.plm_id),
            }
        )
    return {
        "product": ctx.summary(product),
        "occurrence_count": len(occurrences),
        "occurrences": occurrences,
        "impacted_assemblies": [
            {"plm_id": a.plm_id, "name": a.name, "evidence": ctx.cite_product(a)}
            for a in _assemblies_above(ctx, product)
        ],
        "total_quantity_in_motor": quantity.value,
        "total_quantity_evidence": ctx.cite(
            f"Total quantity of {product.name} in the motor", quantity.sources, product.plm_id
        ),
    }


def get_total_quantity(ctx: ToolContext, plm_id: str) -> Result:
    product = ctx.physical(plm_id)
    quantity = ctx.plm.total_quantity(product.plm_id)
    return {
        "product": ctx.summary(product),
        "total_quantity_in_motor": quantity.value,
        "evidence": ctx.cite(f"Total quantity of {product.name} in the motor", quantity.sources, product.plm_id),
    }


def get_unit_cost(ctx: ToolContext, plm_id: str) -> Result:
    product = ctx.physical(plm_id)
    cost = ctx.costs.unit_cost(product.plm_id)
    result: Result = {"product": ctx.summary(product), "origin": ENRICHMENT_ORIGIN, "caveat": COST_CAVEAT}
    if cost is None:
        leaves_below = {occ.plm_id for occ in ctx.leaf_occurrences(under=product.plm_id)}
        missing = [leaf for leaf in ctx.costs.missing_prices() if leaf.plm_id in leaves_below]
        return result | {
            "unit_cost_eur": None,
            "reason": "At least one part below has no material or no material price.",
            "parts_without_price": [ctx.summary(p) for p in missing],
        }

    result["unit_cost_eur"] = round(cost.value, 2)
    result["evidence"] = ctx.cite(f"Material cost of one {product.name}", cost.sources, product.plm_id, COST_CAVEAT)
    if product.is_leaf and product.material:
        price = ctx.enrichment.material_price(product.material)
        if price is not None:
            result["material_price_eur_per_kg"] = price.price_eur_per_kg
            result["material_price_note"] = price.source_note
    if not product.is_root:
        quantity = ctx.plm.total_quantity(product.plm_id)
        result["total_quantity_in_motor"] = quantity.value
        result["cost_in_motor_eur"] = round(cost.value * quantity.value, 2)
        result["cost_in_motor_evidence"] = ctx.cite(
            f"Material cost of all {product.name} in the motor",
            (*cost.sources, *quantity.sources),
            product.plm_id,
            COST_CAVEAT,
        )
    return result


MAX_COMPARED = 10


def compare_products(ctx: ToolContext, plm_ids: list[str]) -> Result:
    if not isinstance(plm_ids, list) or not plm_ids or not all(isinstance(i, str) for i in plm_ids):
        raise ToolInputError("plm_ids must be a non-empty list of plm_id strings")
    if len(plm_ids) > MAX_COMPARED:
        raise ToolInputError(f"At most {MAX_COMPARED} products can be compared at once, got {len(plm_ids)}")

    cards: dict[str, Result] = {}
    unknown = []
    for plm_id in dict.fromkeys(plm_ids):
        try:
            product = ctx.physical(plm_id)
        except UnknownProductError:
            unknown.append(plm_id)
            continue
        if product.plm_id in cards:
            continue
        quantity = ctx.plm.total_quantity(product.plm_id)
        cost = ctx.costs.unit_cost(product.plm_id)
        card = ctx.summary(product) | {
            "assemblies": [a.name for a in _assemblies_above(ctx, product)],
            "total_quantity_in_motor": quantity.value,
            "total_quantity_evidence": ctx.cite(
                f"Total quantity of {product.name} in the motor", quantity.sources, product.plm_id
            ),
            "unit_cost_eur": round(cost.value, 2) if cost else None,
        }
        if cost:
            card["unit_cost_evidence"] = ctx.cite(
                f"Material cost of one {product.name}", cost.sources, product.plm_id, COST_CAVEAT
            )
        if product.is_leaf:
            card["suppliers"] = []
            for sourcing in ctx.enrichment.sourcing(product.plm_id):
                supplier = ctx.enrichment.supplier(sourcing.supplier_id)
                card["suppliers"].append(
                    {
                        "supplier_id": supplier.supplier_id,
                        "name": supplier.name,
                        "lead_time_days": sourcing.lead_time_days,
                        "evidence": ctx.cite(
                            f"Sourcing of {product.name} from {supplier.name}",
                            (sourcing.source, supplier.source),
                            product.plm_id,
                            sourcing.source_note,
                        ),
                    }
                )
        cards[product.plm_id] = card
    return {
        "products": list(cards.values()),
        "unknown_plm_ids": unknown,
        "origin_of_costs_and_suppliers": ENRICHMENT_ORIGIN,
        "caveat": COST_CAVEAT,
    }


def get_sourcing(ctx: ToolContext, plm_id: str) -> Result:
    product = ctx.physical(plm_id)
    if not product.is_leaf:
        raise ToolInputError(
            f"{product.name} is an assembly: sourcing is defined on parts only. "
            "Use aggregate_leaves(group_by='supplier') or get_children instead."
        )
    entries = []
    for sourcing in ctx.enrichment.sourcing(product.plm_id):
        supplier = ctx.enrichment.supplier(sourcing.supplier_id)
        entries.append(
            {
                "supplier_id": supplier.supplier_id,
                "name": supplier.name,
                "country": supplier.country,
                "make_or_buy": sourcing.make_or_buy,
                "lead_time_days": sourcing.lead_time_days,
                "source_note": sourcing.source_note,
                "evidence": ctx.cite(
                    f"Sourcing of {product.name} from {supplier.name}",
                    (sourcing.source, supplier.source),
                    product.plm_id,
                    sourcing.source_note,
                ),
            }
        )
    return {
        "product": ctx.summary(product),
        "origin": ENRICHMENT_ORIGIN,
        "supplier_count": len(entries),
        "single_source": len(entries) == 1,
        "suppliers": entries,
    }


def get_supplier_dependencies(
    ctx: ToolContext,
    supplier_id: str | None = None,
    single_source_only: bool = False,
    limit: int | None = None,
) -> Result:
    parts_limit = _limit(limit, default=MAX_LIMIT)
    limit = _limit(limit)
    by_supplier: dict[str, list[tuple[Product, SourceRef]]] = defaultdict(list)
    supplier_count: dict[str, int] = {}
    for leaf in ctx.plm.leaves():
        sourcing = ctx.enrichment.sourcing(leaf.plm_id)
        supplier_count[leaf.plm_id] = len(sourcing)
        for entry in sourcing:
            by_supplier[entry.supplier_id].append((leaf, entry.source))

    def part_entry(leaf: Product, ref: SourceRef, supplier_name: str) -> Result:
        return {
            "plm_id": leaf.plm_id,
            "name": leaf.name,
            "single_source": supplier_count[leaf.plm_id] == 1,
            "evidence": ctx.cite(f"Sourcing of {leaf.name} from {supplier_name}", (ref,), leaf.plm_id),
        }

    if supplier_id is not None:
        supplier = ctx.enrichment.supplier(supplier_id)
        parts = [
            p for p in by_supplier.get(supplier_id, []) if not single_source_only or supplier_count[p[0].plm_id] == 1
        ]
        return {
            "origin": ENRICHMENT_ORIGIN,
            "supplier": {
                "supplier_id": supplier.supplier_id,
                "name": supplier.name,
                "country": supplier.country,
                "evidence": ctx.cite(f"Supplier {supplier.name}", (supplier.source,)),
            },
            "part_count": len(parts),
            "single_source_part_count": sum(supplier_count[leaf.plm_id] == 1 for leaf, _ in parts),
            "truncated": len(parts) > parts_limit,
            "parts": [part_entry(leaf, ref, supplier.name) for leaf, ref in parts[:parts_limit]],
        }

    ranking = []
    for sid, parts in by_supplier.items():
        if single_source_only:
            parts = [(leaf, ref) for leaf, ref in parts if supplier_count[leaf.plm_id] == 1]
        if not parts:
            continue
        supplier = ctx.enrichment.supplier(sid)
        ranking.append(
            {
                "supplier_id": sid,
                "name": supplier.name,
                "country": supplier.country,
                "part_count": len(parts),
                "single_source_part_count": sum(supplier_count[leaf.plm_id] == 1 for leaf, _ in parts),
                "evidence": ctx.cite(
                    f"Parts {'single-' if single_source_only else ''}sourced from {supplier.name}",
                    (supplier.source, *(ref for _, ref in parts)),
                ),
            }
        )
    ranking.sort(key=lambda r: (-r["part_count"], r["supplier_id"]))
    single = [leaf for leaf in ctx.plm.leaves() if supplier_count[leaf.plm_id] == 1]
    without = [leaf for leaf in ctx.plm.leaves() if supplier_count[leaf.plm_id] == 0]
    single_refs = [s.source for leaf in single for s in ctx.enrichment.sourcing(leaf.plm_id)]
    return {
        "origin": ENRICHMENT_ORIGIN,
        "part_count": len(supplier_count),
        "single_source_part_count": len(single),
        **({"single_source_evidence": ctx.cite("Parts with a single supplier", single_refs)} if single_refs else {}),
        "parts_without_supplier": [ctx.summary(leaf) for leaf in without],
        "supplier_total": len(ranking),
        "truncated": len(ranking) > limit,
        "suppliers": ranking[:limit],
        **(
            {
                "single_source_parts_truncated": len(single) > parts_limit,
                "single_source_parts": [
                    ctx.summary(leaf) | {"supplier_id": ctx.enrichment.sourcing(leaf.plm_id)[0].supplier_id}
                    for leaf in single[:parts_limit]
                ],
            }
            if single_source_only
            else {}
        ),
    }


_GROUP_BY = ("material", "supplier", "country", "subassembly", "assembly")
_METRICS = {"mass_kg": "kg", "material_cost_eur": "EUR", "count": "units"}


def aggregate_leaves(ctx: ToolContext, group_by: str, metric: str, top: int | None = None) -> Result:
    if group_by not in _GROUP_BY:
        raise ToolInputError(f"group_by must be one of {', '.join(_GROUP_BY)}, got {group_by!r}")
    if metric not in _METRICS:
        raise ToolInputError(f"metric must be one of {', '.join(_METRICS)}, got {metric!r}")
    top = _limit(top)

    values: dict[str, float] = defaultdict(float)
    parts: dict[str, set[str]] = defaultdict(set)
    refs: dict[str, list[SourceRef]] = defaultdict(list)
    ids: dict[str, str | None] = {}
    total = 0.0
    unpriced: dict[str, Product] = {}

    for occ in ctx.leaf_occurrences():
        leaf = ctx.plm.product(occ.plm_id)
        quantity = occ.cumulative_quantity
        own_refs = [leaf.source, *occ.sources]
        if metric == "count":
            value = float(quantity)
        elif metric == "mass_kg":
            value = leaf.mass_kg * quantity
        else:
            cost = ctx.costs.unit_cost(leaf.plm_id)
            if cost is None:
                unpriced[leaf.plm_id] = leaf
                continue
            value = cost.value * quantity
            own_refs += cost.sources
        total += value

        for key, key_id, extra_refs in _group_keys(ctx, group_by, leaf, occ):
            values[key] += value
            parts[key].add(leaf.plm_id)
            refs[key] += [*own_refs, *extra_refs]
            ids[key] = key_id

    unit = _METRICS[metric]
    ordered = sorted(values, key=lambda k: (-values[k], k))
    digits = {"material_cost_eur": 2, "mass_kg": 3, "count": 0}[metric]

    def rounded(value: float) -> float | int:
        return int(round(value)) if digits == 0 else round(value, digits)

    groups = [
        {
            "key": key,
            **({"id": ids[key]} if ids[key] else {}),
            "value": rounded(values[key]),
            "share_of_total_pct": round(100 * values[key] / total, 1) if total else None,
            "part_count": len(parts[key]),
            "evidence": ctx.cite(f"{metric} of the parts grouped under {group_by} = {key}", refs[key], ids[key]),
        }
        for key in ordered[:top]
    ]
    result: Result = {
        "group_by": group_by,
        "metric": metric,
        "unit": unit,
        "total": rounded(total),
        "group_count": len(ordered),
        "truncated": len(ordered) > top,
        "groups": groups,
        "method": "Sum over physical parts, weighted by their cumulative quantity in the motor (wrappers excluded).",
    }
    if group_by in ("supplier", "country"):
        result["origin"] = ENRICHMENT_ORIGIN
        result["note"] = (
            "A part with several suppliers counts in each of its groups, so groups can add up to more than the total."
        )
    if metric == "material_cost_eur":
        result["origin"] = ENRICHMENT_ORIGIN
        result["caveat"] = COST_CAVEAT
        result["parts_without_price"] = [ctx.summary(p) for p in unpriced.values()]
    return result


def _group_keys(
    ctx: ToolContext, group_by: str, leaf: Product, occ: Occurrence
) -> list[tuple[str, str | None, list[SourceRef]]]:
    if group_by == "material":
        return [(leaf.material or "(no material)", None, [])]
    if group_by in ("subassembly", "assembly"):
        if group_by == "subassembly":
            pid = occ.path[1] if len(occ.path) > 2 else occ.path[0]
        else:
            pid = next(p for p in reversed(occ.path[:-1]) if ctx.plm.product(p).item_kind == "assembly")
        assembly = ctx.plm.product(pid)
        return [(assembly.name, assembly.plm_id, [assembly.source])]

    sourcing = ctx.enrichment.sourcing(leaf.plm_id)
    if not sourcing:
        return [("(no supplier)", None, [])]
    keys: dict[str, tuple[str | None, list[SourceRef]]] = {}
    for entry in sourcing:
        supplier = ctx.enrichment.supplier(entry.supplier_id)
        key = f"{supplier.name} ({supplier.supplier_id})" if group_by == "supplier" else supplier.country
        key_id = supplier.supplier_id if group_by == "supplier" else None
        keys.setdefault(key, (key_id, []))[1].extend((entry.source, supplier.source))
    return [(key, key_id, extra) for key, (key_id, extra) in keys.items()]


TOOLS: tuple[Tool, ...] = (
    Tool(
        "search_products",
        "Find products by name, plm_id or material (case-insensitive, short queries work best). "
        "Returns physical parts and assemblies; wrappers are merged into their part.",
        _params(
            {
                "query": {"type": "string", "description": "One or two words, e.g. 'bearing', 'D10', 'aluminium'"},
                "kind": {"type": "string", "enum": ["part", "assembly"], "description": "Optional filter"},
                "limit": _LIMIT,
            },
            ("query",),
        ),
        search_products,
    ),
    Tool(
        "get_product",
        "Full card of one product: revision, maturity, material, mass, total quantity in the motor, "
        "assemblies it belongs to, 3D representation, number of suppliers.",
        _params({"plm_id": _PLM_ID}, ("plm_id",)),
        get_product,
    ),
    Tool(
        "get_children",
        "Direct children of an assembly in the BOM, with their quantity under this parent.",
        _params({"plm_id": _PLM_ID, "limit": _LIMIT}, ("plm_id",)),
        get_children,
    ),
    Tool(
        "get_where_used",
        "Every place a product is used: paths from the root, cumulative quantities and impacted assemblies. "
        "For the impact of changing or replacing a part, also call get_sourcing and get_unit_cost on it.",
        _params({"plm_id": _PLM_ID}, ("plm_id",)),
        get_where_used,
    ),
    Tool(
        "get_total_quantity",
        "Number of units of a product in the complete motor, cumulated over the whole BOM.",
        _params({"plm_id": _PLM_ID}, ("plm_id",)),
        get_total_quantity,
    ),
    Tool(
        "get_unit_cost",
        "Material cost in EUR of one unit of a product (mass x material price, rolled up through the BOM) "
        "and of all its units in the motor.",
        _params({"plm_id": _PLM_ID}, ("plm_id",)),
        get_unit_cost,
    ),
    Tool(
        "compare_products",
        f"Side-by-side summary of up to {MAX_COMPARED} products in one call: mass, material, assemblies, quantity "
        "in the motor, material cost and suppliers with lead times. Use it for homonyms (several products with the "
        "same name) and for any question about several products, instead of one call per product.",
        _params(
            {"plm_ids": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_COMPARED}},
            ("plm_ids",),
        ),
        compare_products,
    ),
    Tool(
        "get_sourcing",
        "Suppliers able to deliver a part, with country, make or buy and lead time in days.",
        _params({"plm_id": _PLM_ID}, ("plm_id",)),
        get_sourcing,
    ),
    Tool(
        "get_supplier_dependencies",
        "Use for any question on supplier dependency or supply risk. Without supplier_id: suppliers ranked by "
        "number of distinct parts they deliver and how many of those are single-source, plus the total count of "
        "single-source parts (single_source_only=true lists them). With supplier_id: the parts that supplier delivers.",
        _params(
            {
                "supplier_id": {"type": "string", "description": "Optional, e.g. SUP-003"},
                "single_source_only": {"type": "boolean", "description": "Keep only parts with a single supplier"},
                "limit": _LIMIT,
            }
        ),
        get_supplier_dependencies,
    ),
    Tool(
        "aggregate_leaves",
        "Totals over all physical parts of the motor, grouped and sorted, with each group's share of the total. "
        "subassembly = the major subassemblies under the root; assembly = the nearest real assembly. "
        "count = number of physical units (a screw used 14 times counts 14), not of distinct parts. "
        "Use it for mass, cost or unit totals, rankings and percentages instead of computing yourself; "
        "for supplier dependency use get_supplier_dependencies.",
        _params(
            {
                "group_by": {"type": "string", "enum": list(_GROUP_BY)},
                "metric": {"type": "string", "enum": list(_METRICS)},
                "top": {"type": "integer", "description": f"Number of groups returned (default {DEFAULT_LIMIT})"},
            },
            ("group_by", "metric"),
        ),
        aggregate_leaves,
    ),
)

TOOLS_BY_NAME: dict[str, Tool] = {tool.name: tool for tool in TOOLS}


def tool_schemas(tools: Iterable[Tool] = TOOLS) -> list[dict[str, Any]]:
    return [tool.schema() for tool in tools]


def execute(ctx: ToolContext, name: str, raw_arguments: str, tools: dict[str, Tool] = TOOLS_BY_NAME) -> Result:
    """Run one tool call from the model. Never raises on a bad call: the error goes back to the model."""
    tool = tools.get(name)
    if tool is None:
        return {"error": f"Unknown tool {name!r}. Available tools: {', '.join(tools)}"}
    try:
        arguments = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError as exc:
        return {"error": f"Arguments are not valid JSON: {exc}"}
    if not isinstance(arguments, dict):
        return {"error": "Arguments must be a JSON object"}

    arguments = {k: v for k, v in arguments.items() if v is not None}
    properties = tool.parameters["properties"]
    if unknown := sorted(set(arguments) - set(properties)):
        return {"error": f"Unknown argument(s) {', '.join(unknown)} for {name}. Expected: {', '.join(properties)}"}
    if missing := [k for k in tool.parameters["required"] if k not in arguments]:
        return {"error": f"Missing required argument(s) {', '.join(missing)} for {name}"}

    try:
        result = tool.handler(ctx, **arguments)
    except (UnknownProductError, UnknownSupplierError) as exc:
        return {"error": f"{exc.args[0] if exc.args else exc}. Use search_products to find valid ids."}
    except ToolInputError as exc:
        return {"error": str(exc)}

    if len(json.dumps(result, ensure_ascii=False)) > MAX_RESULT_CHARS:
        return {"error": f"Result of {name} is too large. Narrow the request (lower limit or top)."}
    return result
