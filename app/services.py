"""Data access shared by every view. Everything goes through `plm.parser.load()` and the analysis services."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import streamlit as st

from plm.agent.evidence import describe
from plm.analysis.cost import MaterialCostService
from plm.analysis.risk import MESHING_ARTEFACTS, Criterion, PartRisk, RiskService
from plm.model import Product, SourceRef
from plm.parser import CsvEnrichmentRepository, PlmData, XmlPlmRepository, load

REPO_ROOT = Path(__file__).resolve().parents[1]
XML_PATH = REPO_ROOT / "data" / "raw" / "XPDMXML_ECAR_PLM.xml"
ENRICHMENT_DIR = REPO_ROOT / "data" / "enrichment"
NON_PLM = "non-PLM source"


@dataclass(frozen=True)
class Services:
    data: PlmData
    cost: MaterialCostService
    risk: RiskService

    @property
    def plm(self) -> XmlPlmRepository:
        return self.data.plm

    @property
    def enrichment(self) -> CsvEnrichmentRepository:
        return self.data.enrichment


@st.cache_resource
def get_services() -> Services:
    data = load(XML_PATH, ENRICHMENT_DIR)
    cost = MaterialCostService(data.plm, data.enrichment)
    return Services(data=data, cost=cost, risk=RiskService(data.plm, data.enrichment, cost))


def label(product: Product) -> str:
    return f"{product.name} · {product.plm_id}"


def part_risk(s: Services, plm_id: str) -> PartRisk | None:
    """Risk of a scored leaf part, None for assemblies, wrappers and the stator meshing artefacts."""
    if plm_id in MESHING_ARTEFACTS or not s.plm.product(plm_id).is_leaf:
        return None
    return s.risk.part_risk(plm_id)


def leaves_below(s: Services, plm_id: str) -> list[str]:
    """Leaf parts under a product, the product itself for a leaf. Each plm_id once, in BOM order."""
    if s.plm.product(plm_id).is_leaf:
        return [plm_id]
    found: dict[str, None] = {}
    for line in s.plm.children(plm_id):
        for leaf in leaves_below(s, line.child_plm_id):
            found.setdefault(leaf, None)
    return list(found)


def leaf_frame(s: Services) -> pd.DataFrame:
    """One row per leaf part, with its quantity, mass and material cost in the whole motor."""
    rows = []
    for leaf in s.plm.leaves():
        quantity = s.plm.total_quantity(leaf.plm_id).value
        cost = s.cost.unit_cost(leaf.plm_id)
        risk = part_risk(s, leaf.plm_id)
        rows.append(
            {
                "Part": leaf.name,
                "PLM ID": leaf.plm_id,
                "Material": leaf.material or "Not set",
                "Rev": leaf.revision,
                "Maturity": leaf.maturity,
                "Unit mass (kg)": leaf.mass_kg,
                "Qty in motor": quantity,
                "Mass in motor (kg)": leaf.mass_kg * quantity,
                "Unit cost (EUR)": cost.value if cost else None,
                "Cost in motor (EUR)": cost.value * quantity if cost else None,
                "Suppliers": len(s.enrichment.sourcing(leaf.plm_id)),
                "Risk level": risk.level if risk else "not scored",
                "Criticality": risk.criticality if risk else None,
                "XML line": leaf.source.line,
            }
        )
    return pd.DataFrame(rows)


def sourcing_frame(s: Services, plm_ids: Iterable[str]) -> pd.DataFrame:
    rows = []
    for plm_id in plm_ids:
        sourcing = s.enrichment.sourcing(plm_id)
        for row in sourcing:
            supplier = s.enrichment.supplier(row.supplier_id)
            rows.append(
                {
                    "Part": s.plm.product(plm_id).name,
                    "Supplier": supplier.name,
                    "Country": supplier.country,
                    "Make/buy": row.make_or_buy,
                    "Lead time (days)": row.lead_time_days,
                    "Single source": len(sourcing) == 1,
                    "Source note": row.source_note,
                    "CSV line": row.source.line,
                }
            )
    return pd.DataFrame(rows)


def criteria_frame(criteria: Iterable[Criterion]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Criterion": c.code,
                "Axis": c.axis,
                "Value": c.value,
                "Score": c.score,
                "Explanation": c.explanation,
                "Data": NON_PLM if any(r.origin == "enrichment" for r in c.sources) else "PLM XML",
            }
            for c in criteria
        ]
    )


def show_sources(refs: Iterable[SourceRef], title: str = "Sources") -> None:
    unique = list(dict.fromkeys(refs))
    if not unique:
        return
    with st.expander(f"{title} ({len(unique)})"):
        st.markdown("\n".join(f"- {describe(ref)}" for ref in unique))
