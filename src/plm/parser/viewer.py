"""3D view of a dataset, rebuilt from the extract on every load.

Positions come from the XML: each leaf occurrence composes the RelativeTransformation3D of its instances from the root
(column-major rotations, metres). Shapes come from illustrative profiles (geometry_reference.json), matched by stable
PLM ID and moved with the occurrence; they are not the OEM CAD. A part without a profile is drawn as a marker at its
XML origin. The profile file is read as data and type-checked before use.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from plm.contract import PlmRepository
from plm.model import Product

PROFILES_FILE = "geometry_reference.json"
MASS_TOLERANCE_KG = 0.05
CheckStatus = Literal["ok", "check"]
SHAPES = frozenset(
    {
        "bearing", "shaft", "gear", "bevel", "sidegear", "pin", "jacket", "stator", "hairpins", "onecoil", "tooth",
        "diffcase", "bolt", "elbow", "bearingseat", "seal", "plate", "bracket", "motorcase", "gearcase",
    }
)  # fmt: skip
OUTLINE_SHAPES = frozenset({"gearcase"})
PLACES = frozenset({"xml", "estimated"})
AXES = (("d12", "ID_30", "ID_38"), ("d23", "ID_38", "ID_34"))
MAX_ITEMS = 10_000
MAX_DEPTH = 100
MAX_PROBLEMS = 20
SAME_POSITION_M = 1e-8
_DISPLAY_PREFIX = re.compile(r"^(?:[A-Za-z0-9]+-)?3D[_ ]?")

Matrix = list[list[float]]
Vector = list[float]
IDENTITY: Matrix = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


class ViewerError(ValueError):
    """The profile file cannot be read, or the extract cannot be drawn."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Invalid 3D view:\n" + "\n".join(f"- {p}" for p in problems))


@dataclass(frozen=True)
class ViewerCheck:
    status: CheckStatus
    text: str


@dataclass(frozen=True)
class Profiles:
    items: tuple[dict[str, Any], ...]
    block_product_ids: dict[str, str]
    file: str

    def by_plm(self) -> dict[str, list[dict[str, Any]]]:
        refs: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in self.items:
            refs[item["plm"]].append(item)
        return refs

    def axis_bearings(self) -> list[tuple[str, str, str]]:
        """The bearings whose origins give the shaft centre distances, as plm_ids."""
        plm_of = {item["pid"]: item["plm"] for item in self.items if isinstance(item.get("pid"), str)}
        return [(label, plm_of[a], plm_of[b]) for label, a, b in AXES if a in plm_of and b in plm_of]


@dataclass(frozen=True)
class ViewerData:
    data: dict[str, Any]
    profiles_file: str | None

    @property
    def items(self) -> list[dict[str, Any]]:
        return self.data["items"]

    @property
    def plm_ids(self) -> frozenset[str]:
        return frozenset(item["plm"] for item in self.items)

    @property
    def placed_from_xml(self) -> int:
        return sum(1 for item in self.items if item["place"] == "xml")

    @property
    def markers(self) -> frozenset[str]:
        """Parts without a profile, drawn as markers."""
        return frozenset(item["plm"] for item in self.items if item["shape"]["t"] == "generic")

    @property
    def changed(self) -> frozenset[str]:
        """Parts whose profile was made for another position, mass or material."""
        return frozenset(item["plm"] for item in self.items if item["changed"])

    def covers(self, plm_id: str) -> bool:
        return plm_id in self.plm_ids

    def checks(self, root_mass_kg: float) -> list[ViewerCheck]:
        """Consistency of the 3D view with the extract: passed checks and points to check."""
        data, items = self.data, self.items
        total, n_parts = data["total_mass"], data["n_parts"]
        result = [
            ViewerCheck("ok", f"{n_parts} parts, {data['n_occ']} occurrences after expanding all instances"),
            ViewerCheck(
                "ok" if abs(total - root_mass_kg) <= MASS_TOLERANCE_KG else "check",
                f"Sum of part masses x occurrences = {total:.1f} kg, root mass in the XML {root_mass_kg:.1f} kg",
            ),
        ]
        markers = len(self.markers)
        if self.profiles_file is None:
            result.append(
                ViewerCheck("check", f"No shape profiles ({PROFILES_FILE}): every part is a marker at its XML origin")
            )
        elif markers:
            result.append(
                ViewerCheck(
                    "check",
                    f"{n_parts - markers} of {n_parts} parts drawn from a shape profile, "
                    f"{markers} without a profile shown as markers at their XML origin",
                )
            )
        else:
            result.append(ViewerCheck("ok", f"All {n_parts} parts drawn from a shape profile matched by PLM ID"))
        if self.changed:
            result.append(
                ViewerCheck(
                    "check",
                    f"{len(self.changed)} part(s) differ from their profile in position, mass or material: "
                    "the shape is moved to the XML position, its dimensions are not regenerated",
                )
            )
        axes = data.get("axes") or {}
        if "d12" in axes and "d23" in axes:
            result.append(
                ViewerCheck(
                    "ok",
                    "3 parallel shaft axes read from the bearing positions; centre distances "
                    f"{axes['d12'] * 1000:.1f} mm and {axes['d23'] * 1000:.1f} mm",
                )
            )
        for dup in data["dups"]:
            instances = " and ".join(dup["instances"])
            result.append(ViewerCheck("check", f"{dup['name']}: instances {instances} at the same position"))
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


