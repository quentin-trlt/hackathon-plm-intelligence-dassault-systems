from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from plm.agent.config import DEFAULT_BASE_URL, DEFAULT_MODEL, AgentConfig, AgentConfigError
from plm.agent.llm import LlmMessage, Message, OpenAiCompatibleClient, ToolCall
from plm.agent.spike import QUESTIONS, Run, execute, run_question, verdict
from plm.parser import XmlPlmRepository

SCREW_L155 = "ECAR-3D Screw D10 L155"


def test_config_requires_api_key() -> None:
    with pytest.raises(AgentConfigError, match="PLM_LLM_API_KEY"):
        AgentConfig.from_env(env={})


def test_config_defaults_and_hidden_key() -> None:
    config = AgentConfig.from_env(env={"PLM_LLM_API_KEY": "secret-key"})
    assert (config.base_url, config.model, config.max_steps, config.temperature) == (
        DEFAULT_BASE_URL,
        DEFAULT_MODEL,
        6,
        0.0,
    )
    assert "secret-key" not in repr(config)


@pytest.mark.parametrize(
    ("name", "value"),
    [("PLM_AGENT_MAX_STEPS", "six"), ("PLM_AGENT_MAX_STEPS", "0"), ("PLM_AGENT_TEMPERATURE", "-1")],
)
def test_config_rejects_invalid_values(name: str, value: str) -> None:
    with pytest.raises(AgentConfigError, match=name):
        AgentConfig.from_env(env={"PLM_LLM_API_KEY": "k", name: value})


def test_config_reads_dotenv_file(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PLM_LLM_API_KEY", raising=False)
    monkeypatch.delenv("PLM_LLM_MODEL", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text("PLM_LLM_API_KEY=from-file\nPLM_LLM_MODEL=some/model\n", encoding="utf-8")
    config = AgentConfig.from_env(dotenv_path=dotenv)
    assert (config.api_key, config.model) == ("from-file", "some/model")


class _StubCompletions:
    def __init__(self, response: SimpleNamespace) -> None:
        self.response = response
        self.kwargs: dict = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def _stub_openai(message: SimpleNamespace) -> tuple[SimpleNamespace, _StubCompletions]:
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=15),
    )
    completions = _StubCompletions(response)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def test_client_converts_tool_calls() -> None:
    call = SimpleNamespace(
        id="call_1", type="function", function=SimpleNamespace(name="search_products", arguments='{"query":"screw"}')
    )
    stub, completions = _stub_openai(SimpleNamespace(content=None, tool_calls=[call]))
    client = OpenAiCompatibleClient(AgentConfig(api_key="k"), client=stub)

    reply = client.complete([{"role": "user", "content": "hi"}], tools=[{"type": "function"}])

    assert reply.tool_calls == (ToolCall("call_1", "search_products", '{"query":"screw"}'),)
    assert (reply.usage.prompt_tokens, reply.usage.completion_tokens) == (120, 15)
    assert completions.kwargs["tool_choice"] == "auto"
    assert reply.to_message()["tool_calls"][0]["function"]["name"] == "search_products"


def test_client_omits_tools_when_none() -> None:
    stub, completions = _stub_openai(SimpleNamespace(content="Hello", tool_calls=None))
    reply = OpenAiCompatibleClient(AgentConfig(api_key="k"), client=stub).complete([{"role": "user", "content": "hi"}])
    assert reply.content == "Hello" and reply.tool_calls == ()
    assert "tools" not in completions.kwargs


class _ScriptedLlm:
    """Plays the expected sequence for Q1: search, then total quantity, then answer from the tool result."""

    def __init__(self) -> None:
        self.step = 0

    def complete(self, messages: list[Message], tools: list[Message] | None = None) -> LlmMessage:
        self.step += 1
        if self.step == 1:
            return LlmMessage(None, (ToolCall("c1", "search_products", '{"query": "D10 L155"}'),))
        if self.step == 2:
            found = json.loads(messages[-1]["content"])["products"]
            part = next(p for p in found if p["kind"] == "part")
            return LlmMessage(None, (ToolCall("c2", "get_total_quantity", json.dumps({"plm_id": part["plm_id"]})),))
        quantity = json.loads(messages[-1]["content"])["total_quantity"]
        return LlmMessage(f"There are {quantity} screws.", ())


def test_spike_loop_on_real_extract(plm: XmlPlmRepository) -> None:
    run = run_question(_ScriptedLlm(), plm, QUESTIONS[0].text, max_steps=6)
    assert run.steps == 3 and run.error is None
    assert run.answer == "There are 14 screws."
    assert verdict(QUESTIONS[0], run) == "PASS"


def test_spike_loop_stops_at_step_limit(plm: XmlPlmRepository) -> None:
    class LoopingLlm:
        def complete(self, messages, tools=None):
            return LlmMessage(None, (ToolCall("c", "search_products", '{"query": "screw"}'),))

    run = run_question(LoopingLlm(), plm, "?", max_steps=3)
    assert run.steps == 3 and run.error is not None
    assert verdict(QUESTIONS[0], run) == "ERROR"


def test_tools_report_errors_instead_of_raising(plm: XmlPlmRepository) -> None:
    assert "error" in execute(plm, ToolCall("c", "get_total_quantity", '{"plm_id": "prd-nope"}'))
    assert "error" in execute(plm, ToolCall("c", "get_total_quantity", "{not json"))
    assert "error" in execute(plm, ToolCall("c", "get_total_quantity", '{"wrong": 1}'))
    assert "error" in execute(plm, ToolCall("c", "delete_everything", "{}"))
    assert execute(plm, ToolCall("c", "search_products", '{"query": "magnet"}'))["total"] == 0


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("There are 18 D10 screws [prd-1].", "PASS"), ("36 screws, 18 parts", "FAIL"), ("About 180 screws", "FAIL")],
)
def test_verdict_matches_whole_numbers(answer: str, expected: str) -> None:
    run = Run(answer=answer, calls=['get_total_quantity({"plm_id": "x"})'])
    assert verdict(QUESTIONS[2], run) == expected


def test_verdict_requires_the_tool_call() -> None:
    assert verdict(QUESTIONS[0], Run(answer="14 screws")) == "FAIL"


def test_screw_quantities_used_by_the_questions(plm: XmlPlmRepository) -> None:
    parts = {p.name: plm.total_quantity(p.plm_id).value for p in plm.search("screw d10") if p.item_kind == "part"}
    assert parts[SCREW_L155] == 14 and sum(parts.values()) == 18
