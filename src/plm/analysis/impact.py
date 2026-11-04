"""Change impact analysis: replace a component and trace what it affects.

A proposal is either a change of unit mass and/or material of the component, or
its replacement by another existing product of the extract. The service returns
the impacted assemblies (with the units of the component they contain and their
projected mass and material cost), the suppliers concerned before and after,
the price exposure of the proposed material and, for a replacement by a scored
part, its risk. Every figure keeps its sources.

Limits: costs are material costs only (mass x price per kg). The data holds no
purchase price, tooling or requalification cost.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from plm.analysis.risk import PartRisk, RiskService
from plm.contract import CostService, EnrichmentRepository, PlmRepository
from plm.model import PartSourcing, Product, SourceRef


def _merge(*groups: tuple[SourceRef, ...]) -> tuple[SourceRef, ...]:
    seen: dict[SourceRef, None] = {}
    for group in groups:
        for ref in group:
            seen.setdefault(ref, None)
    return tuple(seen)


@dataclass(frozen=True)
class ImpactedAssembly:
    plm_id: str
    name: str
    item_kind: str
    depth: int  # distance from the root (root = 0)
    units: int  # units of the changed component inside one unit of this assembly
    declared_mass_kg: float
    projected_mass_kg: float
    current_cost_eur: float | None
    projected_cost_eur: float | None
    sources: tuple[SourceRef, ...]


@dataclass(frozen=True)
class ChangeImpact:
    target: Product
    replacement: Product | None
    quantity_in_root: int
    current_unit_mass_kg: float
    proposed_unit_mass_kg: float
    proposed_material: str | None
    current_unit_cost_eur: float | None
    proposed_unit_cost_eur: float | None
    root_mass_delta_kg: float
    root_cost_delta_eur: float | None  # None when a cost is unknown
    impacted: tuple[ImpactedAssembly, ...]  # from the root down
    current_suppliers: tuple[PartSourcing, ...]
    proposed_suppliers: tuple[PartSourcing, ...]  # replacement: its suppliers; material change: unchanged
    current_volatility: str | None
    proposed_volatility: str | None
    current_risk: PartRisk | None
    proposed_risk: PartRisk | None
    sources: tuple[SourceRef, ...]


class ChangeImpactService:
    def __init__(
        self,
        plm: PlmRepository,
        enrichment: EnrichmentRepository,
        cost: CostService,
        risk: RiskService | None = None,
    ) -> None:
        self._plm = plm
        self._enrichment = enrichment
        self._cost = cost
        self._risk = risk

    # ---- public API -------------------------------------------------------

    def units_by_ancestor(self, plm_id: str) -> dict[str, int]:
        """For every assembly above the product: units of the product in one unit of that assembly."""
        by_path = {occ.path: occ for occ in self._plm.occurrences()}
        per_ancestor: dict[str, dict[tuple[str, ...], int]] = defaultdict(lambda: defaultdict(int))
        for occ in self._plm.where_used(plm_id):
            for depth, ancestor in enumerate(occ.path[:-1]):
                ancestor_path = occ.path[: depth + 1]
                ratio = occ.cumulative_quantity // by_path[ancestor_path].cumulative_quantity
                per_ancestor[ancestor][ancestor_path] += ratio
        # An assembly instantiated in several places holds the same content everywhere: keep one path.
        return {ancestor: next(iter(paths.values())) for ancestor, paths in per_ancestor.items()}

    def replacement_candidates(self, plm_id: str) -> list[Product]:
        """Products that can replace the component: same kind of item (leaf or not), not itself or an ancestor."""
        target = self._plm.product(plm_id)
        ancestors = set(self.units_by_ancestor(plm_id))
        return [
            p
            for p in self._plm.products()
            if p.plm_id != plm_id and not p.is_root and p.plm_id not in ancestors and p.is_leaf == target.is_leaf
        ]

    def assess(
        self,
        plm_id: str,
        new_mass_kg: float | None = None,
        new_material: str | None = None,
        replacement_plm_id: str | None = None,
    ) -> ChangeImpact:
        """Impact of changing the mass/material of a component, or of replacing it by another product."""
        target = self._plm.product(plm_id)
        if target.is_root:
            raise ValueError("The root product cannot be replaced")
        replacement = None
        if replacement_plm_id is not None:
            if new_mass_kg is not None or new_material is not None:
                raise ValueError("Give either a replacement or a mass/material change, not both")
            replacement = self._plm.product(replacement_plm_id)
            if replacement.plm_id not in {p.plm_id for p in self.replacement_candidates(plm_id)}:
                raise ValueError(f"{replacement.name} cannot replace {target.name}")

        quantity = self._plm.total_quantity(plm_id)
        current_cost = self._cost.unit_cost(plm_id)
        refs: list[tuple[SourceRef, ...]] = [(target.source,), quantity.sources]
        if current_cost:
            refs.append(current_cost.sources)

        if replacement is not None:
            new_mass = replacement.mass_kg
            material = replacement.material
            new_cost_traced = self._cost.unit_cost(replacement.plm_id)
            new_cost = new_cost_traced.value if new_cost_traced else None
            refs.append((replacement.source,))
            if new_cost_traced:
                refs.append(new_cost_traced.sources)
        else:
            new_mass = target.mass_kg if new_mass_kg is None else new_mass_kg
            if new_mass < 0:
                raise ValueError("The proposed mass must be >= 0")
            material = new_material or target.material
            new_cost = self._material_cost(target, new_mass, material, refs)

        unit_mass_delta = new_mass - target.mass_kg
        unit_cost_delta = new_cost - current_cost.value if new_cost is not None and current_cost else None
        impacted = self._impacted(plm_id, unit_mass_delta, unit_cost_delta)

        current_suppliers = tuple(self._sourcing_below(plm_id))
        proposed_suppliers = tuple(self._sourcing_below(replacement.plm_id)) if replacement else current_suppliers
        refs += [tuple(s.source for s in current_suppliers), tuple(s.source for s in proposed_suppliers)]

        current_vol = self._volatility(target.material, refs)
        proposed_vol = self._volatility(material, refs)

        return ChangeImpact(
            target=target,
            replacement=replacement,
            quantity_in_root=quantity.value,
            current_unit_mass_kg=target.mass_kg,
            proposed_unit_mass_kg=new_mass,
            proposed_material=material,
            current_unit_cost_eur=current_cost.value if current_cost else None,
            proposed_unit_cost_eur=new_cost,
            root_mass_delta_kg=unit_mass_delta * quantity.value,
            root_cost_delta_eur=unit_cost_delta * quantity.value if unit_cost_delta is not None else None,
            impacted=impacted,
            current_suppliers=current_suppliers,
            proposed_suppliers=proposed_suppliers,
            current_volatility=current_vol,
            proposed_volatility=proposed_vol,
            current_risk=self._part_risk(plm_id),
            proposed_risk=self._part_risk(replacement.plm_id) if replacement else None,
            sources=_merge(*refs, *(a.sources for a in impacted)),
        )

    # ---- helpers ----------------------------------------------------------

    def _material_cost(
        self, target: Product, mass: float, material: str | None, refs: list[tuple[SourceRef, ...]]
    ) -> float | None:
        if not target.is_leaf:
            # An assembly's cost comes from its parts: only a mass change is meaningful, the cost is unchanged.
            unchanged = self._cost.unit_cost(target.plm_id)
            return unchanged.value if unchanged and material == target.material else None
        if material is None:
            return None
        price = self._enrichment.material_price(material)
        if price is None:
            return None
        refs.append((price.source,))
        return mass * price.price_eur_per_kg

    def _impacted(
        self, plm_id: str, unit_mass_delta: float, unit_cost_delta: float | None
    ) -> tuple[ImpactedAssembly, ...]:
        units = self.units_by_ancestor(plm_id)
        result: dict[str, ImpactedAssembly] = {}
        for occ in self._plm.occurrences():
            if occ.plm_id not in units or occ.plm_id in result:
                continue
            assembly = self._plm.product(occ.plm_id)
            n = units[occ.plm_id]
            cost = self._cost.unit_cost(assembly.plm_id)
            result[occ.plm_id] = ImpactedAssembly(
                plm_id=assembly.plm_id,
                name=assembly.name,
                item_kind=assembly.item_kind,
                depth=occ.depth,
                units=n,
                declared_mass_kg=assembly.mass_kg,
                projected_mass_kg=assembly.mass_kg + unit_mass_delta * n,
                current_cost_eur=cost.value if cost else None,
                projected_cost_eur=cost.value + unit_cost_delta * n if cost and unit_cost_delta is not None else None,
                sources=_merge((assembly.source,), occ.sources, cost.sources if cost else ()),
            )
        return tuple(sorted(result.values(), key=lambda a: a.depth))

    def _sourcing_below(self, plm_id: str) -> list[PartSourcing]:
        rows: list[PartSourcing] = []
        stack, seen = [plm_id], set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            if self._plm.product(current).is_leaf:
                rows.extend(self._enrichment.sourcing(current))
            stack.extend(line.child_plm_id for line in self._plm.children(current))
        return rows

    def _volatility(self, material: str | None, refs: list[tuple[SourceRef, ...]]) -> str | None:
        price = self._enrichment.material_price(material) if material else None
        if price is None:
            return None
        refs.append((price.source,))
        return price.price_volatility

    def _part_risk(self, plm_id: str) -> PartRisk | None:
        if self._risk is None:
            return None
        try:
            return self._risk.part_risk(plm_id)
        except ValueError:
            return None