def _is_matrix(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 3 and all(_is_vector(row) for row in value)


def _numeric(value: Any) -> bool:
    """A number, a flag, or nested lists of numbers (profiles, outlines)."""
    if isinstance(value, list):
        return all(_numeric(v) for v in value)
    return isinstance(value, bool) or _is_number(value)


def _check_profile(index: int, item: Any, problems: list[str]) -> None:
    where = f"profile {index}"
    if not isinstance(item, dict):
        problems.append(f"{where} is not an object")
        return
    for field in ("plm", "material"):
        if not isinstance(item.get(field), str):
            problems.append(f"{where}: {field} must be text")
    path = item.get("occurrence_path")
    if not isinstance(path, list) or not all(isinstance(p, str) for p in path):
        problems.append(f"{where}: occurrence_path must be a list of XML ids")
    if not _is_vector(item.get("pos")):
        problems.append(f"{where}: pos must be a 3D vector")
    if not _is_matrix(item.get("source_rotation")):
        problems.append(f"{where}: source_rotation must be a 3x3 matrix")
    if not _is_number(item.get("mass")):
        problems.append(f"{where}: mass must be a number")
    if item.get("place") not in PLACES:
        problems.append(f"{where}: place must be one of {sorted(PLACES)}")
    if not isinstance(item.get("artefact"), bool):
        problems.append(f"{where}: artefact must be true or false")
    shape = item.get("shape")
    if not isinstance(shape, dict) or shape.get("t") not in SHAPES:
        problems.append(f"{where}: shape.t must be one of {sorted(SHAPES)}")
        return
    if not all(_numeric(v) for k, v in shape.items() if k != "t"):
        problems.append(f"{where}: shape values must be numbers")
    if shape["t"] not in OUTLINE_SHAPES and not (_is_vector(shape.get("pos")) and _is_vector(shape.get("ax"))):
        problems.append(f"{where}: shape.pos and shape.ax must be 3D vectors")


def load_profiles(path: Path) -> Profiles:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ViewerError([f"{path.name} is not valid JSON: {exc}"]) from None
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ViewerError([f"{path.name} must be an object with an items list"])
    problems: list[str] = []
    for index, item in enumerate(data["items"]):
        _check_profile(index, item, problems)
    blocks = data.get("block_product_ids", {})
    if not isinstance(blocks, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in blocks.items()):
        problems.append("block_product_ids must map block names to plm_ids")
    if problems:
        extra = [f"and {len(problems) - MAX_PROBLEMS} more"] if len(problems) > MAX_PROBLEMS else []
        raise ViewerError(problems[:MAX_PROBLEMS] + extra)
    return Profiles(items=tuple(data["items"]), block_product_ids=blocks, file=path.name)


def _mv(a: Matrix, v: Vector) -> Vector:
    return [sum(a[i][j] * v[j] for j in range(3)) for i in range(3)]


def _mm(a: Matrix, b: Matrix) -> Matrix:
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _transpose(a: Matrix) -> Matrix:
    return [list(row) for row in zip(*a, strict=True)]


def display_name(name: str) -> str:
    return _DISPLAY_PREFIX.sub("", name).replace("_", " ").strip() or name


def _shape(template: dict[str, Any] | None, rotation: Matrix, position: Vector) -> dict[str, Any]:
    """The profile shape moved from its reference occurrence to this one, or a marker at the XML origin."""
    if template is None:
        return {"t": "generic", "pos": position, "ax": [0.0, 1.0, 0.0]}
    shape = json.loads(json.dumps(template["shape"]))
    delta = _mm(rotation, _transpose(template["source_rotation"]))
    if shape["t"] in OUTLINE_SHAPES:
        shape.pop("pos", None)
        shape["transform_delta"] = {
            "R": delta,
            "T": [a - b for a, b in zip(position, _mv(delta, template["pos"]), strict=True)],
        }
        return shape
    offset = [a - b for a, b in zip(shape["pos"], template["pos"], strict=True)]
    shape["pos"] = [a + b for a, b in zip(_mv(delta, offset), position, strict=True)]
    shape["ax"] = _mv(delta, shape["ax"])
    return shape


def _template(candidates: list[dict[str, Any]], path: list[str], position: Vector) -> dict[str, Any] | None:
    if not candidates:
        return None
    same_path = next((c for c in candidates if c["occurrence_path"] == path), None)
    return same_path or min(candidates, key=lambda c: math.dist(c["pos"], position))


def build_view(plm: PlmRepository, profiles_path: Path | None) -> ViewerData:
    """The 3D scene of the extract: one item per leaf occurrence, placed from the XML transforms."""
    profiles = load_profiles(profiles_path) if profiles_path is not None and Path(profiles_path).is_file() else None
    refs = profiles.by_plm() if profiles else {}
    block_of = {plm_id: block for block, plm_id in profiles.block_product_ids.items()} if profiles else {}
    root = plm.root()
    items: list[dict[str, Any]] = []

    def leaf(product: Product, parent: Product | None, rotation: Matrix, position: Vector, path: list[str], block):
        template = _template(refs.get(product.plm_id, []), path, position)
        changed = template is not None and (
            math.dist(template["pos"], position) > SAME_POSITION_M
            or abs(template["mass"] - product.mass_kg) > 1e-9
            or template["material"] != (product.material or "")
        )
        items.append(
            {
                "k": len(items),
                "pid": product.source.xml_id or product.plm_id,
                "plm": product.plm_id,
                "xid": product.xid,
                "name": display_name(product.name),
                "block": block,
                "rev": product.revision,
                "maturity": product.maturity,
                "mass": product.mass_kg,
                "material": product.material or "",
                "desc": product.description or "",
                "ei": parent.name if parent is not None and parent.item_kind == "wrapper" else None,
                "pline": product.source.line,
                "inst": path[-2] if len(path) > 1 else (path[-1] if path else ""),
                "pos": position,
                "place": template["place"] if template else "xml",
                "artefact": bool(template and template["artefact"]),
                "changed": changed,
                "shape": _shape(template, rotation, position),
            }
        )
        if len(items) > MAX_ITEMS:
            raise ViewerError([f"the 3D view draws up to {MAX_ITEMS} leaf occurrences"])

    def walk(product: Product, parent, rotation: Matrix, position: Vector, path: list[str], block, depth: int):
        if depth > MAX_DEPTH:
            raise ViewerError([f"the assembly is deeper than {MAX_DEPTH} levels"])
        lines = plm.children(product.plm_id)
        if not lines:
            leaf(product, parent, rotation, position, path, block or display_name(product.name))
            return
        for line in lines:
            child = plm.product(line.child_plm_id)
            child_block = block or block_of.get(child.plm_id) or display_name(child.name)
            for inst in plm.instances(product.plm_id, child.plm_id):
                r = inst.rotation
                local: Matrix = [[r[j * 3 + i] for j in range(3)] for i in range(3)]
                moved = [a + b for a, b in zip(_mv(rotation, list(inst.translation)), position, strict=True)]
                walk(
                    child,
                    product,
                    _mm(rotation, local),
                    moved,
                    [*path, inst.source.xml_id or inst.xid],
                    child_block,
                    depth + 1,
                )

    walk(root, None, IDENTITY, [0.0, 0.0, 0.0], [], None, 0)

    counts = Counter(item["pid"] for item in items)
    coincident: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        item["qty"] = counts[item["pid"]]
        coincident[(item["pid"], *(round(v, 9) for v in item["pos"]))].append(item)
    dups = []
    for members in coincident.values():
        if len(members) > 1:
            for item in members:
                item["dup"] = True
            dups.append({"name": members[0]["name"], "instances": [m["inst"] for m in members]})
    for item in items:
        item.setdefault("dup", False)

    first = {item["plm"]: item for item in reversed(items)}
    axes = {
        label: math.dist([first[a]["pos"][i] for i in (0, 2)], [first[b]["pos"][i] for i in (0, 2)])
        for label, a, b in (profiles.axis_bearings() if profiles else [])
        if a in first and b in first
    }
    data = {
        "items": items,
        "total_mass": math.fsum(item["mass"] for item in items),
        "n_occ": len(items),
        "n_parts": len(counts),
        "root": {
            "name": display_name(root.name),
            "rev": root.revision,
            "maturity": root.maturity,
            "mass": root.mass_kg,
        },
        "axes": axes,
        "dups": dups,
    }
    return ViewerData(data=data, profiles_file=profiles.file if profiles else None)
