"""Tests for plm.analysis.risk on the real OEM extract and the team's enrichment files."""

from __future__ import annotations

import pytest

from plm.analysis.cost import MaterialCostService
from plm.analysis.risk import MESHING_ARTEFACTS, RiskConfig, RiskService
from plm.parser import CsvEnrichmentRepository, XmlPlmRepository

from conftest import ENRICHMENT_DIR


@pytest.fixture(scope="module")
def enrichment(plm: XmlPlmRepository) -> CsvEnrichmentRepository:
    return CsvEnrichmentRepository.from_dir(ENRICHMENT_DIR, plm)


@pytest.fixture(scope="module")
def risk(plm: XmlPlmRepository, enrichment: CsvEnrichmentRepository) -> RiskService:
    return RiskService(plm, enrichment, MaterialCostService(plm, enrichment))


def test_ranking_covers_every_real_leaf_sorted(plm, risk):
    ranking = risk.ranking()
    assert len(ranking) == len(plm.leaves()) - len(MESHING_ARTEFACTS)
    assert {r.plm_id for r in ranking}.isdisjoint(MESHING_ARTEFACTS)
    scores = [r.criticality for r in ranking]
    assert scores == sorted(scores, reverse=True)


def test_scores_stay_in_range_and_every_criterion_is_sourced(risk):
    for r in risk.ranking():
        assert 0 <= r.probability <= 1 and 0 <= r.impact <= 1 and 0 <= r.criticality <= 100
        assert r.level in {"low", "medium", "high"}
        for c in r.criteria:
            assert 0 <= c.score <= 1
            assert c.sources, f"{r.name} {c.code} has no source"
            assert c.explanation


def test_single_bought_source_is_max_supply_risk(risk, enrichment):
    for r in risk.ranking():
        rows = enrichment.sourcing(r.plm_id)
        if len(rows) == 1 and rows[0].make_or_buy == "buy":
            assert r.criterion("SUPPLY").score == 1.0
        if len(rows) >= 2:
            assert r.criterion("SUPPLY").score < 1.0
            assert r.criterion("CONCENTRATION").score == 0.0


def test_in_house_single_source_is_half_supply_risk(risk, enrichment):
    in_house = [r for r in risk.ranking() if [s.make_or_buy for s in enrichment.sourcing(r.plm_id)] == ["make"]]
    assert in_house
    assert all(r.criterion("SUPPLY").score == 0.5 for r in in_house)


def test_criterion_without_data_is_dropped_not_zero(plm, enrichment):
    # The meshing artefacts have no lead time: when they are scored, LEAD_TIME is absent.
    service = RiskService(plm, enrichment, MaterialCostService(plm, enrichment), excluded_plm_ids=frozenset())
    tooth = service.part_risk("prd-12535825-00000118")
    assert tooth.criterion("LEAD_TIME") is None
    assert tooth.criterion("COST_SHARE") is not None


def test_no_cost_service_drops_cost_criterion(plm, enrichment):
    r = RiskService(plm, enrichment).ranking()[0]
    assert r.criterion("COST_SHARE") is None


def test_shared_part_gets_reuse_score(plm, risk):
    shaft = next(p for p in plm.leaves() if p.name == "ECAR-3D_DifferentialSideShaft")
    assert risk.part_risk(shaft.plm_id).criterion("REUSE").score > 0


def test_assembly_risk_counts_all_parts_below(plm, risk):
    root = risk.assembly_risk(plm.root().plm_id)
    assert sum(root.count_by_level.values()) == len(risk.ranking())
    assert root.worst == risk.ranking()[0]


def test_assembly_rejected_by_part_risk(plm, risk):
    with pytest.raises(ValueError):
        risk.part_risk(plm.root().plm_id)


def test_heat_map_levels(plm, enrichment):
    service = RiskService(plm, enrichment, config=RiskConfig())
    assert service._level(0.9, 0.5) == "high"
    assert service._level(0.5, 0.9) == "high"
    assert service._level(0.5, 0.5) == "medium"
    assert service._level(0.9, 0.1) == "medium"
    assert service._level(0.5, 0.1) == "low"


def test_supplier_exposure_sums_single_sourced_cost(risk):
    exposure = risk.supplier_exposure()
    assert sum(s.single_sourced_cost_share for s in exposure) <= 1.0 + 1e-9
    shares = [s.single_sourced_cost_share for s in exposure]
    assert shares == sorted(shares, reverse=True)
