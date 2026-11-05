"""3D view data of a dataset: the `const DATA = {...}` block of an exported viewer, checked against the extract.

The viewer file is read as data only. Its JSON is parsed and every field is type-checked, so that the app can draw it
in its own template (app/assets/viewer_3d_template.html) without running any code from the uploaded file.
A viewer whose parts are not in the extract was built from another extract and is refused.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from plm.contract import PlmRepository

VIEWER_FILE = "viewer_3d.html"
MASS_TOLERANCE_KG = 0.05  # the viewer rounds masses to 0.1 kg
CheckStatus = Literal["ok", "check"]
SHAPES = frozenset({"ring", "tube", "cyl", "cone", "gear", "box", "screw", "windings", "elbow", "shell"})
MAX_PROBLEMS = 20

_DATA_START = re.compile(r"\bconst\s+DATA\s*=\s*")
_TEXT = (str,)
_OPTIONAL_TEXT = (str, type(None))
ITEM_FIELDS: dict[str, tuple[type, ...]] = {
    "pid": _TEXT,
    "name": _TEXT,
    "block": _TEXT,
    "rev": _OPTIONAL_TEXT,
    "material": _TEXT,
    "plm": _TEXT,
    "xid": _OPTIONAL_TEXT,
    "chain": _TEXT,
    "place": _TEXT,
    "note": _OPTIONAL_TEXT,
    "ei": _OPTIONAL_TEXT,
    "desc": _OPTIONAL_TEXT,
    "artefact": (bool,),
    "dup": (bool,),
}


class ViewerError(ValueError):
    """The 3D view file cannot be read, or does not describe the loaded extract."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Invalid 3D view:\n" + "\n".join(f"- {p}" for p in problems))


@dataclass(frozen=True)
class ViewerCheck:
    status: CheckStatus
    text: str


