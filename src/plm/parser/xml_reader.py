"""XPDMXML reader implementing PlmRepository. Field-level mapping: docs/xml_mapping.md."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from lxml import etree

from plm.contract import UnknownProductError
from plm.model import (
    BomLine,
    ExportInfo,
    Instance,
    ItemKind,
    Occurrence,
    Product,
    Representation,
    SourceRef,
    Traced,
)

logger = logging.getLogger(__name__)

NS = "http://www.3ds.com/xsd/XPDMXML"
MASS_TAG = "WCGEquivalentComputedExt.V_WCG_Mass"
MASS_TOLERANCE_KG = 1e-3
KNOWN_OBJECTS = {"Product", "ProductInst", "Rep3DAggr"}

_NAME_PREFIX = re.compile(r"^(\w+-(?:EI|3D))[_ ]+")


class PlmParseError(ValueError):
    """A required XML element or value is missing or malformed."""


class PlmValidationError(ValueError):
    """The extract is readable but structurally inconsistent."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Invalid PLM extract:\n" + "\n".join(f"- {p}" for p in problems))


def normalize_name(name: str) -> str:
    return _NAME_PREFIX.sub(r"\1_", " ".join(name.split()))


def _search_key(text: str) -> str:
    """Case-insensitive form where underscores and runs of whitespace are all one space, applied to query and fields."""
    return " ".join(text.replace("_", " ").split()).casefold()


def _q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def _local(el: etree._Element) -> str:
    return etree.QName(el).localname


def _text(el: etree._Element, path: str) -> str:
    child = el.find("/".join(_q(part) for part in path.split("/")))
    if child is None:
        raise PlmParseError(f"line {el.sourceline}: <{_local(el)}> has no <{path}>")
    return (child.text or "").strip()


def _optional(el: etree._Element, path: str) -> str | None:
    return _text(el, path) or None


def _convert(el: etree._Element, path: str, converter: Any, label: str) -> Any:
    raw = _text(el, path)
    try:
        return converter(raw)
    except ValueError:
        raise PlmParseError(f"line {el.sourceline}: <{path}> is not a valid {label}: {raw!r}") from None


