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
from plm.analysis.impact import ChangeImpactService
from plm.analysis.risk import PartRisk, RiskService
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
RISK_CAVEAT = (
    "Official risk score of the application (docs/risk_rules.md). Supplier, lead time and volatility data are "
    "team assumptions from the enrichment files."
)

Result = dict[str, Any]


class ToolInputError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ToolContext:
    plm: PlmRepository
    enrichment: EnrichmentRepository
    costs: CostService
    risk: RiskService
    impact: ChangeImpactService
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
        costs = costs or MaterialCostService(plm, enrichment)
        risk = RiskService(plm, enrichment, costs)
        return cls(
            plm=plm,
            enrichment=enrichment,
            costs=costs,
            risk=risk,
            impact=ChangeImpactService(plm, enrichment, costs, risk),
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
    if not product.is_root:
        root = ctx.plm.root()
        mass_in_motor = product.mass_kg * quantity.value
        card["mass_in_motor_kg"] = round(mass_in_motor, 3)
        card["share_of_motor_mass_pct"] = round(100 * mass_in_motor / root.mass_kg, 1) if root.mass_kg else None
        card["mass_in_motor_evidence"] = ctx.cite(
            f"Mass of all {product.name} in the motor and share of the motor mass",
            (product.source, *quantity.sources, root.source),
            product.plm_id,
        )
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
        motor_cost = ctx.costs.unit_cost(ctx.plm.root().plm_id)
        if motor_cost is not None and motor_cost.value:
            result["share_of_motor_material_cost_pct"] = round(100 * cost.value * quantity.value / motor_cost.value, 1)
            result["share_evidence"] = ctx.cite(
                f"Share of {product.name} in the material cost of the motor",
                (*cost.sources, *quantity.sources, *motor_cost.sources),
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
    total_quantity, total_mass, total_cost = 0, 0.0, 0.0
    cost_complete = True
    total_refs: list[SourceRef] = []
    ancestors: set[str] = set()
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
        above = _assemblies_above(ctx, product)
        ancestors.update(a.plm_id for a in above)
        total_quantity += quantity.value
        total_mass += product.mass_kg * quantity.value
        total_refs += [product.source, *quantity.sources]
        if cost is None:
            cost_complete = False
        else:
            total_cost += cost.value * quantity.value
            total_refs += cost.sources
        card = ctx.summary(product) | {
            "assemblies": [a.name for a in above],
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
    totals: Result = {}
    if cards:
        totals = {
            "product_count": len(cards),
            "total_quantity_in_motor": total_quantity,
            "mass_in_motor_kg": round(total_mass, 3),
            "material_cost_in_motor_eur": round(total_cost, 2) if cost_complete else None,
            "evidence": ctx.cite(
                f"Combined totals of {', '.join(c['name'] for c in cards.values())} in the motor",
                total_refs,
                note=COST_CAVEAT,
            ),
        }
        if ancestors & set(cards):
            totals["warning"] = "One compared product contains another: these totals count the shared parts twice."
    return {
        "products": list(cards.values()),
        "totals": totals,
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
    counted: set[str] = set()
    total_refs: list[SourceRef] = []
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
        counted.add(leaf.plm_id)
        total_refs += own_refs

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
        "distinct_part_count": len(counted),
        "group_count": len(ordered),
        "truncated": len(ordered) > top,
        "groups": groups,
        "method": "Sum over physical parts, weighted by their cumulative quantity in the motor (wrappers excluded).",
    }
    if total_refs:
        result["total_evidence"] = ctx.cite(
            f"Total {metric} over the {len(counted)} distinct physical parts", list(dict.fromkeys(total_refs))
        )
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


def _risk_entry(ctx: ToolContext, risk: PartRisk, details: bool = False) -> Result:
    product = ctx.plm.product(risk.plm_id)
    entry: Result = {
        "part": ctx.summary(product),
        "level": risk.level,
        "criticality_score_0_100": risk.criticality,
        "probability_score_0_1": round(risk.probability, 2),
        "impact_score_0_1": round(risk.impact, 2),
        "price_risk_score_0_100": risk.price_risk,
        "price_level": risk.price_level,
        "evidence": ctx.cite(f"Risk score of {product.name}", risk.sources, product.plm_id, RISK_CAVEAT),
    }
    if not details:
        entry["main_drivers"] = [f"{c.code}: {c.value}" for c in risk.criteria if c.score >= 0.5]
        return entry
    entry["criteria"] = [
        {
            "code": c.code,
            "axis": c.axis,
            "value": c.value,
            "score_0_1": round(c.score, 2),
            "explanation": c.explanation,
            "evidence": ctx.cite(f"{c.code} of {product.name}", c.sources, product.plm_id, RISK_CAVEAT),
        }
        for c in risk.criteria
    ]
    return entry


def get_risk_ranking(
    ctx: ToolContext, view: str = "supply", level: str | None = None, limit: int | None = None
) -> Result:
    if view not in ("supply", "price"):
        raise ToolInputError("view must be 'supply' or 'price'")
    if level is not None and level not in ("high", "medium", "low"):
        raise ToolInputError("level must be 'high', 'medium' or 'low'")
    ranking = ctx.risk.ranking() if view == "supply" else ctx.risk.price_ranking()
    if level is not None:
        ranking = [r for r in ranking if (r.level if view == "supply" else r.price_level) == level]
    n = _limit(limit)
    counts = {lvl: sum(1 for r in ctx.risk.ranking() if r.level == lvl) for lvl in ("high", "medium", "low")}
    return {
        "view": view,
        "origin": ENRICHMENT_ORIGIN,
        "caveat": RISK_CAVEAT,
        "scored_part_count": len(ctx.risk.ranking()),
        "supply_risk_count_by_level": counts,
        "total": len(ranking),
        "truncated": len(ranking) > n,
        "parts": [_risk_entry(ctx, r) for r in ranking[:n]],
    }


def get_part_risk(ctx: ToolContext, plm_id: str) -> Result:
    product = ctx.physical(plm_id)
    if not product.is_leaf:
        assembly = ctx.risk.assembly_risk(product.plm_id)
        return {
            "assembly": ctx.summary(product),
            "origin": ENRICHMENT_ORIGIN,
            "caveat": RISK_CAVEAT,
            "scored_parts_below": len(assembly.parts),
            "count_by_level": assembly.count_by_level,
            "most_critical": [_risk_entry(ctx, r) for r in assembly.parts[:5]],
        }
    try:
        risk = ctx.risk.part_risk(product.plm_id)
    except ValueError:
        raise ToolInputError(
            f"{product.name} is not scored: it is a stator meshing artefact, not a physical part"
        ) from None
    return {"origin": ENRICHMENT_ORIGIN, "caveat": RISK_CAVEAT, **_risk_entry(ctx, risk, details=True)}


def get_risk_exposure(ctx: ToolContext, by: str, top: int | None = None) -> Result:
    n = _limit(top)
    if by == "supplier":
        exposure = [e for e in ctx.risk.supplier_exposure() if e.parts]
        groups = [
            {
                "supplier_id": e.supplier_id,
                "name": e.name,
                "country": e.country,
                "parts_delivered": len(e.parts),
                "single_sourced_parts": [ctx.summary(ctx.plm.product(p)) for p in e.single_sourced_parts],
                "share_of_material_cost_stopped_if_it_fails_pct": round(100 * e.single_sourced_cost_share, 1),
                "evidence": ctx.cite(f"Exposure to supplier {e.name}", e.sources, note=RISK_CAVEAT),
            }
            for e in exposure[:n]
        ]
    elif by == "material":
        exposure = ctx.risk.material_exposure()
        groups = [
            {
                "material": m.material,
                "distinct_parts": len(m.parts),
                "units_in_motor": m.units,
                "mass_kg": round(m.mass_kg, 2),
                "share_of_mass_pct": round(100 * m.mass_share, 1),
                "material_cost_eur": round(m.cost_eur, 2) if m.cost_eur is not None else None,
                "share_of_material_cost_pct": round(100 * m.cost_share, 1) if m.cost_share is not None else None,
                "supplier_count": len(m.suppliers),
                "single_sourced_part_count": len(m.single_sourced_parts),
                "price_volatility": m.price_volatility,
                "evidence": ctx.cite(f"Exposure to material {m.material}", m.sources, note=RISK_CAVEAT),
            }
            for m in exposure[:n]
        ]
    else:
        raise ToolInputError("by must be 'supplier' or 'material'")
    return {
        "by": by,
        "origin": ENRICHMENT_ORIGIN,
        "caveat": RISK_CAVEAT + " Stator meshing artefacts are excluded.",
        "total": len(exposure),
        "truncated": len(exposure) > n,
        "groups": groups,
    }


def assess_change(
    ctx: ToolContext,
    plm_id: str,
    new_mass_kg: float | None = None,
    new_material: str | None = None,
    replacement_plm_id: str | None = None,
) -> Result:
    target = ctx.physical(plm_id)
    replacement = ctx.physical(replacement_plm_id).plm_id if replacement_plm_id else None
    if new_material is not None and ctx.enrichment.material_price(new_material) is None:
        known = [p.material for p in ctx.enrichment.material_prices()]
        raise ToolInputError(f"Unknown or unpriced material {new_material!r}. Priced materials: {known}")
    try:
        result = ctx.impact.assess(target.plm_id, new_mass_kg, new_material, replacement)
    except ValueError as exc:
        raise ToolInputError(str(exc)) from None

    def suppliers(rows: Iterable[Any]) -> list[Result]:
        return [
            {
                "part": ctx.plm.product(r.plm_id).name,
                "supplier": ctx.enrichment.supplier(r.supplier_id).name,
                "make_or_buy": r.make_or_buy,
                "lead_time_days": r.lead_time_days,
            }
            for r in rows
        ]

    return {
        "component": ctx.summary(target),
        "replacement": ctx.summary(result.replacement) if result.replacement else None,
        "origin": ENRICHMENT_ORIGIN,
        "caveat": COST_CAVEAT + " No purchase, tooling or requalification cost.",
        "units_in_motor": result.quantity_in_root,
        "unit_mass_kg": {"current": result.current_unit_mass_kg, "proposed": result.proposed_unit_mass_kg},
        "material": {"current": target.material, "proposed": result.proposed_material},
        "price_volatility": {"current": result.current_volatility, "proposed": result.proposed_volatility},
        "unit_material_cost_eur": {
            "current": round(result.current_unit_cost_eur, 2) if result.current_unit_cost_eur is not None else None,
            "proposed": round(result.proposed_unit_cost_eur, 2) if result.proposed_unit_cost_eur is not None else None,
        },
        "motor_mass_change_kg": round(result.root_mass_delta_kg, 3),
        "motor_material_cost_change_eur": (
            round(result.root_cost_delta_eur, 2) if result.root_cost_delta_eur is not None else None
        ),
        "impacted_assemblies": [
            {
                "plm_id": a.plm_id,
                "name": a.name,
                "kind": a.item_kind,
                "units_of_component": a.units,
                "projected_mass_kg": round(a.projected_mass_kg, 3),
                "projected_material_cost_eur": (
                    round(a.projected_cost_eur, 2) if a.projected_cost_eur is not None else None
                ),
            }
            for a in result.impacted
            if a.item_kind != "wrapper"
        ],
        "current_suppliers": suppliers(result.current_suppliers),
        "proposed_suppliers": suppliers(result.proposed_suppliers),
        "risk": {
            "current": result.current_risk.level if result.current_risk else None,
            "proposed": result.proposed_risk.level if result.proposed_risk else None,
        },
        "evidence": ctx.cite(
            f"Impact of changing {target.name}", result.sources, target.plm_id, COST_CAVEAT + " " + RISK_CAVEAT
        ),
    }


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
        "Full card of one product: revision, maturity, material, mass, total quantity and mass in the motor, "
        "share of the motor mass, assemblies it belongs to, 3D representation, number of suppliers.",
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
        "Material cost in EUR of one unit of a product (mass x material price, rolled up through the BOM), "
        "of all its units in the motor, and its share of the motor's material cost.",
        _params({"plm_id": _PLM_ID}, ("plm_id",)),
        get_unit_cost,
    ),
    Tool(
        "compare_products",
        f"Side-by-side summary of up to {MAX_COMPARED} products in one call: mass, material, assemblies, quantity "
        "in the motor, material cost and suppliers with lead times, plus their combined totals (units, mass, cost). "
        "Use it for homonyms, for any question about several products, and whenever a sum over products is needed.",
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
        "count = number of physical units (a screw used 14 times counts 14); distinct_part_count gives the number "
        "of different parts. For 'how many parts', report both. "
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
    Tool(
        "get_risk_ranking",
        "Official criticality ranking of the physical parts (probability x impact, level high/medium/low) "
        "with the main drivers of each score. view='price' ranks by price risk instead (material volatility x cost "
        "share). Criticality and price risk are scores out of 100, not percentages: write them as 63.7/100. "
        "Use it for any question on critical parts, risks or priorities.",
        _params(
            {
                "view": {"type": "string", "enum": ["supply", "price"], "description": "Default supply"},
                "level": {"type": "string", "enum": ["high", "medium", "low"], "description": "Optional filter"},
                "limit": _LIMIT,
            }
        ),
        get_risk_ranking,
    ),
    Tool(
        "get_part_risk",
        "Why a part is (or is not) critical: every risk criterion with its value, score and explanation. "
        "For an assembly: risk levels of the parts below it and the most critical ones. Criticality and price risk "
        "are scores out of 100, criteria scores out of 1; none of them is a percentage.",
        _params({"plm_id": _PLM_ID}, ("plm_id",)),
        get_part_risk,
    ),
    Tool(
        "get_risk_exposure",
        "Dependency exposure. by='supplier': share of the material cost that stops if each supplier fails and its "
        "single-sourced parts. by='material': parts, units, mass and cost shares, suppliers and price volatility "
        "per material.",
        _params(
            {
                "by": {"type": "string", "enum": ["supplier", "material"]},
                "top": {"type": "integer", "description": f"Number of groups returned (default {DEFAULT_LIMIT})"},
            },
            ("by",),
        ),
        get_risk_exposure,
    ),
    Tool(
        "assess_change",
        "Impact of changing a component: either a new unit mass and/or material (new_mass_kg, new_material), or its "
        "replacement by another existing part (replacement_plm_id). Returns impacted assemblies with projected mass "
        "and material cost, the motor mass and cost change, suppliers before and after, price volatility and risk.",
        _params(
            {
                "plm_id": _PLM_ID,
                "new_mass_kg": {"type": "number", "description": "Proposed unit mass in kg"},
                "new_material": {"type": "string", "description": "Proposed material, exact name of a priced one"},
                "replacement_plm_id": {"type": "string", "description": "plm_id of the replacing part"},
            },
            ("plm_id",),
        ),
        assess_change,
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