@dataclass(frozen=True)
class ViewerData:
    data: dict[str, Any]
    file: str
    warnings: tuple[str, ...]

    @property
    def items(self) -> list[dict[str, Any]]:
        return self.data["items"]

    @property
    def plm_ids(self) -> frozenset[str]:
        return frozenset(item["plm"] for item in self.items)

    @property
    def placed_from_xml(self) -> int:
        return sum(1 for item in self.items if item["place"] == "xml")

    def covers(self, plm_id: str) -> bool:
        return plm_id in self.plm_ids

    def checks(self, root_mass_kg: float) -> list[ViewerCheck]:
        """Consistency of the 3D view with the extract: passed checks and points to check."""
        data, items = self.data, self.items
        total = data["total_mass"]
        result = [
            ViewerCheck("ok", f"{data['n_parts']} parts, {data['n_occ']} occurrences after expanding all instances"),
            ViewerCheck(
                "ok" if abs(total - root_mass_kg) <= MASS_TOLERANCE_KG else "check",
                f"Sum of part masses x occurrences = {total:.1f} kg, root mass in the XML {root_mass_kg:.1f} kg",
            ),
        ]
        axes = data.get("axes")
        if isinstance(axes, dict) and _is_number(axes.get("d12")) and _is_number(axes.get("d23")):
            result.append(
                ViewerCheck(
                    "ok",
                    "3 parallel shaft axes read from the bearing positions; centre distances "
                    f"{axes['d12'] * 1000:.1f} mm and {axes['d23'] * 1000:.1f} mm",
                )
            )
        for dup in data.get("dups") or []:
            if isinstance(dup, dict):
                instances = " and ".join(str(i) for i in dup.get("instances") or [])
                name = str(dup.get("name", "")).replace("3D_", "")
                result.append(ViewerCheck("check", f"{name}: instances {instances} at the same position"))
        artefacts = [item for item in items if item["artefact"]]
        if artefacts:
            result.append(
                ViewerCheck(
                    "check",
                    f"{len(artefacts)} part(s) modelled for meshing are counted in their parent mass "
                    f"({sum(item['mass'] for item in artefacts):.2f} kg)",
                )
            )
        return result


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _is_vector(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 3 and all(_is_number(v) for v in value)


def _numbers_only(value: Any) -> bool:
    """A number, or nested lists of numbers (shape sizes, outlines)."""
    if isinstance(value, list):
        return all(_numbers_only(v) for v in value)
    return _is_number(value)


def extract(html: str) -> dict[str, Any]:
    """The JSON object assigned to `const DATA` in the viewer file."""
    match = _DATA_START.search(html)
    if match is None:
        raise ViewerError(["no `const DATA = {...}` block found: the file is not an exported 3D view"])
    try:
        data, _ = json.JSONDecoder().raw_decode(html, match.end())
    except json.JSONDecodeError as exc:
        raise ViewerError([f"the DATA block is not valid JSON: {exc}"]) from None
    if not isinstance(data, dict):
        raise ViewerError(["the DATA block is not a JSON object"])
    return data


def _check_item(index: int, item: Any, problems: list[str]) -> None:
    where = f"item {index}"
    if not isinstance(item, dict):
        problems.append(f"{where} is not an object")
        return
    for field, kinds in ITEM_FIELDS.items():
        if not isinstance(item.get(field), kinds):
            problems.append(f"{where}: {field} is missing or has the wrong type")
    if not _is_number(item.get("mass")) or item["mass"] < 0:
        problems.append(f"{where}: mass must be a number >= 0")
    if not isinstance(item.get("pline"), int) or isinstance(item.get("pline"), bool):
        problems.append(f"{where}: pline must be an integer")
    if not _is_vector(item.get("pos")):
        problems.append(f"{where}: pos must be a 3D vector")
    shape = item.get("shape")
    if not isinstance(shape, dict) or shape.get("t") not in SHAPES:
        problems.append(f"{where}: shape.t must be one of {sorted(SHAPES)}")
        return
    if any(not _numbers_only(v) for k, v in shape.items() if k != "t"):
        problems.append(f"{where}: shape values must be numbers")
    if shape["t"] != "shell" and not (_is_vector(shape.get("pos")) and _is_vector(shape.get("ax"))):
        problems.append(f"{where}: shape.pos and shape.ax must be 3D vectors")


def check(data: dict[str, Any], plm: PlmRepository) -> tuple[list[str], list[str]]:
    """Problems that refuse the file, and warnings that do not."""
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return ["DATA.items is missing or empty"], []
    problems: list[str] = []
    for key in ("n_parts", "n_occ", "total_mass"):
        if not _is_number(data.get(key)):
            problems.append(f"DATA.{key} must be a number")
    for index, item in enumerate(items):
        _check_item(index, item, problems)
    if problems:
        return problems, []

    products = {p.plm_id: p for p in plm.products()}
    drawn = Counter(item["plm"] for item in items)
    unknown = sorted(set(drawn) - set(products))
    if unknown:
        problems.append(
            f"{len(unknown)} part(s) of the 3D view are not in the PLM extract (for instance {unknown[0]}): "
            "this 3D view was built from another extract"
        )
    assemblies = sorted(p for p in drawn if p in products and not products[p].is_leaf)
    if assemblies:
        problems.append(f"{len(assemblies)} item(s) are assemblies, the 3D view draws leaf parts only: {assemblies[0]}")
    if problems:
        return problems, []

    warnings = []
    missing = [p for p in plm.leaves() if p.plm_id not in drawn]
    if missing:
        warnings.append(f"{len(missing)} leaf part(s) of the extract are not drawn, for instance {missing[0].name}")
    mismatched = [p for p, n in drawn.items() if n != plm.total_quantity(p).value]
    if mismatched:
        warnings.append(f"{len(mismatched)} part(s) are drawn a different number of times than their quantity")
    return problems, warnings


def load_viewer(path: Path, plm: PlmRepository) -> ViewerData:
    path = Path(path)
    data = extract(path.read_text(encoding="utf-8"))
    problems, warnings = check(data, plm)
    if problems:
        raise ViewerError(
            problems[:MAX_PROBLEMS]
            + ([f"and {len(problems) - MAX_PROBLEMS} more"] if len(problems) > MAX_PROBLEMS else [])
        )
    return ViewerData(data=data, file=path.name, warnings=tuple(warnings))