def _parse_datetime(raw: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise ValueError(raw)


def _parse_floats(count: int) -> Any:
    def parse(raw: str) -> tuple[float, ...]:
        values = tuple(float(v) for v in raw.split())
        if len(values) != count:
            raise ValueError(raw)
        return values

    return parse


def _xid(el: etree._Element) -> str:
    uid = el.find(_q("UniqueID"))
    xid = uid.get("XID") if uid is not None else None
    if not xid:
        raise PlmParseError(f"line {el.sourceline}: <{_local(el)}> has no UniqueID/@XID")
    return xid


def _xml_id(el: etree._Element) -> str:
    xml_id = el.get("id")
    if not xml_id:
        raise PlmParseError(f"line {el.sourceline}: <{_local(el)}> has no @id")
    return xml_id


def _source(file: str, el: etree._Element) -> SourceRef:
    return SourceRef(
        origin="plm_xml",
        file=file,
        line=el.sourceline,
        element=_local(el),
        xml_id=el.get("id"),
        xid=_xid(el) if el.find(_q("UniqueID")) is not None else None,
    )


def _check_unique(kind: str, field: str, values: list[tuple[str, int]], problems: list[str]) -> None:
    seen: dict[str, int] = {}
    for value, line in values:
        if value in seen:
            problems.append(f"{kind} {field} {value!r} appears twice (lines {seen[value]} and {line})")
        else:
            seen[value] = line


def _unique_sources(sources: Any) -> tuple[SourceRef, ...]:
    return tuple(dict.fromkeys(sources))


class XmlPlmRepository:
    def __init__(
        self,
        export_info: ExportInfo,
        products: list[Product],
        instances: list[Instance],
        representations: list[Representation],
        bom_lines: list[BomLine],
        occurrences: list[Occurrence],
        warnings: list[str],
    ) -> None:
        self._export_info = export_info
        self._products = {p.plm_id: p for p in products}
        self._instances = instances
        self._representations = {r.product_plm_id: r for r in representations}
        self._occurrences = occurrences
        self.warnings = tuple(warnings)

        self._children: dict[str, list[BomLine]] = defaultdict(list)
        self._parents: dict[str, list[BomLine]] = defaultdict(list)
        for line in bom_lines:
            self._children[line.parent_plm_id].append(line)
            self._parents[line.child_plm_id].append(line)
        for lines in self._children.values():
            lines.sort(key=lambda b: b.order)

        self._instances_by_pair: dict[tuple[str, str], list[Instance]] = defaultdict(list)
        for inst in instances:
            self._instances_by_pair[(inst.parent_plm_id, inst.child_plm_id)].append(inst)

        self._occurrences_by_product: dict[str, list[Occurrence]] = defaultdict(list)
        for occ in occurrences:
            self._occurrences_by_product[occ.plm_id].append(occ)

    @classmethod
    def from_file(cls, path: Path) -> XmlPlmRepository:
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"PLM extract not found: {path}")
        try:
            tree = etree.parse(str(path))
        except etree.XMLSyntaxError as exc:
            raise PlmParseError(f"{path.name} is not well-formed XML: {exc}") from exc
        return _Builder(path.name, tree.getroot()).build()

    @property
    def export_info(self) -> ExportInfo:
        return self._export_info

    def root(self) -> Product:
        return self._products[self._export_info.root_plm_id]

    def product(self, plm_id: str) -> Product:
        try:
            return self._products[plm_id]
        except KeyError:
            raise UnknownProductError(f"No product with plm_id {plm_id!r}") from None

    def products(self) -> list[Product]:
        return list(self._products.values())

    def leaves(self) -> list[Product]:
        return [p for p in self._products.values() if p.is_leaf]

    def search(self, text: str) -> list[Product]:
        needle = _search_key(text)
        return [
            p
            for p in self._products.values()
            if needle in _search_key(p.name_normalized)
            or needle in p.plm_id.casefold()
            or needle in _search_key(p.material or "")
        ]

    def instances(self, parent_plm_id: str, child_plm_id: str) -> list[Instance]:
        self.product(parent_plm_id)
        self.product(child_plm_id)
        return list(self._instances_by_pair.get((parent_plm_id, child_plm_id), []))

    def all_instances(self) -> list[Instance]:
        return list(self._instances)

    def children(self, plm_id: str) -> list[BomLine]:
        self.product(plm_id)
        return list(self._children.get(plm_id, []))

    def parents(self, plm_id: str) -> list[BomLine]:
        self.product(plm_id)
        return list(self._parents.get(plm_id, []))

    def occurrences(self) -> list[Occurrence]:
        return list(self._occurrences)

    def where_used(self, plm_id: str) -> list[Occurrence]:
        self.product(plm_id)
        return list(self._occurrences_by_product.get(plm_id, []))

    def total_quantity(self, plm_id: str) -> Traced[int]:
        occurrences = self.where_used(plm_id)
        return Traced(
            value=sum(o.cumulative_quantity for o in occurrences),
            sources=_unique_sources(s for o in occurrences for s in o.sources),
        )

    def representation(self, plm_id: str) -> Representation | None:
        self.product(plm_id)
        return self._representations.get(plm_id)


