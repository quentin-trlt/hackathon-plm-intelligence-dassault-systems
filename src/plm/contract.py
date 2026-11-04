"""Interfaces every module codes against. `plm.parser` and the enrichment loader implement them."""

from __future__ import annotations

from typing import Protocol

from plm.model import (
    BomLine,
    ExportInfo,
    Instance,
    MaterialPrice,
    Occurrence,
    PartSourcing,
    Product,
    Representation,
    Supplier,
    Traced,
)


class UnknownProductError(KeyError):
    pass


class UnknownSupplierError(KeyError):
    pass


class PlmRepository(Protocol):
    """Read-only access to the OEM extract. Every product is addressed by its `plm_id`."""

    @property
    def export_info(self) -> ExportInfo: ...

    def root(self) -> Product: ...

    def product(self, plm_id: str) -> Product:
        """Raises UnknownProductError if absent."""
        ...

    def products(self) -> list[Product]: ...

    def leaves(self) -> list[Product]: ...

    def search(self, text: str) -> list[Product]:
        """Case-insensitive match on `name_normalized`, `plm_id` and `material`."""
        ...

    def instances(self, parent_plm_id: str, child_plm_id: str) -> list[Instance]: ...

    def children(self, plm_id: str) -> list[BomLine]:
        """Direct children, sorted by `order`."""
        ...

    def parents(self, plm_id: str) -> list[BomLine]:
        """Direct parents."""
        ...

    def occurrences(self) -> list[Occurrence]:
        """Whole expanded tree, root first, depth-first in `order`."""
        ...

    def where_used(self, plm_id: str) -> list[Occurrence]:
        """Every occurrence of the product in the expanded tree."""
        ...

    def total_quantity(self, plm_id: str) -> Traced[int]:
        """Number of units of the product in the root product."""
        ...

    def representation(self, plm_id: str) -> Representation | None: ...


class EnrichmentRepository(Protocol):
    """Non-PLM data from `data/enrichment/`. Joined to the PLM model on `plm_id` and on the exact `material` string."""

    def material_price(self, material: str) -> MaterialPrice | None: ...

    def suppliers(self) -> list[Supplier]: ...

    def supplier(self, supplier_id: str) -> Supplier:
        """Raises UnknownSupplierError if absent."""
        ...

    def sourcing(self, plm_id: str) -> list[PartSourcing]:
        """One entry per supplier able to deliver the part. Empty if unknown."""
        ...


class CostService(Protocol):
    """Leaf cost = mass_kg x price_eur_per_kg of its material. Assembly cost = sum of quantity x child cost."""

    def unit_cost(self, plm_id: str) -> Traced[float] | None:
        """None when a leaf below the product has no material or no material price."""
        ...

    def missing_prices(self) -> list[Product]:
        """Leaves whose cost cannot be computed."""
        ...
