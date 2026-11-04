"""Recorded answers for the demo, so the pitch does not depend on the network or on the free-tier rate limit.

Each record carries a fingerprint of the data files it was computed from. A record whose fingerprint differs from
the current data is ignored: a replayed answer can never contradict the extract or the enrichment files.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from plm.agent.agent import AgentAnswer, Conversation, PlmAgent, ToolTrace
from plm.agent.config import AgentConfig, ReplayMode
from plm.agent.evidence import Evidence
from plm.agent.llm import LlmError, Usage
from plm.model import SourceRef
from plm.parser import DEFAULT_ENRICHMENT_DIR, DEFAULT_XML_PATH, load

FORMAT_VERSION = 1
_CITATION = re.compile(r"\[(E\d+)\]")
_TRAILING = re.compile(r"[\s?!.;:]+$")


class ReplayMissError(LlmError):
    """Replay-only mode and no valid recording for the question."""


def normalize_question(question: str) -> str:
    text = " ".join(question.replace("’", "'").split()).casefold()
    return _TRAILING.sub("", text)


def data_fingerprint(xml_path: Path, enrichment_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in [Path(xml_path), *sorted(Path(enrichment_dir).glob("*.csv"))]:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def answer_to_dict(answer: AgentAnswer) -> dict[str, Any]:
    return {
        "text": answer.text,
        "evidence": [
            {
                "id": e.id,
                "label": e.label,
                "plm_id": e.plm_id,
                "note": e.note,
                "refs": [dataclasses.asdict(ref) for ref in e.refs],
            }
            for e in answer.evidence
        ],
        "tool_calls": [dataclasses.asdict(t) for t in answer.tool_calls],
        "warnings": list(answer.warnings),
        "usage": dataclasses.asdict(answer.usage),
        "rejected_text": answer.rejected_text,
    }


def answer_from_dict(data: dict[str, Any], replayed_at: str | None = None) -> AgentAnswer:
    return AgentAnswer(
        text=data["text"],
        evidence=tuple(
            Evidence(
                id=e["id"],
                label=e["label"],
                refs=tuple(SourceRef(**ref) for ref in e["refs"]),
                plm_id=e["plm_id"],
                note=e["note"],
            )
            for e in data["evidence"]
        ),
        tool_calls=tuple(ToolTrace(**t) for t in data["tool_calls"]),
        warnings=tuple(data["warnings"]),
        usage=Usage(**data["usage"]),
        replayed_at=replayed_at,
        rejected_text=data.get("rejected_text"),
    )


def adopt(conversation: Conversation, question: str, answer: AgentAnswer) -> AgentAnswer:
    """Register a recorded answer's evidence in this conversation and renumber its citations accordingly."""
    new_ids = {
        e.id: conversation.registry.register(e.label, e.refs, plm_id=e.plm_id, note=e.note) for e in answer.evidence
    }
    text = _CITATION.sub(lambda m: f"[{new_ids[m[1]]}]" if m[1] in new_ids else "", answer.text)
    conversation.history += [{"role": "user", "content": question}, {"role": "assistant", "content": text}]
    return dataclasses.replace(
        answer,
        text=text,
        evidence=tuple(conversation.registry.get(new_ids[e.id]) for e in answer.evidence),
    )


class ReplayStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._records: dict[str, dict[str, Any]] = {}
        if self.path.is_file():
            content = json.loads(self.path.read_text(encoding="utf-8"))
            if content.get("version") != FORMAT_VERSION:
                raise ValueError(f"{self.path}: unsupported replay format {content.get('version')!r}")
            self._records = content["records"]

    def __len__(self) -> int:
        return len(self._records)

    def get(self, question: str, fingerprint: str) -> AgentAnswer | None:
        record = self._records.get(normalize_question(question))
        if record is None or record["fingerprint"] != fingerprint:
            return None
        return answer_from_dict(record["answer"], replayed_at=record["recorded_at"])

    def put(self, question: str, answer: AgentAnswer, fingerprint: str, model: str) -> None:
        self._records[normalize_question(question)] = {
            "question": question,
            "model": model,
            "fingerprint": fingerprint,
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "answer": answer_to_dict(answer),
        }

    def stale_count(self, fingerprint: str) -> int:
        return sum(record["fingerprint"] != fingerprint for record in self._records.values())

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = {"version": FORMAT_VERSION, "records": dict(sorted(self._records.items()))}
        self.path.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class ReplayingAgent:
    """Same interface as `PlmAgent.ask`. `agent` may be None in replay-only mode (no API key needed)."""

    def __init__(
        self,
        agent: PlmAgent | None,
        config: AgentConfig,
        store: ReplayStore,
        fingerprint: str,
        mode: ReplayMode | None = None,
    ) -> None:
        self.mode = mode or config.replay
        if agent is None and self.mode != "only":
            raise ValueError(f"A live agent is required in replay mode {self.mode!r}")
        self.config = config
        self.store = store
        self.fingerprint = fingerprint
        self._agent = agent

    def new_conversation(self) -> Conversation:
        return Conversation()

    def ask(self, conversation: Conversation, question: str) -> AgentAnswer:
        recorded = self.store.get(question, self.fingerprint) if self.mode != "off" else None
        if self.mode == "only":
            if recorded is None:
                raise ReplayMissError(
                    "Replay-only mode: no recorded answer for this question with the current data. "
                    "Record it with `python -m plm.agent.eval --record`, or set PLM_AGENT_REPLAY=fallback."
                )
            return adopt(conversation, question, recorded)
        if recorded is not None:
            return adopt(conversation, question, recorded)
        return self._agent.ask(conversation, question)


def build_assistant(
    config: AgentConfig,
    xml_path: Path = DEFAULT_XML_PATH,
    enrichment_dir: Path = DEFAULT_ENRICHMENT_DIR,
) -> PlmAgent | ReplayingAgent:
    """The live agent, wrapped in a ReplayingAgent unless PLM_AGENT_REPLAY is off."""
    data = load(xml_path, enrichment_dir)
    agent = PlmAgent(data.plm, data.enrichment, config) if config.replay != "only" else None
    if config.replay == "off":
        return agent
    return ReplayingAgent(agent, config, ReplayStore(config.replay_path), data_fingerprint(xml_path, enrichment_dir))
