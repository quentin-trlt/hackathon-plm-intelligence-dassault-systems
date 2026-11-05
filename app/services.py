"""Data access shared by every view. Everything goes through `plm.parser.load()` and the analysis services.

The dataset is chosen per browser session: the reference extract of the repository by default, or an extract
uploaded from the sidebar (app/dataset.py). Services are cached per dataset.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import streamlit as st

from plm.analysis.cost import MaterialCostService
from plm.analysis.impact import ChangeImpactService
from plm.analysis.risk import Criterion, PartRisk, RiskService
from plm.model import Product
from plm.parser import CsvEnrichmentRepository, PlmData, XmlPlmRepository, load

REPO_ROOT = Path(__file__).resolve().parents[1]
XML_PATH = REPO_ROOT / "data" / "raw" / "XPDMXML_ECAR_PLM.xml"
ENRICHMENT_DIR = REPO_ROOT / "data" / "enrichment"
NON_PLM = "non-PLM source"
DATASET_KEY = "dataset"
MAX_CACHED_DATASETS = 8


@dataclass(frozen=True)
class Dataset:
    xml_path: Path
    enrichment_dir: Path
    reference: bool = False

    @property
    def name(self) -> str:
        return self.xml_path.name


REFERENCE = Dataset(XML_PATH, ENRICHMENT_DIR, reference=True)


@dataclass(frozen=True)
class Services:
    dataset: Dataset
    data: PlmData
    cost: MaterialCostService
    risk: RiskService
    impact: ChangeImpactService

    @property
    def plm(self) -> XmlPlmRepository:
        return self.data.plm

    @property
    def enrichment(self) -> CsvEnrichmentRepository:
        return self.data.enrichment

    @property
    def has_prices(self) -> bool:
        return self.enrichment.has_prices

    @property
    def has_sourcing(self) -> bool:
        return self.enrichment.has_sourcing


def active_dataset() -> Dataset:
    return st.session_state.get(DATASET_KEY, REFERENCE)


@st.cache_resource(max_entries=MAX_CACHED_DATASETS, show_spinner="Loading the PLM data...")
def _build(xml_path: str, enrichment_dir: str, reference: bool) -> Services:
    dataset = Dataset(Path(xml_path), Path(enrichment_dir), reference)
    data = load(dataset.xml_path, dataset.enrichment_dir)
    cost = MaterialCostService(data.plm, data.enrichment)
    risk = RiskService(data.plm, data.enrichment, cost)
    impact = ChangeImpactService(data.plm, data.enrichment, cost, risk)
    return Services(dataset=dataset, data=data, cost=cost, risk=risk, impact=impact)


def build_services(dataset: Dataset) -> Services:
    return _build(str(dataset.xml_path), str(dataset.enrichment_dir), dataset.reference)


def get_services() -> Services:
    """Services of the dataset active in this browser session."""
    return build_services(active_dataset())


def label(product: Product) -> str:
    return f"{product.name} ({product.plm_id.rsplit('-', 1)[-1]})"


def part_risk(s: Services, plm_id: str) -> PartRisk | None:
    """Risk of a scored leaf part, None for assemblies, wrappers and the stator meshing artefacts."""
    if plm_id in s.risk.excluded or not s.plm.product(plm_id).is_leaf:
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
