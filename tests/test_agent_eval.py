from __future__ import annotations

import json

import pytest

from plm.agent.agent import SCOPE_MESSAGES, AgentAnswer, Conversation, PlmAgent, ToolTrace
from plm.agent.config import AgentConfig
from plm.agent.eval import (
    DEMO_QUESTIONS,
    QUESTIONS,
    core_name,
    evaluate,
    golden_cases,
    grounded,
    has_numbers,
    lacks_numbers,
    mentions,
    numbers_in,
    refused,
    used_tool,
)
from plm.agent.evidence import Evidence
from plm.agent.llm import LlmMessage, ToolCall, Usage
from plm.model import SourceRef
from plm.parser import PlmData

REF = SourceRef("plm_xml", "x.xml", 1, "Product", "ID_1")


def answer(text: str, tools: tuple[str, ...] = (), cited: bool = True, warnings: tuple[str, ...] = ()) -> AgentAnswer:
    return AgentAnswer(
        text=text,
        evidence=(Evidence("E1", "fact", (REF,)),) if cited else (),
        tool_calls=tuple(ToolTrace(name, "{}", 0.0, None) for name in tools),
        warnings=warnings,
        usage=Usage(0, 0),
    )


def test_numbers_handle_french_decimals_and_ignore_identifiers() -> None:
    text = "Coût **127,37 €**, soit 47.8 % [E1]; vis D10 L155, prd‑12535825‑00000117, ECAR-3D, 410KW."
    assert numbers_in(text) == [127.37, 47.8]


def test_core_name_survives_typographic_rewrites() -> None:
    assert core_name("ECAR-EI_DIFFERENTIAL_ASSEMBLY") == "differential assembly"
    assert mentions(core_name("ECAR-EI_REDUCER FRONT"))(answer("the ECAR‑EI REDUCER FRONT assembly")) is None


def test_has_numbers_tolerance() -> None:
    assert has_numbers(266.68, 14)(answer("266.7 EUR and 14 units")) is None
    assert "missing" in has_numbers(266.68)(answer("about 250 EUR"))


def test_lacks_numbers() -> None:
    assert lacks_numbers(36)(answer("18 screws")) is None
    assert "wrong" in lacks_numbers(36)(answer("36 screws"))


def test_used_tool_grounded_and_refused() -> None:
    assert used_tool("a", "b")(answer("x", tools=("b",))) is None
    assert used_tool("a")(answer("x", tools=("b",)))
    assert grounded(answer("x [E1]")) is None
    assert grounded(answer("x", cited=False)) == "no evidence cited"
    assert refused(answer(SCOPE_MESSAGES["fr"], cited=False)) is None
    assert refused(answer("Voici une recette", cited=False)) == "off-topic request was answered"


def test_golden_cases_cover_every_question(plm_data: PlmData) -> None:
    cases = golden_cases(plm_data)
    assert [c.id for c in cases] == list(QUESTIONS)
    assert all(c.checks for c in cases)
    assert set(DEMO_QUESTIONS) <= set(QUESTIONS.values())
    assert not {QUESTIONS["off_topic_fr"], QUESTIONS["off_topic_en"]} & set(DEMO_QUESTIONS)


def test_golden_checks_accept_a_correct_answer_and_reject_a_wrong_one(plm_data: PlmData) -> None:
    case = next(c for c in golden_cases(plm_data) if c.id == "screw_d10_total")
    right = answer("18 D10 screws [E1]: 4 L115 and 14 L155.", tools=("get_total_quantity",))
    wrong = answer("36 D10 screws [E1].", tools=("get_total_quantity",))
    assert evaluate(case, right) == []
    assert evaluate(case, wrong) == ["missing figure(s) [18]", "wrong figure(s) [36]"]


@pytest.mark.parametrize("case_id", ["off_topic_fr", "off_topic_en"])
def test_off_topic_cases_require_the_scope_message(plm_data: PlmData, case_id: str) -> None:
    case = next(c for c in golden_cases(plm_data) if c.id == case_id)
    assert evaluate(case, answer(SCOPE_MESSAGES["en"], cited=False)) == []
    assert evaluate(case, answer("Paris.", cited=False)) == ["off-topic request was answered"]


ZWSP = chr(0x200B)
NB_HYPHEN = chr(0x2011)
REJECTED_ON_4_NOV = (
    (
        "The motor's major subassemblies are:\n\n"
        "| Subassembly (plm_id) | Mass (kg) |\n|---|---|\n"
        "| ECAR-EI EMOTOR FRONT (prd-12535825-00000048) | 39.73 kg |\n"
        "| ECAR-EI REDUCER FRONT (prd-12535825-00000049) | 29.85 kg |\n"
        "| ECAR-EI HOUSING FRONT EDRIVE (prd-12535825-00000050) | 30.52 kg |\n\n"
        "These three assemblies are the direct children of the root product ECAR-EI_410KW_EDRIVE_FRONT "
        "[@E7][@E1][@E3][@E5]."
    )
    .replace("-", NB_HYPHEN)
    .replace("@", ZWSP)
)


def test_answer_rejected_during_the_eval_is_now_kept_and_passes(plm_data: PlmData) -> None:
    class Scripted:
        def __init__(self) -> None:
            self.step = 0

        def complete(self, messages, tools=None, tool_choice="auto") -> LlmMessage:
            self.step += 1
            if self.step == 1:
                root = plm_data.plm.root().plm_id
                return LlmMessage(None, (ToolCall("c1", "get_children", json.dumps({"plm_id": root})),))
            return LlmMessage(REJECTED_ON_4_NOV, ())

    agent = PlmAgent(plm_data.plm, plm_data.enrichment, AgentConfig(api_key="k"), client=Scripted())
    answer = agent.ask(Conversation(), QUESTIONS["subassemblies"])
    case = next(c for c in golden_cases(plm_data) if c.id == "subassemblies")
    assert answer.rejected_text is None and [e.id for e in answer.evidence] == ["E7", "E1", "E3", "E5"]
    assert evaluate(case, answer) == []
