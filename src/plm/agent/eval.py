"""Golden-question evaluation of the assistant against the live model.

    python -m plm.agent.eval                    # all questions
    python -m plm.agent.eval --only top_supplier gear_wheel
    python -m plm.agent.eval --record           # also store the passing answers for the replay mode

Expected values are computed from the current data with the same tools the model uses, so the evaluation stays
valid when the team edits the enrichment files. It needs the network and an API key; it is not part of pytest.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from plm.agent.agent import SCOPE_MESSAGES, AgentAnswer, PlmAgent
from plm.agent.config import AgentConfig, AgentConfigError
from plm.agent.llm import LlmError
from plm.agent.replay import ReplayStore, data_fingerprint
from plm.agent.tools import ToolContext, execute
from plm.parser import DEFAULT_ENRICHMENT_DIR, DEFAULT_XML_PATH, PlmData, load

PASS_THRESHOLD = 0.8

QUESTIONS: dict[str, str] = {
    "subassemblies": "What are the major subassemblies of the motor?",
    "motor_cost": "What is the material cost of the motor?",
    "screw_l155": "How many D10 L155 screws are in the motor?",
    "screw_d10_total": "How many D10 screws are there in the motor, all lengths combined?",
    "single_source": "Which parts are single-sourced?",
    "top_supplier": "Which supplier do we depend on the most?",
    "heaviest_materials": "Which three materials weigh the most in the motor?",
    "side_shaft_change": "If the differential side shaft is replaced, what is impacted?",
    "gear_wheel": "Tell me about the gear wheel",
    "rotor_magnet": "How many rotor magnets are in the motor?",
    "stator_cost_share": "Quelle part du coût matière vient du stator ?",
    "reducer_mass": "Quelle est la masse du sous-ensemble REDUCER FRONT ?",
    "off_topic_fr": "Donne moi une recette de tarte aux pommes",
    "off_topic_en": "What is the capital of France?",
}
DEMO_IDS = (
    "subassemblies",
    "top_supplier",
    "single_source",
    "side_shaft_change",
    "stator_cost_share",
    "gear_wheel",
    "rotor_magnet",
    "off_topic_fr",
)
DEMO_QUESTIONS = tuple(QUESTIONS[i] for i in DEMO_IDS)

Check = Callable[[AgentAnswer], str | None]

_SPACES_AND_DASHES = str.maketrans({**{c: "-" for c in "‐‑‒–—−"}, **{c: " " for c in "   _"}})
_NUMBER = re.compile(r"(?<![\w.\-])\d+(?:[.,]\d+)?(?![\w\-]|[.,]\d)")
_PREFIX = re.compile(r"^\w+-(?:ei|3d)\s+")


def normalize(text: str) -> str:
    """Casefold, typographic dashes to '-', underscores and special spaces to plain spaces."""
    return " ".join(text.translate(_SPACES_AND_DASHES).split()).casefold()


def numbers_in(text: str) -> list[float]:
    return [float(n.replace(",", ".")) for n in _NUMBER.findall(normalize(text))]


def core_name(name: str) -> str:
    """'ECAR-EI_REDUCER FRONT' -> 'reducer front', the part a model keeps when it rewrites a name."""
    return _PREFIX.sub("", normalize(name))


def has_numbers(*expected: float) -> Check:
    def check(answer: AgentAnswer) -> str | None:
        found = numbers_in(answer.text)
        missing = [v for v in expected if not any(math.isclose(f, v, rel_tol=0.01, abs_tol=0.01) for f in found)]
        return f"missing figure(s) {missing}" if missing else None

    return check


def lacks_numbers(*forbidden: float) -> Check:
    def check(answer: AgentAnswer) -> str | None:
        found = [v for v in forbidden if any(math.isclose(f, v, abs_tol=1e-9) for f in numbers_in(answer.text))]
        return f"wrong figure(s) {found}" if found else None

    return check


def mentions(*texts: str) -> Check:
    def check(answer: AgentAnswer) -> str | None:
        text = normalize(answer.text)
        missing = [t for t in texts if normalize(t) not in text]
        return f"does not mention {missing}" if missing else None

    return check


def used_tool(*names: str) -> Check:
    def check(answer: AgentAnswer) -> str | None:
        return None if any(t.name in names for t in answer.tool_calls) else f"none of {list(names)} was called"

    return check


def grounded(answer: AgentAnswer) -> str | None:
    if any("not grounded" in w for w in answer.warnings):
        return "replaced by the scope message"
    return None if answer.evidence else "no evidence cited"


def not_refused(answer: AgentAnswer) -> str | None:
    return "replaced by the scope message" if answer.text in SCOPE_MESSAGES.values() else None


def refused(answer: AgentAnswer) -> str | None:
    if answer.text not in SCOPE_MESSAGES.values():
        return "off-topic request was answered"
    return "tools were called for an off-topic request" if answer.tool_calls else None


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    checks: tuple[Check, ...]

    @property
    def question(self) -> str:
        return QUESTIONS[self.id]


def golden_cases(data: PlmData) -> list[Case]:
    ctx = ToolContext.build(data.plm, data.enrichment)
    plm = data.plm

    def tool(name: str, **arguments: Any) -> dict[str, Any]:
        result = execute(ctx, name, json.dumps(arguments))
        if "error" in result:
            raise RuntimeError(f"Cannot compute the expected value with {name}: {result['error']}")
        return result

    root = plm.root()
    subassemblies = [plm.product(line.child_plm_id).name for line in plm.children(root.plm_id)]
    screw_l155 = tool("search_products", query="ECAR-3D Screw D10 L155")["products"][0]["plm_id"]
    d10_parts = tool("search_products", query="screw d10", kind="part")["products"]
    d10_total = sum(tool("get_total_quantity", plm_id=p["plm_id"])["total_quantity_in_motor"] for p in d10_parts)
    dependencies = tool("get_supplier_dependencies")
    top_supplier = dependencies["suppliers"][0]
    materials = tool("aggregate_leaves", group_by="material", metric="mass_kg", top=3)["groups"]
    side_shaft = tool("search_products", query="DifferentialSideShaft", kind="part")["products"][0]["plm_id"]
    impacted = tool("get_where_used", plm_id=side_shaft)["impacted_assemblies"]
    side_sourcing = tool("get_sourcing", plm_id=side_shaft)["suppliers"]
    gear_ids = [p["plm_id"] for p in tool("search_products", query="gearwheel", kind="part")["products"]]
    by_assembly = tool("aggregate_leaves", group_by="assembly", metric="material_cost_eur", top=30)["groups"]
    stator = next(g for g in by_assembly if "stator" in normalize(g["key"]))
    reducer = next(p for p in plm.products() if core_name(p.name) == "reducer front")

    return [
        Case("subassemblies", (grounded, mentions(*(core_name(n) for n in subassemblies)))),
        Case(
            "motor_cost",
            (
                grounded,
                used_tool("get_unit_cost", "aggregate_leaves"),
                has_numbers(round(ctx.costs.unit_cost(root.plm_id).value, 2)),
            ),
        ),
        Case(
            "screw_l155",
            (
                grounded,
                used_tool("get_total_quantity", "get_product", "compare_products"),
                has_numbers(plm.total_quantity(screw_l155).value),
            ),
        ),
        Case("screw_d10_total", (grounded, has_numbers(d10_total), lacks_numbers(2 * d10_total))),
        Case(
            "single_source",
            (grounded, used_tool("get_supplier_dependencies"), has_numbers(dependencies["single_source_part_count"])),
        ),
        Case(
            "top_supplier",
            (
                grounded,
                used_tool("get_supplier_dependencies"),
                mentions(top_supplier["name"].split(" (")[0]),
                has_numbers(top_supplier["part_count"]),
            ),
        ),
        Case("heaviest_materials", (grounded, has_numbers(*(g["value"] for g in materials)))),
        Case(
            "side_shaft_change",
            (
                grounded,
                used_tool("get_where_used"),
                mentions(*(core_name(a["name"]) for a in impacted if a["plm_id"] != root.plm_id)),
                mentions(*(s["name"].split(" (")[0] for s in side_sourcing)),
                has_numbers(*(s["lead_time_days"] for s in side_sourcing if s["lead_time_days"] is not None)),
            ),
        ),
        Case("gear_wheel", (grounded, mentions(*gear_ids))),
        Case("rotor_magnet", (not_refused, used_tool("search_products"))),
        Case("stator_cost_share", (grounded, has_numbers(stator["value"], stator["share_of_total_pct"]))),
        Case("reducer_mass", (grounded, has_numbers(reducer.mass_kg))),
        Case("off_topic_fr", (refused,)),
        Case("off_topic_en", (refused,)),
    ]


@dataclass(slots=True)
class CaseResult:
    case: Case
    answer: AgentAnswer | None
    seconds: float
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def evaluate(case: Case, answer: AgentAnswer) -> list[str]:
    return [failure for check in case.checks if (failure := check(answer))]


def run_case(agent: PlmAgent, case: Case) -> CaseResult:
    started = time.perf_counter()
    try:
        answer = agent.ask(agent.new_conversation(), case.question)
    except LlmError as exc:
        return CaseResult(case, None, time.perf_counter() - started, [f"LLM error: {exc}"])
    return CaseResult(case, answer, time.perf_counter() - started, evaluate(case, answer))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="+", choices=list(QUESTIONS), help="Run these question ids only")
    parser.add_argument("--record", action="store_true", help="Store the passing answers in the replay file")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print every answer, not only the failing ones")
    args = parser.parse_args(argv)

    try:
        config = AgentConfig.from_env()
    except AgentConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not config.api_key:
        print("error: the evaluation needs PLM_LLM_API_KEY (replay-only mode has no live model)", file=sys.stderr)
        return 2

    data = load()
    agent = PlmAgent(data.plm, data.enrichment, config)
    cases = [c for c in golden_cases(data) if not args.only or c.id in args.only]
    results = []
    for case in cases:
        result = run_case(agent, case)
        results.append(result)
        tokens = (
            f"{result.answer.usage.prompt_tokens}+{result.answer.usage.completion_tokens}" if result.answer else "-"
        )
        print(f"{'PASS' if result.passed else 'FAIL'}  {case.id:<20} {result.seconds:5.1f}s  {tokens:>11} tokens")
        for failure in result.failures:
            print(f"      - {failure}")
        if result.answer and (args.verbose or not result.passed):
            print("      " + result.answer.text.strip().replace("\n", "\n      "))
            if result.answer.rejected_text:
                print(
                    "      Rejected model answer:\n      "
                    + result.answer.rejected_text.strip().replace("\n", "\n      ")
                )

    passed = sum(r.passed for r in results)
    print(f"\n{passed}/{len(results)} passed ({passed / len(results):.0%})")

    if args.record:
        fingerprint = data_fingerprint(DEFAULT_XML_PATH, DEFAULT_ENRICHMENT_DIR)
        store = ReplayStore(config.replay_path)
        for result in results:
            if result.passed and result.answer is not None:
                store.put(result.case.question, result.answer, fingerprint, config.model)
        store.save()
        print(
            f"Recorded {passed} answer(s) in {store.path} ({len(store)} in total, {store.stale_count(fingerprint)} stale)"
        )
    return 0 if passed / len(results) >= PASS_THRESHOLD else 1


if __name__ == "__main__":
    sys.exit(main())
