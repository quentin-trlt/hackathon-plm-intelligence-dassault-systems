"""Material cost of products, as defined in docs/data_contract.md#cost-rule.

    leaf cost     = mass_kg x price_eur_per_kg(material)
    assembly cost = sum over BOM lines of (quantity x child cost)

Costs are only computed on leaves, so an EI wrapper and its 3D leaf are never
counted twice. A missing material or price is never replaced by 0: the cost of
the leaf and of every assembly above it is None, and the leaf is listed by
`missing_prices()`.
"""

from __future__ import annotations

from plm.contract import EnrichmentRepository, PlmRepository
from plm.model import Product, SourceRef, Traced


def _merge(*groups: tuple[SourceRef, ...]) -> tuple[SourceRef, ...]:
    """Concatenate source tuples, dropping duplicates while keeping order."""
    seen: dict[SourceRef, None] = {}
    for group in groups:
        for ref in group:
            seen.setdefault(ref, None)
    return tuple(seen)


class MaterialCostService:
    """Implements `plm.contract.CostService`."""

    def __init__(
        self,
        plm: PlmRepository,
        enrichment: EnrichmentRepository,
        excluded_plm_ids: frozenset[str] = frozenset(),
    ) -> None:
        # `excluded_plm_ids`: products left out of every roll-up (e.g. the stator
        # meshing parts, open question 4 of docs/xml_mapping.md). Empty by default,
        # which follows the data contract as written.
        self._plm = plm
        self._enrichment = enrichment
        self._excluded = excluded_plm_ids
        self._cache: dict[str, Traced[float] | None] = {}

    def unit_cost(self, plm_id: str) -> Traced[float] | None:
        """Cost of one unit of the product, with every source it was computed from."""
        if plm_id not in self._cache:
            self._cache[plm_id] = self._compute(self._plm.product(plm_id))
        return self._cache[plm_id]

    def missing_prices(self) -> list[Product]:
        """Leaves whose cost cannot be computed (no material, or material without price)."""
        return [
            leaf
            for leaf in self._plm.leaves()
            if leaf.plm_id not in self._excluded and self._leaf_cost(leaf) is None
        ]

    def _compute(self, product: Product) -> Traced[float] | None:
        if product.is_leaf:
            return self._leaf_cost(product)
        total = 0.0
        sources: tuple[SourceRef, ...] = ()
        for line in self._plm.children(product.plm_id):
            if line.child_plm_id in self._excluded:
                continue
            child = self.unit_cost(line.child_plm_id)
            if child is None:
                return None
            total += line.quantity * child.value
            sources = _merge(sources, line.sources, child.sources)
        return Traced(value=total, sources=_merge((product.source,), sources))

    def _leaf_cost(self, leaf: Product) -> Traced[float] | None:
        if leaf.material is None:
            return None
        price = self._enrichment.material_price(leaf.material)
        if price is None:
            return None
        return Traced(
            value=leaf.mass_kg * price.price_eur_per_kg,
            sources=(leaf.source, price.source),
        )
