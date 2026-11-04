from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from plm.agent.agent import FORCE_ANSWER, SCOPE_MESSAGES, Conversation, PlmAgent, is_grounded
from plm.agent.config import AgentConfig
from plm.agent.llm import LlmMessage, LlmToolCallError, Message, ToolCall, Usage
from plm.parser import PlmData

SCREW_L155 = "prd-12535825-00000117"

Step = Callable[[list[Message]], LlmMessage]


def tool(name: str, **arguments: object) -> Step:
    return lambda messages: LlmMessage(
        None, (ToolCall(f"c{len(messages)}", name, json.dumps(arguments)),), Usage(10, 2)
    )


def say(template: str) -> Step:
    """Final answer; `{evidence}` is replaced by the `evidence` id of the last tool result."""

    def step(messages: list[Message]) -> LlmMessage:
        last = json.loads(messages[-1]["content"]) if messages[-1]["role"] == "tool" else {}
        return LlmMessage(template.format(evidence=last.get("evidence", "")), (), Usage(10, 5))

    return step


def fail(exc: Exception) -> Step:
    def step(messages: list[Message]) -> LlmMessage:
        raise exc

    return step


class ScriptedLlm:
    def __init__(self, *steps: Step) -> None:
        self.steps = list(steps)
        self.calls: list[dict] = []

    def complete(self, messages, tools=None, tool_choice="auto") -> LlmMessage:
        self.calls.append({"messages": list(messages), "tool_choice": tool_choice})
        if not self.steps:
            raise AssertionError("The agent called the LLM more often than scripted")
        return self.steps.pop(0)(messages)


def make_agent(plm_data: PlmData, llm: ScriptedLlm, max_steps: int = 6) -> PlmAgent:
    return PlmAgent(plm_data.plm, plm_data.enrichment, AgentConfig(api_key="k", max_steps=max_steps), client=llm)


def test_search_then_quantity_then_cited_answer(plm_data: PlmData) -> None:
    llm = ScriptedLlm(
        tool("search_products", query="D10 L155"),
        tool("get_total_quantity", plm_id=SCREW_L155),
        say("There are 14 screws [{evidence}]."),
    )
    agent = make_agent(plm_data, llm)
    conversation = agent.new_conversation()

    answer = agent.ask(conversation, "How many D10 L155 screws?")

    assert answer.text == "There are 14 screws [E2]."
    assert [e.label for e in answer.evidence] == ["Total quantity of ECAR-3D Screw D10 L155 in the motor"]
    assert len(answer.evidence[0].refs) == 17
    assert [t.name for t in answer.tool_calls] == ["search_products", "get_total_quantity"]
    assert answer.warnings == ()
    assert (answer.usage.prompt_tokens, answer.usage.completion_tokens) == (30, 9)
    assert [m["role"] for m in conversation.history] == ["user", "assistant"]


def test_system_prompt_holds_rules_and_overview(plm_data: PlmData) -> None:
    llm = ScriptedLlm(say("Hello."))
    make_agent(plm_data, llm).ask(Conversation(), "Hi")
    system = llm.calls[0]["messages"][0]
    assert system["role"] == "system"
    assert "[E3]" in system["content"] and plm_data.plm.root().plm_id in system["content"]
    assert "ECAR-3D_GearWheel (x4" in system["content"]


def test_tool_errors_go_back_to_the_model(plm_data: PlmData) -> None:
    llm = ScriptedLlm(tool("get_product", plm_id="prd-nope"), say("Unknown product."))
    answer = make_agent(plm_data, llm).ask(Conversation(), "?")
    assert answer.tool_calls[0].error is not None
    tool_message = llm.calls[1]["messages"][-1]
    assert tool_message["role"] == "tool" and "error" in json.loads(tool_message["content"])


def test_step_limit_forces_a_final_answer_without_tools(plm_data: PlmData) -> None:
    llm = ScriptedLlm(
        tool("search_products", query="magnet"),
        tool("search_products", query="rotor"),
        say("The extract contains no rotor magnet."),
    )
    answer = make_agent(plm_data, llm, max_steps=2).ask(Conversation(), "Magnets?")
    assert answer.text == "The extract contains no rotor magnet."
    assert llm.calls[-1]["tool_choice"] == "none"
    assert llm.calls[-1]["messages"][-1]["content"] == FORCE_ANSWER
    assert any("Stopped after 2 steps" in w for w in answer.warnings)


