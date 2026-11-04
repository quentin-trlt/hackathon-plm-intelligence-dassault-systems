from __future__ import annotations

import json
from typing import Any

import pytest

from plm.agent.evidence import EvidenceRegistry
from plm.agent.tools import TOOLS, ToolContext, execute, tool_schemas
from plm.parser import PlmData

SCREW_L155 = "prd-12535825-00000117"
SCREW_L155_WRAPPER = "prd-12535825-00000080"
SIDE_SHAFT = "prd-12535825-00000125"
DIFFERENTIAL = "prd-12535825-00000055"
REDUCER = "prd-12535825-00000049"


@pytest.fixture
def ctx(plm_data: PlmData) -> ToolContext:
    return ToolContext.build(plm_data.plm, plm_data.enrichment, EvidenceRegistry())


def call(ctx: ToolContext, name: str, **arguments: Any) -> dict[str, Any]:
    result = execute(ctx, name, json.dumps(arguments))
    assert "error" not in result, result["error"]
    return result


def evidence_ids(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [v for k, v in value.items() if k.endswith("evidence")] + [
            i for v in value.values() for i in evidence_ids(v)
        ]
    if isinstance(value, list):
        return [i for v in value for i in evidence_ids(v)]
    return []


def kinds(value: Any) -> set[str]:
    if isinstance(value, dict):
        return ({value["kind"]} if "kind" in value else set()) | {k for v in value.values() for k in kinds(v)}
    if isinstance(value, list):
        return {k for v in value for k in kinds(v)}
    return set()


def test_schemas_are_consistent() -> None:
    names = [t.name for t in TOOLS]
    assert len(names) == len(set(names))
    for schema in tool_schemas():
        parameters = schema["function"]["parameters"]
        assert set(parameters["required"]) <= set(parameters["properties"])


def test_search_finds_the_exact_xml_name(ctx: ToolContext) -> None:
    result = call(ctx, "search_products", query="ECAR-3D Screw D10 L155")
    assert [p["plm_id"] for p in result["products"]] == [SCREW_L155]
    assert result["products"][0]["wrapper_plm_ids"] == [SCREW_L155_WRAPPER]


def test_search_merges_wrappers_into_their_part(ctx: ToolContext) -> None:
    result = call(ctx, "search_products", query="differential side shaft")
    assert [p["plm_id"] for p in result["products"]] == [SIDE_SHAFT]
    assert len(result["products"][0]["wrapper_plm_ids"]) == 2


def test_search_falls_back_to_all_words(ctx: ToolContext) -> None:
    result = call(ctx, "search_products", query="side shaft differential")
    assert result["match"] == "all_words"
    assert SIDE_SHAFT in [p["plm_id"] for p in result["products"]]


def test_search_keeps_homonyms_apart(ctx: ToolContext) -> None:
    result = call(ctx, "search_products", query="gearwheel")
    assert result["total"] == 4
    assert len({p["mass_kg"] for p in result["products"]}) == 4


def test_search_without_match_gives_a_hint(ctx: ToolContext) -> None:
    result = call(ctx, "search_products", query="magnet")
    assert result["total"] == 0 and result["match"] == "none" and "hint" in result


def test_get_product_resolves_a_wrapper(ctx: ToolContext) -> None:
    card = call(ctx, "get_product", plm_id=SCREW_L155_WRAPPER)
    assert card["plm_id"] == SCREW_L155 and card["resolved_from_wrapper"] == SCREW_L155_WRAPPER
    assert card["total_quantity_in_motor"] == 14 and card["supplier_count"] >= 1


def test_children_of_root_are_the_three_subassemblies(ctx: ToolContext, plm_data: PlmData) -> None:
    result = call(ctx, "get_children", plm_id=plm_data.plm.root().plm_id)
    assert [c["name"] for c in result["children"]] == [
        "ECAR-EI_EMOTOR FRONT",
        "ECAR-EI_REDUCER FRONT",
        "ECAR-EI_HOUSING FRONT EDRIVE",
    ]


def test_children_never_expose_wrappers(ctx: ToolContext) -> None:
    result = call(ctx, "get_children", plm_id=DIFFERENTIAL)
    assert kinds(result["children"]) <= {"part", "assembly"}
    assert result["total"] == len(result["children"])


def test_where_used_side_shaft(ctx: ToolContext, plm_data: PlmData) -> None:
    result = call(ctx, "get_where_used", plm_id=SIDE_SHAFT)
    assert result["occurrence_count"] == 2 and result["total_quantity_in_motor"] == 2
    assert {o["position"] for o in result["occurrences"]} == {
        "ECAR-EI_DIFFERENTIAL SIDE SHAFT LEFT",
        "ECAR-EI_DIFFERENTIAL SIDE SHAFT RIGHT",
    }
    assert [a["plm_id"] for a in result["impacted_assemblies"]] == [
        plm_data.plm.root().plm_id,
        REDUCER,
        DIFFERENTIAL,
    ]


def test_total_quantity(ctx: ToolContext) -> None:
    assert call(ctx, "get_total_quantity", plm_id=SCREW_L155)["total_quantity_in_motor"] == 14


def test_unit_cost_of_the_motor(ctx: ToolContext, plm_data: PlmData) -> None:
    result = call(ctx, "get_unit_cost", plm_id=plm_data.plm.root().plm_id)
    assert result["unit_cost_eur"] == pytest.approx(266.68, abs=0.01)
    assert result["origin"] == "enrichment" and "cost_in_motor_eur" not in result
    assert ctx.registry.get(result["evidence"]).is_enrichment


def test_unit_cost_of_a_part_includes_the_motor_total(ctx: ToolContext) -> None:
    result = call(ctx, "get_unit_cost", plm_id=SCREW_L155)
    exact_unit_cost = result["product"]["mass_kg"] * result["material_price_eur_per_kg"]
    assert result["total_quantity_in_motor"] == 14
    assert result["cost_in_motor_eur"] == pytest.approx(exact_unit_cost * 14, abs=0.01)


def test_compare_homonyms_in_one_call(ctx: ToolContext) -> None:
    gear_ids = [p["plm_id"] for p in call(ctx, "search_products", query="gearwheel")["products"]]
    result = call(ctx, "compare_products", plm_ids=gear_ids)
    assert len(result["products"]) == 4 and result["unknown_plm_ids"] == []
    for card in result["products"]:
        assert card["total_quantity_in_motor"] == 1 and card["unit_cost_eur"] > 0 and card["suppliers"]
        assert all(i in ctx.registry for i in evidence_ids(card))


def test_compare_merges_a_wrapper_with_its_part_and_reports_unknown_ids(ctx: ToolContext) -> None:
    result = call(ctx, "compare_products", plm_ids=[SCREW_L155, SCREW_L155_WRAPPER, "prd-nope"])
    assert [p["plm_id"] for p in result["products"]] == [SCREW_L155]
    assert result["unknown_plm_ids"] == ["prd-nope"]


@pytest.mark.parametrize(
    ("plm_ids", "message"),
    [([], "non-empty list"), ([SCREW_L155] * 11, "At most 10"), (SCREW_L155, "non-empty list")],
)
def test_compare_rejects_bad_lists(ctx: ToolContext, plm_ids: object, message: str) -> None:
    assert message in execute(ctx, "compare_products", json.dumps({"plm_ids": plm_ids}))["error"]


def single_source_ids(plm_data: PlmData) -> set[str]:
    return {leaf.plm_id for leaf in plm_data.plm.leaves() if len(plm_data.enrichment.sourcing(leaf.plm_id)) == 1}


def test_sourcing_matches_the_enrichment_rows(ctx: ToolContext, plm_data: PlmData) -> None:
    result = call(ctx, "get_sourcing", plm_id=SIDE_SHAFT)
    expected = [(s.supplier_id, s.lead_time_days) for s in plm_data.enrichment.sourcing(SIDE_SHAFT)]
    assert expected and [(s["supplier_id"], s["lead_time_days"]) for s in result["suppliers"]] == expected
    assert result["single_source"] == (len(expected) == 1)
    assert all(ctx.registry.get(s["evidence"]).is_enrichment for s in result["suppliers"])


def test_sourcing_rejects_assemblies(ctx: ToolContext) -> None:
    assert "assembly" in execute(ctx, "get_sourcing", json.dumps({"plm_id": DIFFERENTIAL}))["error"]


def test_supplier_dependencies(ctx: ToolContext, plm_data: PlmData) -> None:
    result = call(ctx, "get_supplier_dependencies", limit=30)
    assert result["single_source_part_count"] == len(single_source_ids(plm_data))
    assert result["part_count"] == len(plm_data.plm.leaves())
    counts = [s["part_count"] for s in result["suppliers"]]
    assert counts == sorted(counts, reverse=True) and len(counts) == result["supplier_total"]


def test_single_source_list_is_complete_by_default(ctx: ToolContext, plm_data: PlmData) -> None:
    result = call(ctx, "get_supplier_dependencies", single_source_only=True)
    assert {p["plm_id"] for p in result["single_source_parts"]} == single_source_ids(plm_data)
    assert not result["single_source_parts_truncated"]


def test_supplier_dependencies_for_one_supplier(ctx: ToolContext, plm_data: PlmData) -> None:
    top = call(ctx, "get_supplier_dependencies")["suppliers"][0]["supplier_id"]
    result = call(ctx, "get_supplier_dependencies", supplier_id=top, single_source_only=True)
    assert {p["plm_id"] for p in result["parts"]} <= single_source_ids(plm_data)
    assert all(p["single_source"] for p in result["parts"])


def test_mass_by_material(ctx: ToolContext) -> None:
    result = call(ctx, "aggregate_leaves", group_by="material", metric="mass_kg", top=30)
    assert result["total"] == pytest.approx(100.1, abs=0.01)
    assert sum(g["value"] for g in result["groups"]) == pytest.approx(100.1, abs=0.01)
    assert [(g["key"][:16], g["value"]) for g in result["groups"][:3]] == [
        ("Electrical steel", pytest.approx(21.62, abs=0.01)),
        ("Case-hardening s", pytest.approx(20.50, abs=0.01)),
        ("Aluminium alloy ", pytest.approx(19.60, abs=0.01)),
    ]


def test_mass_by_subassembly_matches_the_xml_masses(ctx: ToolContext, plm_data: PlmData) -> None:
    result = call(ctx, "aggregate_leaves", group_by="subassembly", metric="mass_kg")
    for group in result["groups"]:
        assert group["value"] == pytest.approx(plm_data.plm.product(group["id"]).mass_kg, abs=0.01)


def test_cost_by_assembly_adds_up_to_the_motor(ctx: ToolContext) -> None:
    result = call(ctx, "aggregate_leaves", group_by="assembly", metric="material_cost_eur", top=30)
    assert sum(g["value"] for g in result["groups"]) == pytest.approx(266.68, abs=0.05)
    assert result["caveat"] and result["parts_without_price"] == []


def test_count_by_supplier_warns_about_multi_sourcing(ctx: ToolContext) -> None:
    result = call(ctx, "aggregate_leaves", group_by="supplier", metric="count")
    assert isinstance(result["total"], int) and "note" in result


def test_every_cited_evidence_is_registered(ctx: ToolContext, plm_data: PlmData) -> None:
    results = [
        call(ctx, "search_products", query="bearing"),
        call(ctx, "get_product", plm_id=SIDE_SHAFT),
        call(ctx, "get_children", plm_id=plm_data.plm.root().plm_id),
        call(ctx, "get_where_used", plm_id=SIDE_SHAFT),
        call(ctx, "get_unit_cost", plm_id=REDUCER),
        call(ctx, "get_sourcing", plm_id=SIDE_SHAFT),
        call(ctx, "get_supplier_dependencies", single_source_only=True),
        call(ctx, "aggregate_leaves", group_by="country", metric="mass_kg"),
    ]
    ids = evidence_ids(results)
    assert ids and all(i in ctx.registry for i in ids)
    assert "wrapper" not in kinds(results)


def test_same_fact_gets_the_same_evidence_id(ctx: ToolContext) -> None:
    first = call(ctx, "get_total_quantity", plm_id=SCREW_L155)["evidence"]
    assert call(ctx, "get_total_quantity", plm_id=SCREW_L155_WRAPPER)["evidence"] == first


@pytest.mark.parametrize(
    ("name", "arguments", "message"),
    [
        ("get_product", '{"plm_id": "prd-nope"}', "search_products"),
        ("get_product", "{}", "Missing required"),
        ("get_product", '{"plm_id": "x", "depth": 2}', "Unknown argument"),
        ("get_product", "{not json", "not valid JSON"),
        ("get_product", "[1]", "JSON object"),
        ("drop_table", "{}", "Unknown tool"),
        ("search_products", '{"query": "screw", "kind": "wrapper"}', "kind"),
        ("search_products", '{"query": "screw", "limit": 0}', "limit"),
        ("aggregate_leaves", '{"group_by": "colour", "metric": "mass_kg"}', "group_by"),
        ("get_supplier_dependencies", '{"supplier_id": "SUP-999"}', "SUP-999"),
    ],
)
def test_bad_calls_return_an_error(ctx: ToolContext, name: str, arguments: str, message: str) -> None:
    assert message in execute(ctx, name, arguments)["error"]


def test_null_optional_arguments_are_ignored(ctx: ToolContext) -> None:
    result = execute(ctx, "search_products", '{"query": "screw", "kind": null, "limit": null}')
    assert result["total"] > 0


def test_compare_totals_answer_sums_without_model_arithmetic(ctx: ToolContext, plm_data: PlmData) -> None:
    d10 = [p["plm_id"] for p in call(ctx, "search_products", query="screw d10", kind="part")["products"]]
    totals = call(ctx, "compare_products", plm_ids=d10)["totals"]
    assert totals["total_quantity_in_motor"] == sum(plm_data.plm.total_quantity(i).value for i in d10)
    assert totals["product_count"] == 2 and "warning" not in totals
    assert totals["evidence"] in ctx.registry


def test_compare_totals_warn_when_one_product_contains_another(ctx: ToolContext, plm_data: PlmData) -> None:
    totals = call(ctx, "compare_products", plm_ids=[REDUCER, SIDE_SHAFT])["totals"]
    assert "twice" in totals["warning"]


def test_cost_share_matches_the_aggregate(ctx: ToolContext, plm_data: PlmData) -> None:
    stator = next(p for p in plm_data.plm.products() if p.name == "ECAR-EI_STATOR")
    share = call(ctx, "get_unit_cost", plm_id=stator.plm_id)["share_of_motor_material_cost_pct"]
    groups = call(ctx, "aggregate_leaves", group_by="assembly", metric="material_cost_eur", top=30)["groups"]
    assert share == next(g["share_of_total_pct"] for g in groups if g["id"] == stator.plm_id)


def test_mass_share_of_a_subassembly(ctx: ToolContext, plm_data: PlmData) -> None:
    card = call(ctx, "get_product", plm_id=REDUCER)
    expected = 100 * plm_data.plm.product(REDUCER).mass_kg / plm_data.plm.root().mass_kg
    assert card["share_of_motor_mass_pct"] == pytest.approx(expected, abs=0.05)
    assert "share_of_motor_mass_pct" not in call(ctx, "get_product", plm_id=plm_data.plm.root().plm_id)
