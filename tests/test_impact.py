"""Tests for plm.analysis.impact and plm.analysis.summary on the real extract."""

from __future__ import annotations

import pytest
from conftest import ENRICHMENT_DIR

from plm.analysis.cost import MaterialCostService
from plm.analysis.impact import ChangeImpactService
from plm.analysis.risk import RiskService
from plm.analysis.summary import subassembly_summary
from plm.parser import CsvEnrichmentRepository

COPPER = "Copper Cu-ETP (enamelled wire)"


@pytest.fixture(scope="module")
def services(plm):
    enrichment = CsvEnrichmentRepository.from_dir(ENRICHMENT_DIR, plm)
    cost = MaterialCostService(plm, enrichment)
    risk = RiskService(plm, enrichment, cost)
    return plm, enrichment, cost, risk, ChangeImpactService(plm, enrichment, cost, risk)


def leaf(plm, name):
    return next(p for p in plm.leaves() if p.name == name)


def test_shared_part_impacts_both_wrappers_and_counts_units(services):
    plm, _, _, _, impact = services
    shaft = leaf(plm, "ECAR-3D_DifferentialSideShaft")
    result = impact.assess(shaft.plm_id, new_mass_kg=shaft.mass_kg + 0.1)
    by_name = {a.name: a for a in result.impacted}
    assert by_name["ECAR-EI_410KW_EDRIVE_FRONT"].units == 2
    assert by_name["ECAR-EI_DIFFERENTIAL_ASSEMBLY"].units == 2
    assert by_name["ECAR-EI_DIFFERENTIAL SIDE SHAFT LEFT"].units == 1
    assert by_name["ECAR-EI_DIFFERENTIAL SIDE SHAFT RIGHT"].units == 1
    assert result.root_mass_delta_kg == pytest.approx(0.2)
    assert by_name["ECAR-EI_410KW_EDRIVE_FRONT"].projected_mass_kg == pytest.approx(100.3)
    assert [a.depth for a in result.impacted] == sorted(a.depth for a in result.impacted)


def test_material_change_moves_cost_and_volatility(services):
    plm, enrichment, cost, _, impact = services
    screw = leaf(plm, "ECAR-3D Screw D10 L155")
    result = impact.assess(screw.plm_id, new_material=COPPER)
    expected_unit = screw.mass_kg * enrichment.material_price(COPPER).price_eur_per_kg
    assert result.proposed_unit_cost_eur == pytest.approx(expected_unit)
    assert result.root_cost_delta_eur == pytest.approx((expected_unit - cost.unit_cost(screw.plm_id).value) * 14)
    root = next(a for a in result.impacted if a.depth == 0)
    assert root.projected_cost_eur - root.current_cost_eur == pytest.approx(result.root_cost_delta_eur)
    assert result.current_volatility == "low" and result.proposed_volatility == "high"


def test_replacement_by_existing_part(services):
    plm, enrichment, cost, risk, impact = services
    gears = [p for p in plm.leaves() if p.name == "ECAR-3D_GearWheel"]
    light, heavy = min(gears, key=lambda p: p.mass_kg), max(gears, key=lambda p: p.mass_kg)
    result = impact.assess(heavy.plm_id, replacement_plm_id=light.plm_id)
    assert result.replacement == light
    assert result.root_mass_delta_kg == pytest.approx(light.mass_kg - heavy.mass_kg)
    assert result.root_cost_delta_eur == pytest.approx(
        cost.unit_cost(light.plm_id).value - cost.unit_cost(heavy.plm_id).value
    )
    assert result.proposed_suppliers == tuple(enrichment.sourcing(light.plm_id))
    assert result.proposed_risk == risk.part_risk(light.plm_id)
    assert result.sources


def test_invalid_proposals_are_rejected(services):
    plm, _, _, _, impact = services
    root = plm.root()
    shaft = leaf(plm, "ECAR-3D_DifferentialSideShaft")
    with pytest.raises(ValueError):
        impact.assess(root.plm_id, new_mass_kg=1)
    with pytest.raises(ValueError):
        impact.assess(shaft.plm_id, replacement_plm_id=root.plm_id)
    with pytest.raises(ValueError):
        impact.assess(shaft.plm_id, new_mass_kg=1, replacement_plm_id=leaf(plm, "ECAR-3D_Pin").plm_id)
    assert all(p.is_leaf for p in impact.replacement_candidates(shaft.plm_id))


def test_unchanged_proposal_has_no_delta(services):
    plm, _, _, _, impact = services
    for product in plm.products():
        if product.is_root:
            continue
        result = impact.assess(product.plm_id)
        assert result.root_mass_delta_kg == pytest.approx(0)
        assert result.root_cost_delta_eur in (None, pytest.approx(0))


def test_subassembly_summary(services):
    plm, _, cost, risk, _ = services
    summary = subassembly_summary(plm, cost, risk)
    assert {s.name for s in summary} == {
        "ECAR-EI_EMOTOR FRONT",
        "ECAR-EI_REDUCER FRONT",
        "ECAR-EI_HOUSING FRONT EDRIVE",
    }
    assert sum(s.mass_share for s in summary) == pytest.approx(1.0)
    assert sum(s.cost_share for s in summary) == pytest.approx(1.0)
    assert sum(s.parts for s in summary) == len(risk.ranking())
    assert summary[0].name == "ECAR-EI_EMOTOR FRONT"
