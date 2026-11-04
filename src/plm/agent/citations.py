"""Citation check on the model's final answer.

Accepts `[E3]`, `【E3】`, grouped forms such as `[E3, E5]` and ranges such as `[E3–E5]`, and rewrites them as
`[E3][E4][E5]`. A range is only expanded when every id in it exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from plm.agent.evidence import EvidenceRegistry

_ITEM = r"E\d+(?:\s*[-\u2010-\u2014]\s*E?\d+)?"
_GROUP = re.compile(rf"[\[【]\s*({_ITEM}(?:\s*[,;]\s*{_ITEM})*)\s*[\]】]")
_SEPARATOR = re.compile(r"\s*[,;]\s*")
_RANGE = re.compile(r"E(\d+)\s*[-\u2010-\u2014]\s*E?(\d+)")
_CITATION = re.compile(r"\[E\d+\]")
_NUMBER = re.compile(r"(?<![\w.\-])\d+(?:[.,]\d+)?(?![\w\-]|[.,]\d)")
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}")
_INVISIBLE = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")


@dataclass(frozen=True, slots=True)
class CheckedAnswer:
    text: str
    cited: tuple[str, ...]
    warnings: tuple[str, ...]
    uncited_figures: int = 0


def check_citations(text: str, registry: EvidenceRegistry) -> CheckedAnswer:
    """Zero-width characters are removed first: gpt-oss sometimes writes `[\u200bE7]`."""
    text = _INVISIBLE.sub("", text)
    cited: dict[str, None] = {}
    unknown: dict[str, None] = {}

    def rewrite(match: re.Match[str]) -> str:
        ids = [i for item in _SEPARATOR.split(match.group(1)) for i in _expand(item, registry)]
        known = [i for i in ids if i in registry]
        unknown.update(dict.fromkeys(i for i in ids if i not in registry))
        cited.update(dict.fromkeys(known))
        return "".join(f"[{i}]" for i in known)

    rewritten = _GROUP.sub(rewrite, text)
    warnings = []
    if unknown:
        warnings.append(f"Removed citation(s) to unknown evidence: {', '.join(unknown)}")
    if uncited := _uncited_segments(rewritten):
        warnings.append(f"{uncited} sentence(s) state a figure without a citation")
    return CheckedAnswer(text=rewritten, cited=tuple(cited), warnings=tuple(warnings), uncited_figures=uncited)


def _uncited_segments(text: str) -> int:
    count = 0
    for line in text.splitlines():
        if _TABLE_RULE.match(line):
            continue
        body = _LIST_MARKER.sub("", line)
        for segment in [body] if "|" in body else _SENTENCE_END.split(body):
            if _NUMBER.search(_CITATION.sub("", segment)) and not _CITATION.search(segment):
                count += 1
    return count


def _expand(item: str, registry: EvidenceRegistry) -> list[str]:
    bounds = _RANGE.fullmatch(item)
    if bounds is None:
        return [item]
    first, last = int(bounds[1]), int(bounds[2])
    ids = [f"E{n}" for n in range(first, last + 1)]
    return ids if first <= last and all(i in registry for i in ids) else [item]