def test_malformed_tool_call_is_retried_once(plm_data: PlmData) -> None:
    llm = ScriptedLlm(
        fail(LlmToolCallError("tool 'search_results' not in request.tools")),
        tool("search_products", query="magnet"),
        say("The extract contains no magnet."),
    )
    answer = make_agent(plm_data, llm).ask(Conversation(), "?")
    assert answer.text == "The extract contains no magnet."
    assert "search_products" in llm.calls[1]["messages"][-1]["content"]
    assert any("malformed tool call" in w for w in answer.warnings)


def test_second_malformed_tool_call_propagates(plm_data: PlmData) -> None:
    llm = ScriptedLlm(fail(LlmToolCallError("bad")), fail(LlmToolCallError("bad again")))
    with pytest.raises(LlmToolCallError, match="bad again"):
        make_agent(plm_data, llm).ask(Conversation(), "?")


def test_invented_citations_are_removed(plm_data: PlmData) -> None:
    llm = ScriptedLlm(
        tool("get_total_quantity", plm_id=SCREW_L155),
        say("There are 14 screws [{evidence}][E42]."),
    )
    answer = make_agent(plm_data, llm).ask(Conversation(), "?")
    assert answer.text == "There are 14 screws [E2]." and [e.id for e in answer.evidence] == ["E2"]
    assert any("E42" in w for w in answer.warnings)


def test_answer_citing_only_invented_evidence_is_replaced(plm_data: PlmData) -> None:
    llm = ScriptedLlm(say("There are 14 screws [E42]."))
    answer = make_agent(plm_data, llm).ask(Conversation(), "How many screws?")
    assert answer.text == SCOPE_MESSAGES["en"] and answer.evidence == ()


@pytest.mark.parametrize(
    ("question", "language"),
    [
        ("Donne moi une recette de tarte aux pommes", "fr"),
        ("Écris un poème", "fr"),
        ("Give me an apple pie recipe", "en"),
        ("What is the capital of France?", "en"),
    ],
)
def test_off_topic_answer_without_data_is_replaced(plm_data: PlmData, question: str, language: str) -> None:
    llm = ScriptedLlm(say("Ingredients: 80 g of sugar, 4 apples, 30 g of butter. Bake 35 minutes at 180 C."))
    conversation = Conversation()
    answer = make_agent(plm_data, llm).ask(conversation, question)
    assert answer.text == SCOPE_MESSAGES[language] and answer.evidence == () and answer.tool_calls == ()
    assert any("not grounded" in w for w in answer.warnings)
    assert conversation.history[-1]["content"] == SCOPE_MESSAGES[language]


def test_off_topic_answer_after_a_tool_call_is_replaced(plm_data: PlmData) -> None:
    llm = ScriptedLlm(tool("search_products", query="apple"), say("Use 4 apples and 80 g of sugar."))
    answer = make_agent(plm_data, llm).ask(Conversation(), "Apple pie?")
    assert answer.text == SCOPE_MESSAGES["en"]


def test_follow_up_citing_earlier_evidence_is_kept(plm_data: PlmData) -> None:
    llm = ScriptedLlm(
        tool("get_total_quantity", plm_id=SCREW_L155),
        say("14 screws [{evidence}]."),
        say("As said, 14 screws [E2]."),
    )
    agent = make_agent(plm_data, llm)
    conversation = agent.new_conversation()
    agent.ask(conversation, "How many screws?")
    answer = agent.ask(conversation, "Can you repeat?")
    assert answer.text == "As said, 14 screws [E2]." and answer.tool_calls == ()


@pytest.mark.parametrize(
    ("cited", "used_tools", "uncited_figures", "expected"),
    [
        (("E1",), False, 3, True),
        ((), True, 0, True),
        ((), True, 1, False),
        ((), False, 0, False),
    ],
)
def test_is_grounded(cited: tuple[str, ...], used_tools: bool, uncited_figures: int, expected: bool) -> None:
    assert is_grounded(cited, used_tools, uncited_figures) is expected


def test_evidence_ids_are_stable_across_turns(plm_data: PlmData) -> None:
    llm = ScriptedLlm(
        tool("get_total_quantity", plm_id=SCREW_L155),
        say("14 [{evidence}]."),
        tool("get_total_quantity", plm_id=SCREW_L155),
        say("Still 14 [{evidence}], the part itself is [E1]."),
    )
    agent = make_agent(plm_data, llm)
    conversation = agent.new_conversation()

    first = agent.ask(conversation, "How many?")
    second = agent.ask(conversation, "Sure?")

    assert first.evidence[0].id == second.evidence[0].id == "E2"
    assert [e.id for e in second.evidence] == ["E2", "E1"] and second.warnings == ()
    assert len(conversation.history) == 4
    second_turn = llm.calls[2]["messages"]
    assert [m["role"] for m in second_turn] == ["system", "user", "assistant", "user"]
