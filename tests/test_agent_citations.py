from __future__ import annotations

import pytest

from plm.agent.citations import check_citations
from plm.agent.evidence import EvidenceRegistry
from plm.model import SourceRef


@pytest.fixture
def registry() -> EvidenceRegistry:
    registry = EvidenceRegistry()
    for line in (10, 20, 30):
        registry.register(f"fact {line}", (SourceRef("plm_xml", "x.xml", line, "Product", f"ID_{line}"),))
    return registry


def test_known_citations_are_kept_in_order_of_appearance(registry: EvidenceRegistry) -> None:
    checked = check_citations("Mass 2 kg [E2]. Quantity 14 [E1].", registry)
    assert checked.cited == ("E2", "E1") and checked.warnings == ()


@pytest.mark.parametrize("text", ["14 screws 【E1】.", "14 screws [E1, E3].", "14 screws [ E1 ; E3 ]."])
def test_alternative_forms_are_normalised(registry: EvidenceRegistry, text: str) -> None:
    checked = check_citations(text, registry)
    assert "[E1]" in checked.text and "【" not in checked.text and "," not in checked.text.split("screws")[1]


def test_unknown_ids_are_removed(registry: EvidenceRegistry) -> None:
    checked = check_citations("14 screws [E1][E9]. Cost 3 EUR [E8].", registry)
    assert checked.cited == ("E1",)
    assert "[E9]" not in checked.text and "[E8]" not in checked.text
    assert any("E9" in w and "E8" in w for w in checked.warnings)


def test_uncited_figures_are_flagged(registry: EvidenceRegistry) -> None:
    checked = check_citations("There are 14 screws [E1]. The housing weighs 30.52 kg.", registry)
    assert checked.warnings == ("1 sentence(s) state a figure without a citation",)


@pytest.mark.parametrize(
    "text",
    [
        "The ECAR-3D Screw D10 L155 sits in the 410KW_EDRIVE_FRONT.",
        "See prd-12535825-00000081 for details.",
        "1. First item\n2. Second item",
        "| Material | Mass |\n|---|---|\n| Steel | 21.62 [E1] |",
    ],
)
def test_identifiers_and_list_markers_are_not_figures(registry: EvidenceRegistry, text: str) -> None:
    assert check_citations(text, registry).warnings == ()
