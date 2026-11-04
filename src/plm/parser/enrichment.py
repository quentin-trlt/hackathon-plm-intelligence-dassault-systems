"""CSV loader for non-PLM data, implementing EnrichmentRepository. File formats: docs/data_contract.md."""

from __future__ import annotations

import csv
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from plm.contract import PlmRepository, UnknownProductError, UnknownSupplierError
from plm.model import MaterialPrice, PartSourcing, SourceRef, Supplier

logger = logging.getLogger(__name__)

MATERIAL_PRICES_FILE = "material_prices.csv"
SUPPLIERS_FILE = "suppliers.csv"
PART_SOURCING_FILE = "part_sourcing.csv"

_SUPPLIER_ID = re.compile(r"^SUP-\d{3}$")
_COUNTRY = re.compile(r"^[A-Z]{2}$")


class EnrichmentError(ValueError):
    """One or more enrichment rows break the data contract."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Invalid enrichment data:\n" + "\n".join(f"- {p}" for p in problems))


@dataclass(frozen=True, slots=True)
class CoverageReport:
    leaves_without_sourcing: tuple[str, ...]
    leaves_without_material: tuple[str, ...]
    materials_without_price: tuple[str, ...]
    unfilled_rows: tuple[SourceRef, ...]

    @property
    def complete(self) -> bool:
        return not (self.leaves_without_sourcing or self.leaves_without_material or self.materials_without_price)


@dataclass(frozen=True, slots=True)
class _Row:
    values: dict[str, str]
    source: SourceRef

    def __getitem__(self, column: str) -> str:
        return self.values.get(column, "")

    def where(self) -> str:
        return f"{self.source.file}:{self.source.line}"


def _read(
    path: Path, required: tuple[str, ...], value_columns: tuple[str, ...], problems: list[str]
) -> tuple[list[_Row], list[SourceRef]]:
    if not path.is_file():
        logger.info("Enrichment file not found, treated as empty: %s", path)
        return [], []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = [h.strip() for h in reader.fieldnames or []]
        if len(header) == 1 and ";" in header[0]:
            problems.append(f"{path.name}: uses ';' as separator, save it as comma-separated CSV")
            return [], []
        missing = [c for c in required if c not in header]
        if missing:
            problems.append(f"{path.name}: missing columns {missing}")
            return [], []
        rows: list[_Row] = []
        unfilled: list[SourceRef] = []
        for raw in reader:
            values = {(k or "").strip(): (v or "").strip() for k, v in raw.items() if k is not None}
            source = SourceRef(origin="enrichment", file=path.name, line=reader.line_num)
            if not any(values.get(c) for c in value_columns):
                unfilled.append(source)
                continue
            rows.append(_Row(values, source))
    return rows, unfilled


def _required(row: _Row, column: str, problems: list[str]) -> str | None:
    value = row[column]
    if not value:
        problems.append(f"{row.where()}: {column} is empty")
        return None
    return value


class CsvEnrichmentRepository:
    def __init__(
        self,
        material_prices: list[MaterialPrice],
        suppliers: list[Supplier],
        sourcing: list[PartSourcing],
        unfilled_rows: list[SourceRef],
    ) -> None:
        self._prices = {p.material: p for p in material_prices}
        self._suppliers = {s.supplier_id: s for s in suppliers}
        self._sourcing: dict[str, list[PartSourcing]] = defaultdict(list)
        for entry in sourcing:
            self._sourcing[entry.plm_id].append(entry)
        self.unfilled_rows = tuple(unfilled_rows)

    @classmethod
    def from_dir(cls, directory: Path, plm: PlmRepository) -> CsvEnrichmentRepository:
        directory = Path(directory)
        if not directory.is_dir():
            logger.warning("Enrichment directory not found, no enrichment data loaded: %s", directory)
            return cls([], [], [], [])
        return _Loader(directory, plm).load()

    def material_price(self, material: str) -> MaterialPrice | None:
        return self._prices.get(material)

    def material_prices(self) -> list[MaterialPrice]:
        return list(self._prices.values())

    def suppliers(self) -> list[Supplier]:
        return list(self._suppliers.values())

    def supplier(self, supplier_id: str) -> Supplier:
        try:
            return self._suppliers[supplier_id]
        except KeyError:
            raise UnknownSupplierError(f"No supplier with supplier_id {supplier_id!r}") from None

    def sourcing(self, plm_id: str) -> list[PartSourcing]:
        return list(self._sourcing.get(plm_id, []))

    def coverage(self, plm: PlmRepository) -> CoverageReport:
        leaves = plm.leaves()
        materials = sorted({p.material for p in leaves if p.material})
        return CoverageReport(
            leaves_without_sourcing=tuple(p.plm_id for p in leaves if not self._sourcing.get(p.plm_id)),
            leaves_without_material=tuple(p.plm_id for p in leaves if not p.material),
            materials_without_price=tuple(m for m in materials if m not in self._prices),
            unfilled_rows=self.unfilled_rows,
        )


class _Loader:
    def __init__(self, directory: Path, plm: PlmRepository) -> None:
        self.directory = directory
        self.plm = plm
        self.problems: list[str] = []
        self.unfilled: list[SourceRef] = []

    def load(self) -> CsvEnrichmentRepository:
        prices = self._material_prices()
        suppliers = self._suppliers()
        sourcing = self._sourcing({s.supplier_id for s in suppliers})
        if self.problems:
            raise EnrichmentError(self.problems)
        return CsvEnrichmentRepository(prices, suppliers, sourcing, self.unfilled)

    def _rows(self, file: str, required: tuple[str, ...], value_columns: tuple[str, ...]) -> list[_Row]:
        rows, unfilled = _read(self.directory / file, required, value_columns, self.problems)
        self.unfilled.extend(unfilled)
        return rows

    def _material_prices(self) -> list[MaterialPrice]:
        rows = self._rows(
            MATERIAL_PRICES_FILE, ("material", "price_eur_per_kg", "source_note"), ("price_eur_per_kg", "source_note")
        )
        known = {p.material for p in self.plm.leaves() if p.material}
        seen: dict[str, str] = {}
        result = []
        for row in rows:
            material = _required(row, "material", self.problems)
            price = self._positive_float(row, "price_eur_per_kg")
            note = _required(row, "source_note", self.problems)
            if material is not None:
                if material not in known:
                    self.problems.append(f"{row.where()}: unknown material {material!r}, it must match the XML exactly")
                if material in seen:
                    self.problems.append(f"{row.where()}: material {material!r} already priced at {seen[material]}")
                seen.setdefault(material, row.where())
            if material and price is not None and note:
                result.append(MaterialPrice(material, price, note, row.source))
        return result

    def _suppliers(self) -> list[Supplier]:
        rows = self._rows(
            SUPPLIERS_FILE, ("supplier_id", "name", "country", "source_note"), ("name", "country", "source_note")
        )
        seen: dict[str, str] = {}
        result = []
        for row in rows:
            supplier_id = _required(row, "supplier_id", self.problems)
            name = _required(row, "name", self.problems)
            country = _required(row, "country", self.problems)
            note = _required(row, "source_note", self.problems)
            if supplier_id is not None:
                if not _SUPPLIER_ID.match(supplier_id):
                    self.problems.append(f"{row.where()}: supplier_id {supplier_id!r} does not match SUP-NNN")
                if supplier_id in seen:
                    self.problems.append(
                        f"{row.where()}: supplier_id {supplier_id!r} already defined at {seen[supplier_id]}"
                    )
                seen.setdefault(supplier_id, row.where())
            if country is not None and not _COUNTRY.match(country):
                self.problems.append(f"{row.where()}: country {country!r} is not an ISO 3166-1 alpha-2 code")
            if supplier_id and name and country and note:
                result.append(Supplier(supplier_id, name, country, note, row.source))
        return result

    def _sourcing(self, supplier_ids: set[str]) -> list[PartSourcing]:
        rows = self._rows(
            PART_SOURCING_FILE,
            ("plm_id", "supplier_id", "make_or_buy", "lead_time_days", "source_note"),
            ("supplier_id", "make_or_buy", "lead_time_days", "source_note"),
        )
        seen: dict[tuple[str, str], str] = {}
        result = []
        for row in rows:
            plm_id = _required(row, "plm_id", self.problems)
            supplier_id = _required(row, "supplier_id", self.problems)
            make_or_buy = _required(row, "make_or_buy", self.problems)
            note = _required(row, "source_note", self.problems)
            lead_time = self._lead_time(row)
            if plm_id is not None:
                self._check_leaf(row, plm_id)
            if supplier_id is not None and supplier_id not in supplier_ids:
                self.problems.append(f"{row.where()}: supplier_id {supplier_id!r} is not in {SUPPLIERS_FILE}")
            if make_or_buy is not None:
                make_or_buy = make_or_buy.lower()
                if make_or_buy not in ("make", "buy"):
                    self.problems.append(
                        f"{row.where()}: make_or_buy must be 'make' or 'buy', got {row['make_or_buy']!r}"
                    )
            if plm_id and supplier_id:
                key = (plm_id, supplier_id)
                if key in seen:
                    self.problems.append(f"{row.where()}: {plm_id} / {supplier_id} already listed at {seen[key]}")
                seen.setdefault(key, row.where())
            if plm_id and supplier_id and make_or_buy in ("make", "buy") and note:
                result.append(PartSourcing(plm_id, supplier_id, make_or_buy, lead_time, note, row.source))
        return result

    def _check_leaf(self, row: _Row, plm_id: str) -> None:
        try:
            product = self.plm.product(plm_id)
        except UnknownProductError:
            self.problems.append(f"{row.where()}: plm_id {plm_id!r} is not in the PLM extract")
            return
        if not product.is_leaf:
            self.problems.append(f"{row.where()}: {plm_id} ({product.name}) is an assembly, sourcing applies to leaves")

    def _positive_float(self, row: _Row, column: str) -> float | None:
        raw = _required(row, column, self.problems)
        if raw is None:
            return None
        try:
            value = float(raw)
        except ValueError:
            hint = ", use a decimal point" if "," in raw else ""
            self.problems.append(f"{row.where()}: {column} {raw!r} is not a number{hint}")
            return None
        if value <= 0:
            self.problems.append(f"{row.where()}: {column} must be > 0, got {value}")
            return None
        return value

    def _lead_time(self, row: _Row) -> int | None:
        raw = row["lead_time_days"]
        if not raw:
            return None
        try:
            value = int(raw)
        except ValueError:
            self.problems.append(f"{row.where()}: lead_time_days {raw!r} is not an integer")
            return None
        if value < 0:
            self.problems.append(f"{row.where()}: lead_time_days must be >= 0, got {value}")
            return None
        return value
