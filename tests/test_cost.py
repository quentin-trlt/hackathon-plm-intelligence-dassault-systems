"""Tests for plm.analysis.cost, using in-memory fakes of the contract interfaces."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from plm.analysis.cost import MaterialCostService
from plm.model import BomLine, MaterialPrice, Product, SourceRef

NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


def ref(name: str) -> SourceRef:
    return SourceRef(origin="plm_xml", file="test.xml", line=1, element=name)


def product(plm_id: str, mass: float, material: str | None, leaf: bool) -> Product:
    return Product(
        plm_id=plm_id, xid=plm_id, name=plm_id, name_normalized=plm_id, description=None,
        revision="A", revision_index=1, revision_family=plm_id, maturity="IN_WORK",
        lifecycle="x", created_at=NOW, modified_at=NOW, owner="o", organization="o",
        project="p", mass_kg=mass, material=material,
        item_kind="part" if leaf else "assembly", is_root=False, is_leaf=leaf,
        source=ref(plm_id),
    )


class FakePlm:
    def __init__(self, products: list[Product], lines: list[BomLine]) -> None:
        self._p = {p.plm_id: p for p in products}
        self._lines = lines

    def product(self, plm_id: str) -> Product:
        return self._p[plm_id]

    def leaves(self) -> list[Product]:
        return [p for p in self._p.values() if p.is_leaf]

    def children(self, plm_id: str) -> list[BomLine]:
        return [l for l in self._lines if l.parent_plm_id == plm_id]


class FakeEnrichment:
    def __init__(self, prices: dict[str, float]) -> None:
        self._prices = prices

    def material_price(self, material: str) -> MaterialPrice | None:
        if material not in self._prices:
            return None
        return MaterialPrice(
            material=material, price_eur_per_kg=self._prices[material], source_note="test",
            source=SourceRef(origin="enrichment", file="material_prices.csv", line=2),
        )


def line(parent: str, child: str, qty: int) -> BomLine:
    return BomLine(parent, child, qty, 0.0, (), (ref(f"{parent}>{child}"),))


@pytest.fixture
def plm() -> FakePlm:
    # root -> wrapper(EI) -> screw(3D) x14, root -> casing(3D)
    return FakePlm(
        [
            product("root", 2.0, None, leaf=False),
            product("wrapper", 0.1, "Steel", leaf=False),
            product("screw", 0.1, "Steel", leaf=True),
            product("casing", 0.6, "Alu", leaf=True),
        ],
        [line("root", "wrapper", 14), line("wrapper", "screw", 1), line("root", "casing", 1)],
    )


def test_leaf_cost_is_mass_times_price(plm: FakePlm) -> None:
    cost = MaterialCostService(plm, FakeEnrichment({"Steel": 1.0, "Alu": 2.5})).unit_cost("casing")
    assert cost is not None and cost.value == pytest.approx(1.5)


def test_assembly_rolls_up_quantities_without_double_counting_wrapper(plm: FakePlm) -> None:
    cost = MaterialCostService(plm, FakeEnrichment({"Steel": 1.0, "Alu": 2.5})).unit_cost("root")
    # 14 x 0.1 x 1.0 + 0.6 x 2.5 ; the wrapper's own mass is not added
    assert cost is not None and cost.value == pytest.approx(2.9)


def test_sources_include_xml_and_price_rows(plm: FakePlm) -> None:
    cost = MaterialCostService(plm, FakeEnrichment({"Steel": 1.0, "Alu": 2.5})).unit_cost("root")
    origins = {s.origin for s in cost.sources}
    assert origins == {"plm_xml", "enrichment"}


def test_missing_price_gives_none_not_zero(plm: FakePlm) -> None:
    service = MaterialCostService(plm, FakeEnrichment({"Steel": 1.0}))
    assert service.unit_cost("root") is None
    assert service.unit_cost("wrapper") is not None
    assert [p.plm_id for p in service.missing_prices()] == ["casing"]


def test_excluded_parts_are_left_out(plm: FakePlm) -> None:
    service = MaterialCostService(
        plm, FakeEnrichment({"Steel": 1.0}), excluded_plm_ids=frozenset({"casing"})
    )
    cost = service.unit_cost("root")
    assert cost is not None and cost.value == pytest.approx(1.4)
    assert service.missing_prices() == []
