from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from plm.agent.agent import AgentAnswer, Conversation, ToolTrace
from plm.agent.config import AgentConfig, AgentConfigError
from plm.agent.evidence import Evidence
from plm.agent.llm import Usage
from plm.agent.replay import (
    ReplayingAgent,
    ReplayMissError,
    ReplayStore,
    adopt,
    answer_from_dict,
    answer_to_dict,
    data_fingerprint,
    normalize_question,
)
from plm.model import SourceRef

XML_REF = SourceRef("plm_xml", "XPDMXML_ECAR_PLM.xml", 78, "Product", "ID_3", "abc")
CSV_REF = SourceRef("enrichment", "part_sourcing.csv", 5)
QUESTION = "How many screws?"


def recorded_answer() -> AgentAnswer:
    return AgentAnswer(
        text="14 screws [E7], from SKF [E9].",
        evidence=(
            Evidence("E7", "Total quantity of screw", (XML_REF,), "prd-1"),
            Evidence("E9", "Sourcing of screw", (CSV_REF,), "prd-1", "catalogue"),
        ),
        tool_calls=(ToolTrace("get_total_quantity", '{"plm_id": "prd-1"}', 0.001, None),),
        warnings=("1 sentence(s) state a figure without a citation",),
        usage=Usage(1200, 80),
    )


class FakeAgent:
    def __init__(self) -> None:
        self.questions: list[str] = []

    def ask(self, conversation: Conversation, question: str) -> AgentAnswer:
        self.questions.append(question)
        return AgentAnswer("live", (), (), (), Usage(1, 1))


def test_answer_round_trip() -> None:
    answer = recorded_answer()
    assert answer_from_dict(answer_to_dict(answer)) == answer
    assert answer_from_dict(answer_to_dict(answer), replayed_at="2026-11-04").replayed_at == "2026-11-04"


def test_adopt_renumbers_evidence_in_the_new_conversation() -> None:
    conversation = Conversation()
    conversation.registry.register("something earlier", (CSV_REF,))

    adopted = adopt(conversation, QUESTION, recorded_answer())

    assert adopted.text == "14 screws [E2], from SKF [E3]."
    assert [e.id for e in adopted.evidence] == ["E2", "E3"]
    assert conversation.registry.get("E3").note == "catalogue"
    assert conversation.history[-1] == {"role": "assistant", "content": adopted.text}


@pytest.mark.parametrize(
    "variant",
    ["How many screws?", "  how   many SCREWS ", "How many screws ?!", "How many screws."],
)
def test_question_normalisation(variant: str) -> None:
    assert normalize_question(variant) == normalize_question(QUESTION)


def test_quote_variants_match() -> None:
    assert normalize_question("Qu’est-ce que le stator ?") == normalize_question("Qu'est-ce que le stator")


def test_store_round_trip_and_stale_records(tmp_path: Path) -> None:
    path = tmp_path / "replays" / "answers.json"
    store = ReplayStore(path)
    store.put(QUESTION, recorded_answer(), fingerprint="v1", model="m")
    store.save()

    reloaded = ReplayStore(path)
    replayed = reloaded.get("how many screws", "v1")
    assert replayed is not None and replayed.text == recorded_answer().text and replayed.replayed_at
    assert reloaded.get(QUESTION, "v2") is None
    assert reloaded.stale_count("v2") == 1 and reloaded.stale_count("v1") == 0


def test_store_rejects_unknown_format(tmp_path: Path) -> None:
    path = tmp_path / "answers.json"
    path.write_text('{"version": 99, "records": {}}', encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported replay format"):
        ReplayStore(path)


def test_fingerprint_follows_the_data_files(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    xml = repo / "data" / "raw" / "XPDMXML_ECAR_PLM.xml"
    if not xml.is_file():
        pytest.skip("OEM extract not available")
    enrichment = tmp_path / "enrichment"
    shutil.copytree(repo / "data" / "enrichment", enrichment)

    before = data_fingerprint(xml, enrichment)
    assert data_fingerprint(xml, enrichment) == before
    with (enrichment / "suppliers.csv").open("a", encoding="utf-8") as csv:
        csv.write("\n")
    assert data_fingerprint(xml, enrichment) != before


@pytest.fixture
def store(tmp_path: Path) -> ReplayStore:
    store = ReplayStore(tmp_path / "answers.json")
    store.put(QUESTION, recorded_answer(), fingerprint="v1", model="m")
    return store


def test_fallback_replays_recorded_questions_and_asks_the_others_live(store: ReplayStore) -> None:
    live = FakeAgent()
    agent = ReplayingAgent(live, AgentConfig(api_key="k", replay="fallback"), store, "v1")
    conversation = agent.new_conversation()

    replayed = agent.ask(conversation, QUESTION)
    fresh = agent.ask(conversation, "Something else?")

    assert replayed.replayed_at and replayed.text.startswith("14 screws [E1]")
    assert fresh.text == "live" and live.questions == ["Something else?"]


def test_fallback_goes_live_when_the_recording_is_stale(store: ReplayStore) -> None:
    live = FakeAgent()
    agent = ReplayingAgent(live, AgentConfig(api_key="k", replay="fallback"), store, "v2")
    assert agent.ask(Conversation(), QUESTION).text == "live"


def test_off_mode_never_replays(store: ReplayStore) -> None:
    live = FakeAgent()
    agent = ReplayingAgent(live, AgentConfig(api_key="k"), store, "v1", mode="off")
    assert agent.ask(Conversation(), QUESTION).text == "live"


def test_only_mode_needs_no_live_agent(store: ReplayStore) -> None:
    agent = ReplayingAgent(None, AgentConfig(api_key="", replay="only"), store, "v1")
    assert agent.ask(Conversation(), QUESTION).replayed_at
    with pytest.raises(ReplayMissError, match="--record"):
        agent.ask(Conversation(), "Not recorded?")


def test_live_modes_require_an_agent(store: ReplayStore) -> None:
    with pytest.raises(ValueError, match="live agent"):
        ReplayingAgent(None, AgentConfig(api_key="k", replay="fallback"), store, "v1")


def test_config_replay_only_does_not_need_a_key() -> None:
    config = AgentConfig.from_env(env={"PLM_AGENT_REPLAY": "only"})
    assert config.replay == "only" and config.api_key == ""
    with pytest.raises(AgentConfigError, match="PLM_LLM_API_KEY"):
        AgentConfig.from_env(env={"PLM_AGENT_REPLAY": "fallback"})
    with pytest.raises(AgentConfigError, match="PLM_AGENT_REPLAY"):
        AgentConfig.from_env(env={"PLM_LLM_API_KEY": "k", "PLM_AGENT_REPLAY": "sometimes"})
