"""Shared data model. Units: mass in kg, prices in EUR, lead times in days, translations in metres."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Generic, Literal, TypeVar

T = TypeVar("T")

Origin = Literal["plm_xml", "enrichment"]
ItemKind = Literal["assembly", "wrapper", "part"]
MakeOrBuy = Literal["make", "buy"]


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Where a value comes from: an XML element of the OEM extract, or a row of an enrichment CSV."""

    origin: Origin
    file: str
    line: int
    element: str | None = None
    xml_id: str | None = None
    xid: str | None = None


@dataclass(frozen=True, slots=True)
class Traced(Generic[T]):
    """A computed value together with every source it was derived from."""

    value: T
    sources: tuple[SourceRef, ...]


@dataclass(frozen=True, slots=True)
class ExportInfo:
    root_plm_id: str
    root_xid: str
    completion: str
    raw: dict[str, str]
    source: SourceRef


@dataclass(frozen=True, slots=True)
class Product:
    plm_id: str
    xid: str
    name: str
    name_normalized: str
    description: str | None
    revision: str
    revision_index: int
    revision_family: str
    maturity: str
    lifecycle: str
    created_at: datetime
    modified_at: datetime
    owner: str
    organization: str
    project: str
    mass_kg: float
    material: str | None
    item_kind: ItemKind
    is_root: bool
    is_leaf: bool
    source: SourceRef


@dataclass(frozen=True, slots=True)
class Instance:
    xid: str
    instance_name: str
    parent_plm_id: str
    child_plm_id: str
    child_order: float
    rotation: tuple[float, ...]
    translation: tuple[float, float, float]
    source: SourceRef


@dataclass(frozen=True, slots=True)
class Representation:
    plm_id: str
    xid: str
    name: str
    product_plm_id: str
    revision: str
    maturity: str
    source: SourceRef


@dataclass(frozen=True, slots=True)
class BomLine:
    parent_plm_id: str
    child_plm_id: str
    quantity: int
    order: float
    instance_xids: tuple[str, ...]
    sources: tuple[SourceRef, ...]


@dataclass(frozen=True, slots=True)
class Occurrence:
    """One node of the expanded tree. `path` lists plm_ids from the root (included) to this product (included)."""

    path: tuple[str, ...]
    cumulative_quantity: int
    sources: tuple[SourceRef, ...]

    @property
    def plm_id(self) -> str:
        return self.path[-1]

    @property
    def depth(self) -> int:
        return len(self.path) - 1


@dataclass(frozen=True, slots=True)
class MaterialPrice:
    material: str
    price_eur_per_kg: float
    source_note: str
    source: SourceRef
    price_volatility: str | None = None  # optional: "low" | "medium" | "high"


@dataclass(frozen=True, slots=True)
class Supplier:
    supplier_id: str
    name: str
    country: str
    source_note: str
    source: SourceRef


@dataclass(frozen=True, slots=True)
class PartSourcing:
    plm_id: str
    supplier_id: str
    make_or_buy: MakeOrBuy
    lead_time_days: int | None
    source_note: str
    source: SourceRef
