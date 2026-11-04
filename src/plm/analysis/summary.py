"""Roll-up of mass, material cost and risk per major subassembly (the children of the root)."""

from __future__ import annotations

from dataclasses import dataclass

from plm.analysis.risk import PartRisk, RiskService
from plm.contract import CostService, PlmRepository
from plm.model import SourceRef


@dataclass(frozen=True)
class SubassemblySummary:
    plm_id: str
    name: str
    mass_kg: float  # declared mass of one unit of the subassembly
    mass_share: float  # share of the root mass
    cost_eur: float | None  # material cost of one unit
    cost_share: float | None  # share of the root material cost
    parts: int  # scored leaf parts below
    count_by_level: dict[str, int]  # supply risk levels of those parts
    high_price_risk_parts: int
    worst: PartRisk | None
    sources: tuple[SourceRef, ...]


def subassembly_summary(plm: PlmRepository, cost: CostService, risk: RiskService) -> list[SubassemblySummary]:
    """One entry per direct child of the root, heaviest cost first."""
    root = plm.root()
    root_cost = cost.unit_cost(root.plm_id)
    result = []
    for line in plm.children(root.plm_id):
        product = plm.product(line.child_plm_id)
        unit = cost.unit_cost(product.plm_id)
        assembly = risk.assembly_risk(product.plm_id)
        result.append(
            SubassemblySummary(
                plm_id=product.plm_id,
                name=product.name,
                mass_kg=product.mass_kg,
                mass_share=product.mass_kg * line.quantity / root.mass_kg if root.mass_kg else 0.0,
                cost_eur=unit.value if unit else None,
                cost_share=unit.value * line.quantity / root_cost.value if unit and root_cost else None,
                parts=len(assembly.parts),
                count_by_level=assembly.count_by_level,
                high_price_risk_parts=sum(1 for r in assembly.parts if r.price_level == "high"),
                worst=assembly.worst,
                sources=(product.source, *line.sources, *(unit.sources if unit else ())),
            )
        )
    return sorted(result, key=lambda s: -(s.cost_eur or 0.0))
