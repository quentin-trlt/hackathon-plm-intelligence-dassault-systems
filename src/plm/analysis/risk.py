"""Part criticality: a probability x impact risk score for every leaf part.

Rules, thresholds and weights are documented in docs/risk_rules.md. Every
criterion is explainable on its own (raw value, 0-1 score, sentence) and keeps
the sources it was computed from, so the UI can show *why* a part is critical.

Probability: how likely a disruption is.
    SUPPLY         number of sources and make/buy           (enrichment)
    CONCENTRATION  other parts single-sourced on the same supplier (enrichment)
    MATURITY       maturity state and revision index        (PLM XML)
Impact: how much a disruption would hurt.
    COST_SHARE     share of the root material cost          (PLM XML + prices)
    LEAD_TIME      shortest lead time to re-supply           (enrichment)
    REUSE          number of places the part is used         (PLM XML)

A criterion without data (e.g. no lead time) is left out and its axis is
re-weighted over the remaining criteria; it is never scored as 0.

Price exposure is scored apart from supply disruption, because it is a different
risk (the part is delivered, but costs more):
    PRICE_VOLATILITY  volatility class of the material      (material_prices.csv)
    price_risk = 100 x volatility score x COST_SHARE score
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from plm.contract import CostService, EnrichmentRepository, PlmRepository
from plm.model import PartSourcing, SourceRef

Axis = Literal["probability", "impact", "price"]
Level = Literal["low", "medium", "high"]

# Single stator tooth and single stator coil, modelled for meshing only
# (description: "This is only a single Tooth -> required for meshing").
# They are not physical parts and would duplicate the full stator core and winding.
MESHING_ARTEFACTS = frozenset({"prd-12535825-00000118", "prd-12535825-00000119"})


@dataclass(frozen=True)
class RiskConfig:
    """Thresholds and weights. Change them here, not in the code below."""

    weights: dict[str, float] = field(
        default_factory=lambda: {
            "SUPPLY": 0.5,
            "CONCENTRATION": 0.3,
            "MATURITY": 0.2,
            "COST_SHARE": 0.5,
            "LEAD_TIME": 0.35,
            "REUSE": 0.15,
        }
    )
    lead_time_low_days: int = 30  # at or below: score 0
    lead_time_high_days: int = 120  # at or above: score 1
    cost_share_full: float = 0.10  # a part worth 10 % of the material cost scores 1
    concentration_full: int = 6  # 6 parts single-sourced on one supplier scores 1
    released_states: frozenset[str] = frozenset({"RELEASED"})
    frozen_states: frozenset[str] = frozenset({"FROZEN"})
    band_low: float = 0.33  # axis value below: low
    band_high: float = 0.66  # axis value at or above: high
    volatility_scores: dict[str, float] = field(default_factory=lambda: {"low": 0.2, "medium": 0.5, "high": 1.0})
    price_risk_high: float = 50.0  # price_risk at or above: high
    price_risk_medium: float = 20.0  # price_risk at or above: medium


@dataclass(frozen=True)
class Criterion:
    code: str
    axis: Axis
    weight: float
    score: float  # 0 (no risk) .. 1 (maximum risk)
    value: str  # raw value shown to the user, e.g. "112 days"
    explanation: str
    sources: tuple[SourceRef, ...]


@dataclass(frozen=True)
class PartRisk:
    plm_id: str
    name: str
    probability: float  # 0..1
    impact: float  # 0..1
    criticality: float  # 0..100 = 100 x probability x impact, used for ranking
    level: Level
    criteria: tuple[Criterion, ...]
    price_risk: float | None = None  # 0..100, None when volatility or cost is unknown
    price_level: Level | None = None

    @property
    def sources(self) -> tuple[SourceRef, ...]:
        return _merge(*(c.sources for c in self.criteria))

    def criterion(self, code: str) -> Criterion | None:
        return next((c for c in self.criteria if c.code == code), None)


@dataclass(frozen=True)
class AssemblyRisk:
    plm_id: str
    name: str
    worst: PartRisk | None
    parts: tuple[PartRisk, ...]  # sorted by criticality, highest first
    count_by_level: dict[str, int]


@dataclass(frozen=True)
class SupplierExposure:
    supplier_id: str
    name: str
    country: str
    parts: tuple[str, ...]  # plm_ids delivered by this supplier
    single_sourced_parts: tuple[str, ...]  # plm_ids that only this supplier delivers
    single_sourced_cost_share: float  # share of root material cost that stops if it fails
    sources: tuple[SourceRef, ...]


@dataclass(frozen=True)
class MaterialExposure:
    material: str
    parts: tuple[str, ...]  # plm_ids of the leaf parts made of this material
    units: int  # number of physical units in the root product
    mass_kg: float  # total mass in the root product
    mass_share: float  # share of the root mass
    cost_eur: float | None  # total material cost in the root product
    cost_share: float | None  # share of the root material cost
    suppliers: tuple[str, ...]  # supplier_ids delivering at least one of these parts
    single_sourced_parts: tuple[str, ...]
    price_volatility: str | None
    sources: tuple[SourceRef, ...]


def _merge(*groups: tuple[SourceRef, ...]) -> tuple[SourceRef, ...]:
    seen: dict[SourceRef, None] = {}
    for group in groups:
        for ref in group:
            seen.setdefault(ref, None)
    return tuple(seen)


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


class RiskService:
    def __init__(
        self,
        plm: PlmRepository,
        enrichment: EnrichmentRepository,
        cost: CostService | None = None,
        excluded_plm_ids: frozenset[str] = MESHING_ARTEFACTS,
        config: RiskConfig | None = None,
    ) -> None:
        self._plm = plm
        self._enrichment = enrichment
        self._cost = cost
        self._excluded = excluded_plm_ids
        self._cfg = config or RiskConfig()
        self._leaves = [p for p in plm.leaves() if p.plm_id not in self._excluded]
        self._sourcing: dict[str, list[PartSourcing]] = {p.plm_id: enrichment.sourcing(p.plm_id) for p in self._leaves}
        self._single_by_supplier: dict[str, list[str]] = defaultdict(list)
        for plm_id, rows in self._sourcing.items():
            if len(rows) == 1:
                self._single_by_supplier[rows[0].supplier_id].append(plm_id)
        # Reference cost = sum over the scored leaves (excluded parts left out), so shares add up to 100 %.
        self._root_cost: float | None = None
        if cost is not None:
            unit_costs = [cost.unit_cost(p.plm_id) for p in self._leaves]
            if all(u is not None for u in unit_costs):
                self._root_cost = sum(
                    u.value * plm.total_quantity(p.plm_id).value for u, p in zip(unit_costs, self._leaves)
                )
        self._cache: dict[str, PartRisk] = {}

    # ---- public API -------------------------------------------------------

    def part_risk(self, plm_id: str) -> PartRisk:
        """Risk of one leaf part. Raises ValueError for an assembly or an excluded part."""
        if plm_id not in self._cache:
            product = self._plm.product(plm_id)
            if not product.is_leaf or plm_id in self._excluded:
                raise ValueError(f"{plm_id} is not a scored leaf part")
            self._cache[plm_id] = self._score(plm_id)
        return self._cache[plm_id]

    def ranking(self) -> list[PartRisk]:
        """All scored leaf parts, most critical first."""
        return sorted((self.part_risk(p.plm_id) for p in self._leaves), key=lambda r: -r.criticality)

    def assembly_risk(self, plm_id: str) -> AssemblyRisk:
        """Risk of the parts below an assembly (or of the part itself for a leaf)."""
        product = self._plm.product(plm_id)
        below: set[str] = set()
        stack = [plm_id]
        while stack:
            current = stack.pop()
            if current in self._excluded:
                continue
            if self._plm.product(current).is_leaf:
                below.add(current)
            stack.extend(line.child_plm_id for line in self._plm.children(current))
        parts = tuple(sorted((self.part_risk(p) for p in below), key=lambda r: -r.criticality))
        counts = {"high": 0, "medium": 0, "low": 0}
        for r in parts:
            counts[r.level] += 1
        return AssemblyRisk(plm_id, product.name, parts[0] if parts else None, parts, counts)

    def supplier_exposure(self) -> list[SupplierExposure]:
        """Per supplier: parts delivered, parts that would stop if it failed, and their cost share."""
        delivered: dict[str, list[str]] = defaultdict(list)
        refs: dict[str, list[SourceRef]] = defaultdict(list)
        for plm_id, rows in self._sourcing.items():
            for row in rows:
                delivered[row.supplier_id].append(plm_id)
                refs[row.supplier_id].append(row.source)
        result = []
        for supplier in self._enrichment.suppliers():
            single = tuple(self._single_by_supplier.get(supplier.supplier_id, []))
            share = sum(self._cost_share(p)[0] or 0.0 for p in single)
            result.append(
                SupplierExposure(
                    supplier.supplier_id,
                    supplier.name,
                    supplier.country,
                    tuple(delivered.get(supplier.supplier_id, [])),
                    single,
                    share,
                    _merge((supplier.source,), tuple(refs.get(supplier.supplier_id, []))),
                )
            )
        return sorted(result, key=lambda s: (-s.single_sourced_cost_share, -len(s.single_sourced_parts)))

    def material_exposure(self) -> list[MaterialExposure]:
        """Per material: parts, mass and cost shares, suppliers. Highest cost share first."""
        root_mass = sum(p.mass_kg * self._plm.total_quantity(p.plm_id).value for p in self._leaves)
        groups: dict[str, list[str]] = defaultdict(list)
        for p in self._leaves:
            if p.material:
                groups[p.material].append(p.plm_id)
        result = []
        for material, plm_ids in groups.items():
            units, mass, refs = 0, 0.0, []
            for plm_id in plm_ids:
                quantity = self._plm.total_quantity(plm_id)
                units += quantity.value
                mass += self._plm.product(plm_id).mass_kg * quantity.value
                refs.append(self._plm.product(plm_id).source)
            shares = [self._cost_share(p) for p in plm_ids]
            cost_share = sum(s for s, _ in shares) if all(s is not None for s, _ in shares) else None
            price = self._enrichment.material_price(material)
            suppliers = sorted({r.supplier_id for p in plm_ids for r in self._sourcing[p]})
            result.append(
                MaterialExposure(
                    material=material,
                    parts=tuple(plm_ids),
                    units=units,
                    mass_kg=mass,
                    mass_share=mass / root_mass if root_mass else 0.0,
                    cost_eur=cost_share * self._root_cost if cost_share is not None and self._root_cost else None,
                    cost_share=cost_share,
                    suppliers=tuple(suppliers),
                    single_sourced_parts=tuple(p for p in plm_ids if len(self._sourcing[p]) == 1),
                    price_volatility=price.price_volatility if price else None,
                    sources=_merge(tuple(refs), (price.source,) if price else (), *(r for _, r in shares)),
                )
            )
        return sorted(result, key=lambda m: -(m.cost_share or 0.0))

    def price_ranking(self) -> list[PartRisk]:
        """Parts with a known price risk, highest first."""
        scored = [r for r in self.ranking() if r.price_risk is not None]
        return sorted(scored, key=lambda r: -r.price_risk)

    # ---- scoring ----------------------------------------------------------

    def _score(self, plm_id: str) -> PartRisk:
        criteria = [
            c
            for c in (
                self._supply(plm_id),
                self._concentration(plm_id),
                self._maturity(plm_id),
                self._cost_share_criterion(plm_id),
                self._lead_time(plm_id),
                self._reuse(plm_id),
            )
            if c is not None
        ]
        probability = self._axis(criteria, "probability")
        impact = self._axis(criteria, "impact")
        price_risk, price_level = None, None
        volatility = self._volatility(plm_id)
        cost = next((c for c in criteria if c.code == "COST_SHARE"), None)
        if volatility is not None:
            criteria.append(volatility)
            if cost is not None:
                price_risk = round(100 * volatility.score * cost.score, 1)
                price_level = (
                    "high"
                    if price_risk >= self._cfg.price_risk_high
                    else "medium"
                    if price_risk >= self._cfg.price_risk_medium
                    else "low"
                )
        return PartRisk(
            plm_id=plm_id,
            name=self._plm.product(plm_id).name,
            probability=probability,
            impact=impact,
            criticality=round(100 * probability * impact, 1),
            level=self._level(probability, impact),
            criteria=tuple(criteria),
            price_risk=price_risk,
            price_level=price_level,
        )

    @staticmethod
    def _axis(criteria: list[Criterion], axis: Axis) -> float:
        on_axis = [c for c in criteria if c.axis == axis]
        total = sum(c.weight for c in on_axis)
        return sum(c.weight * c.score for c in on_axis) / total if total else 0.0

    def _band(self, x: float) -> int:
        return 0 if x < self._cfg.band_low else (2 if x >= self._cfg.band_high else 1)

    def _level(self, probability: float, impact: float) -> Level:
        """3x3 heat map: high if one axis is high and the other at least medium."""
        p, i = self._band(probability), self._band(impact)
        if (p == 2 and i >= 1) or (i == 2 and p >= 1):
            return "high"
        if p + i >= 2:
            return "medium"
        return "low"

    def _w(self, code: str) -> float:
        return self._cfg.weights[code]

    def _supply(self, plm_id: str) -> Criterion:
        rows = self._sourcing[plm_id]
        refs = tuple(r.source for r in rows)
        if not rows:
            return Criterion(
                "SUPPLY",
                "probability",
                self._w("SUPPLY"),
                1.0,
                "no supplier",
                "No supplier is recorded for this part: treated as maximum risk.",
                refs,
            )
        if len(rows) == 1:
            row = rows[0]
            name = self._enrichment.supplier(row.supplier_id).name
            if row.make_or_buy == "make":
                return Criterion(
                    "SUPPLY",
                    "probability",
                    self._w("SUPPLY"),
                    0.5,
                    "single source (in-house)",
                    f"Only made in-house ({name}): no alternative, but the risk is under our control.",
                    refs,
                )
            return Criterion(
                "SUPPLY",
                "probability",
                self._w("SUPPLY"),
                1.0,
                "single source (bought)",
                f"Only one external supplier ({name}): any failure stops the part.",
                refs,
            )
        score = 0.25 if len(rows) == 2 else 0.0
        return Criterion(
            "SUPPLY",
            "probability",
            self._w("SUPPLY"),
            score,
            f"{len(rows)} sources",
            f"{len(rows)} sources can deliver the part.",
            refs,
        )

    def _concentration(self, plm_id: str) -> Criterion:
        rows = self._sourcing[plm_id]
        if len(rows) != 1:
            return Criterion(
                "CONCENTRATION",
                "probability",
                self._w("CONCENTRATION"),
                0.0,
                "n/a (multi-source)",
                "Several sources: the failure of one supplier does not stop the part.",
                tuple(r.source for r in rows),
            )
        supplier_id = rows[0].supplier_id
        same = self._single_by_supplier[supplier_id]
        n = len(same)
        score = _clamp((n - 1) / (self._cfg.concentration_full - 1))
        name = self._enrichment.supplier(supplier_id).name
        refs = tuple(r.source for p in same for r in self._sourcing[p])
        return Criterion(
            "CONCENTRATION",
            "probability",
            self._w("CONCENTRATION"),
            score,
            f"{n} single-sourced parts on {name}",
            f"{name} is the only source of {n} part(s): one failure stops all of them.",
            refs,
        )

    def _maturity(self, plm_id: str) -> Criterion:
        p = self._plm.product(plm_id)
        if p.maturity in self._cfg.released_states:
            state, state_text = 0.0, f"released ({p.maturity})"
        elif p.maturity in self._cfg.frozen_states:
            state, state_text = 0.25, f"frozen ({p.maturity})"
        else:
            state, state_text = 0.5, f"not frozen ({p.maturity})"
        revision = _clamp((p.revision_index - 1) / 3)
        score = state + 0.5 * revision
        return Criterion(
            "MATURITY",
            "probability",
            self._w("MATURITY"),
            score,
            f"{p.maturity}, rev. {p.revision}",
            f"Design {state_text}, revision {p.revision} (index {p.revision_index}): "
            "the more revisions, the less stable the design.",
            (p.source,),
        )

    def _cost_share(self, plm_id: str) -> tuple[float | None, tuple[SourceRef, ...]]:
        if self._cost is None or not self._root_cost:
            return None, ()
        unit = self._cost.unit_cost(plm_id)
        if unit is None:
            return None, ()
        quantity = self._plm.total_quantity(plm_id)
        return unit.value * quantity.value / self._root_cost, _merge(unit.sources, quantity.sources)

    def _cost_share_criterion(self, plm_id: str) -> Criterion | None:
        share, refs = self._cost_share(plm_id)
        if share is None:
            return None
        score = _clamp(share / self._cfg.cost_share_full)
        return Criterion(
            "COST_SHARE",
            "impact",
            self._w("COST_SHARE"),
            score,
            f"{share:.1%} of material cost",
            f"The part (all units) is {share:.1%} of the e-drive material cost.",
            refs,
        )

    def _lead_time(self, plm_id: str) -> Criterion | None:
        rows = [r for r in self._sourcing[plm_id] if r.lead_time_days is not None]
        if not rows:
            return None
        best = min(rows, key=lambda r: r.lead_time_days)
        lo, hi = self._cfg.lead_time_low_days, self._cfg.lead_time_high_days
        score = _clamp((best.lead_time_days - lo) / (hi - lo))
        return Criterion(
            "LEAD_TIME",
            "impact",
            self._w("LEAD_TIME"),
            score,
            f"{best.lead_time_days} days",
            f"Fastest re-supply takes {best.lead_time_days} days.",
            tuple(r.source for r in rows),
        )

    def _reuse(self, plm_id: str) -> Criterion:
        occurrences = self._plm.where_used(plm_id)
        n = len(occurrences)
        score = _clamp((n - 1) / 2)
        return Criterion(
            "REUSE",
            "impact",
            self._w("REUSE"),
            score,
            f"used in {n} place(s)",
            f"The part appears in {n} place(s) of the product structure: a problem spreads to every one of them.",
            _merge(*(o.sources for o in occurrences)),
        )

    def _volatility(self, plm_id: str) -> Criterion | None:
        material = self._plm.product(plm_id).material
        price = self._enrichment.material_price(material) if material else None
        if price is None or price.price_volatility is None:
            return None
        score = self._cfg.volatility_scores[price.price_volatility]
        return Criterion(
            "PRICE_VOLATILITY",
            "price",
            0.0,
            score,
            f"{price.price_volatility} ({material})",
            f"The price of {material} has a {price.price_volatility} volatility: "
            "a rise directly increases the cost of this part.",
            (price.source,),
        )
