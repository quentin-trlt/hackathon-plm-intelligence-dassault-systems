"""Evidence the assistant can cite. One evidence groups every `SourceRef` behind one fact returned by a tool."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from plm.model import SourceRef


@dataclass(frozen=True, slots=True)
class Evidence:
    id: str
    label: str
    refs: tuple[SourceRef, ...]
    plm_id: str | None = None
    note: str | None = None

    @property
    def is_enrichment(self) -> bool:
        """True when part of the fact comes from the team's CSV files rather than from the OEM extract."""
        return any(ref.origin == "enrichment" for ref in self.refs)


def describe(ref: SourceRef) -> str:
    if ref.origin == "plm_xml":
        element = f' <{ref.element} id="{ref.xml_id}">' if ref.element else ""
        return f"{ref.file}, line {ref.line}{element}"
    return f"{ref.file}, line {ref.line} (non-PLM source)"


class EvidenceRegistry:
    """Belongs to one conversation, so an id cited in an earlier answer still resolves later."""

    def __init__(self) -> None:
        self._by_id: dict[str, Evidence] = {}
        self._by_key: dict[tuple[str, tuple[SourceRef, ...]], str] = {}

    def register(
        self,
        label: str,
        refs: Iterable[SourceRef],
        plm_id: str | None = None,
        note: str | None = None,
    ) -> str:
        unique = tuple(dict.fromkeys(refs))
        if not unique:
            raise ValueError(f"Evidence {label!r} has no source: every fact must trace back to a file")
        key = (label, unique)
        if key not in self._by_key:
            evidence_id = f"E{len(self._by_id) + 1}"
            self._by_id[evidence_id] = Evidence(evidence_id, label, unique, plm_id, note)
            self._by_key[key] = evidence_id
        return self._by_key[key]

    def get(self, evidence_id: str) -> Evidence | None:
        return self._by_id.get(evidence_id)

    def __contains__(self, evidence_id: object) -> bool:
        return evidence_id in self._by_id

    def __len__(self) -> int:
        return len(self._by_id)