class _Builder:
    def __init__(self, file: str, root: etree._Element) -> None:
        self.file = file
        self.root = root
        self.problems: list[str] = []
        self.warnings: list[str] = []

    def build(self) -> XmlPlmRepository:
        if _local(self.root) != "XPDMXML" or etree.QName(self.root).namespace != NS:
            raise PlmParseError(f"{self.file}: root element is not {{{NS}}}XPDMXML")
        structure = self.root.find(_q("ProductStructure"))
        if structure is None:
            raise PlmParseError(f"{self.file}: no <ProductStructure>")

        product_els, instance_els, rep_els = self._split(structure)
        raw_products = [self._raw_product(el) for el in product_els]
        id_to_plm = {r["xml_id"]: r["plm_id"] for r in raw_products}

        _check_unique("Product", "@id", [(r["xml_id"], r["source"].line) for r in raw_products], self.problems)
        _check_unique("Product", "ID", [(r["plm_id"], r["source"].line) for r in raw_products], self.problems)
        _check_unique("Product", "XID", [(r["xid"], r["source"].line) for r in raw_products], self.problems)

        instances = [self._instance(el, id_to_plm) for el in instance_els]
        representations = [self._representation(el, id_to_plm) for el in rep_els]
        _check_unique("ProductInst", "XID", [(i.xid, i.source.line) for i in instances], self.problems)
        _check_unique("Rep3DAggr", "XID", [(r.xid, r.source.line) for r in representations], self.problems)
        _check_unique("Rep3DAggr", "owner", [(r.product_plm_id, r.source.line) for r in representations], self.problems)
        self._raise_if_problems()

        root_plm_id = self._root(structure, id_to_plm, instances)
        bom_lines = self._bom_lines(instances)
        children: dict[str, list[BomLine]] = defaultdict(list)
        for line in bom_lines:
            children[line.parent_plm_id].append(line)
        for lines in children.values():
            lines.sort(key=lambda b: b.order)
        self._check_cycles(id_to_plm.values(), children)
        self._raise_if_problems()

        products = [
            Product(
                **{k: v for k, v in raw.items() if k != "xml_id"},
                item_kind=self._item_kind(raw["plm_id"], children),
                is_root=raw["plm_id"] == root_plm_id,
                is_leaf=not children.get(raw["plm_id"]),
            )
            for raw in raw_products
        ]
        by_plm = {p.plm_id: p for p in products}
        occurrences = self._occurrences(by_plm[root_plm_id], children)
        self._check_masses(by_plm, children)
        export_info = self._export_info(by_plm[root_plm_id])

        for warning in self.warnings:
            logger.warning(warning)
        return XmlPlmRepository(
            export_info, products, instances, representations, bom_lines, occurrences, self.warnings
        )

    def _raise_if_problems(self) -> None:
        if self.problems:
            raise PlmValidationError(self.problems)

    def _split(self, structure: etree._Element) -> tuple[list, list, list]:
        groups: dict[str, list[etree._Element]] = defaultdict(list)
        for el in structure:
            if not isinstance(el.tag, str):
                continue
            groups[_local(el)].append(el)
        for tag, els in groups.items():
            if tag not in KNOWN_OBJECTS:
                self.warnings.append(f"Ignored {len(els)} <{tag}> element(s), first at line {els[0].sourceline}")
        return groups["Product"], groups["ProductInst"], groups["Rep3DAggr"]

    def _raw_product(self, el: etree._Element) -> dict[str, Any]:
        name = _text(el, "Name")
        return {
            "xml_id": _xml_id(el),
            "plm_id": _text(el, "ID"),
            "xid": _xid(el),
            "name": name,
            "name_normalized": normalize_name(name),
            "description": _optional(el, "Description"),
            "revision": _text(el, "RevisionName"),
            "revision_index": _convert(el, "RevisionIndex", int, "integer"),
            "revision_family": _text(el, "RevisionFamily"),
            "maturity": _text(el, "Maturity"),
            "lifecycle": _text(el, "Lifecycle"),
            "created_at": _convert(el, "TimeCreated", _parse_datetime, "datetime"),
            "modified_at": _convert(el, "TimeModified", _parse_datetime, "datetime"),
            "owner": _text(el, "Owner"),
            "organization": _text(el, "Organization"),
            "project": _text(el, "Project"),
            "mass_kg": _convert(el, MASS_TAG, float, "number"),
            "material": _optional(el, "Material"),
            "source": _source(self.file, el),
        }

    def _resolve(self, el: etree._Element, field: str, id_to_plm: dict[str, str]) -> str:
        ref = _text(el, field)
        if ref not in id_to_plm:
            self.problems.append(f"line {el.sourceline}: <{_local(el)}>/{field} points to unknown product {ref!r}")
            return ""
        return id_to_plm[ref]

    def _instance(self, el: etree._Element, id_to_plm: dict[str, str]) -> Instance:
        _xml_id(el)
        return Instance(
            xid=_xid(el),
            instance_name=_text(el, "ID"),
            parent_plm_id=self._resolve(el, "Owned", id_to_plm),
            child_plm_id=self._resolve(el, "Instancing", id_to_plm),
            child_order=_convert(el, "ChildOrder", float, "number"),
            rotation=_convert(el, "RelativeTransformation3D/Rotation3D", _parse_floats(9), "3x3 matrix"),
            translation=_convert(el, "RelativeTransformation3D/Translation3D", _parse_floats(3), "3D vector"),
            source=_source(self.file, el),
        )

    def _representation(self, el: etree._Element, id_to_plm: dict[str, str]) -> Representation:
        _xml_id(el)
        return Representation(
            plm_id=_text(el, "ID"),
            xid=_xid(el),
            name=_text(el, "Name"),
            product_plm_id=self._resolve(el, "Owned", id_to_plm),
            revision=_text(el, "RevisionName"),
            maturity=_text(el, "Maturity"),
            source=_source(self.file, el),
        )

    def _root(self, structure: etree._Element, id_to_plm: dict[str, str], instances: list[Instance]) -> str:
        refs = (structure.get("rootRefs") or "").split()
        if len(refs) != 1:
            raise PlmValidationError([f"<ProductStructure>/@rootRefs must name exactly one product, got {refs}"])
        if refs[0] not in id_to_plm:
            raise PlmValidationError([f"rootRefs points to unknown product {refs[0]!r}"])
        header_ref = self._header().get("SourceObjectRef")
        if header_ref is not None and header_ref != refs[0]:
            self.problems.append(f"InfoHeader SourceObjectRef {header_ref!r} differs from rootRefs {refs[0]!r}")

        root_plm_id = id_to_plm[refs[0]]
        instanced = {i.child_plm_id for i in instances}
        if root_plm_id in instanced:
            self.problems.append(f"Root product {root_plm_id} is instanced inside another product")
        orphans = sorted(set(id_to_plm.values()) - instanced - {root_plm_id})
        if orphans:
            self.problems.append(f"Products not reachable from the root: {orphans}")
        return root_plm_id

    def _header(self) -> dict[str, str]:
        header = self.root.find(_q("InfoHeader"))
        if header is None:
            return {}
        return {info.get("name"): info.get("value", "") for info in header.iterfind(_q("Info"))}

    def _export_info(self, root: Product) -> ExportInfo:
        header_el = self.root.find(_q("InfoHeader"))
        line = header_el.sourceline if header_el is not None else self.root.sourceline
        return ExportInfo(
            root_plm_id=root.plm_id,
            root_xid=root.xid,
            completion=self._header().get("CompletionType", ""),
            raw=self._header(),
            source=SourceRef(origin="plm_xml", file=self.file, line=line, element="InfoHeader"),
        )

    @staticmethod
    def _bom_lines(instances: list[Instance]) -> list[BomLine]:
        groups: dict[tuple[str, str], list[Instance]] = defaultdict(list)
        for inst in instances:
            groups[(inst.parent_plm_id, inst.child_plm_id)].append(inst)
        return [
            BomLine(
                parent_plm_id=parent,
                child_plm_id=child,
                quantity=len(group),
                order=min(i.child_order for i in group),
                instance_xids=tuple(i.xid for i in group),
                sources=tuple(i.source for i in group),
            )
            for (parent, child), group in groups.items()
        ]

    def _check_cycles(self, plm_ids: Any, children: dict[str, list[BomLine]]) -> None:
        state: dict[str, int] = {}

        def visit(node: str, path: list[str]) -> None:
            state[node] = 1
            for line in children.get(node, []):
                child = line.child_plm_id
                if state.get(child) == 1:
                    self.problems.append(f"Cycle in product structure: {' -> '.join([*path, child])}")
                elif child not in state:
                    visit(child, [*path, child])
            state[node] = 2

        for plm_id in plm_ids:
            if plm_id not in state:
                visit(plm_id, [plm_id])

    @staticmethod
    def _item_kind(plm_id: str, children: dict[str, list[BomLine]]) -> ItemKind:
        lines = children.get(plm_id, [])
        if not lines:
            return "part"
        if len(lines) == 1 and lines[0].quantity == 1 and not children.get(lines[0].child_plm_id):
            return "wrapper"
        return "assembly"

    @staticmethod
    def _occurrences(root: Product, children: dict[str, list[BomLine]]) -> list[Occurrence]:
        result: list[Occurrence] = []

        def walk(path: tuple[str, ...], quantity: int, sources: tuple[SourceRef, ...]) -> None:
            result.append(Occurrence(path=path, cumulative_quantity=quantity, sources=sources))
            for line in children.get(path[-1], []):
                walk((*path, line.child_plm_id), quantity * line.quantity, sources + line.sources)

        walk((root.plm_id,), 1, (root.source,))
        return result

    def _check_masses(self, products: dict[str, Product], children: dict[str, list[BomLine]]) -> None:
        for plm_id, lines in children.items():
            rolled = sum(line.quantity * products[line.child_plm_id].mass_kg for line in lines)
            own = products[plm_id].mass_kg
            if abs(rolled - own) > MASS_TOLERANCE_KG:
                self.warnings.append(
                    f"Mass of {products[plm_id].name} ({plm_id}) is {own} kg but its children sum to {rolled:.3f} kg"
                )
