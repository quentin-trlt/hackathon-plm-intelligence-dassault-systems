from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from plm.contract import UnknownProductError
from plm.parser import PlmParseError, PlmValidationError, XmlPlmRepository, normalize_name

NS = "http://www.3ds.com/xsd/XPDMXML"


def test_counts(plm):
    assert len(plm.products()) == 79
    assert len(plm.all_instances()) == 112
    assert sum(1 for p in plm.products() if plm.representation(p.plm_id)) == 36
    assert sum(len(plm.children(p.plm_id)) for p in plm.products()) == 80
    assert len(plm.occurrences()) == 81
    assert plm.warnings == ()


def test_root(plm):
    root = plm.root()
    assert root.name == "ECAR-EI_410KW_EDRIVE_FRONT"
    assert root.plm_id == plm.export_info.root_plm_id == "prd-12535825-00000047"
    assert root.is_root and not plm.parents(root.plm_id)
    assert plm.export_info.completion == "Expand=-1"
    assert plm.occurrences()[0].path == (root.plm_id,)


def test_item_kinds(plm):
    kinds = Counter(p.item_kind for p in plm.products())
    assert kinds == {"assembly": 6, "wrapper": 37, "part": 36}
    assert {p.plm_id for p in plm.leaves()} == {p.plm_id for p in plm.products() if plm.representation(p.plm_id)}


def test_mass_rolls_up_to_root(plm):
    total = sum(p.mass_kg * plm.total_quantity(p.plm_id).value for p in plm.leaves())
    assert total == pytest.approx(100.1)
    assert plm.root().mass_kg == pytest.approx(100.1)


def test_quantities(plm, by_name):
    housing = by_name("ECAR-EI_HOUSING FRONT EDRIVE")[0]
    screw_ei = by_name("ECAR-EI_Screw D10 L155")[0]
    screw_3d = by_name("ECAR-3D Screw D10 L155")[0]
    line = next(b for b in plm.children(housing.plm_id) if b.child_plm_id == screw_ei.plm_id)
    assert line.quantity == 14
    assert len(line.instance_xids) == len(line.sources) == 14
    assert len(plm.instances(housing.plm_id, screw_ei.plm_id)) == 14
    assert plm.total_quantity(screw_3d.plm_id).value == 14


def test_children_sorted_by_order(plm):
    for product in plm.products():
        orders = [b.order for b in plm.children(product.plm_id)]
        assert orders == sorted(orders)


def test_gear_wheels_are_distinct_products(plm, by_name):
    wheels = by_name("ECAR-EI_GEAR_WHEEL")
    assert len({w.plm_id for w in wheels}) == 4
    assert sorted(w.revision for w in wheels) == ["A", "B", "C", "D"]
    assert sorted(w.mass_kg for w in wheels) == [1.3, 1.6, 3.9, 7.4]


def test_motor_screw_2_has_no_wrapper(plm, by_name):
    screw = by_name("ECAR-3D_MOTOR SCREW 2")[0]
    (parent_line,) = plm.parents(screw.plm_id)
    assert plm.product(parent_line.parent_plm_id).name == "ECAR-EI_HOUSING FRONT EDRIVE"
    assert parent_line.quantity == 2


def test_shared_part_where_used(plm, by_name):
    shaft = by_name("ECAR-3D_DifferentialSideShaft")[0]
    occurrences = plm.where_used(shaft.plm_id)
    assert len(occurrences) == 2
    assert len({o.path for o in occurrences}) == 2
    assert all(o.depth == 4 for o in occurrences)


def test_source_refs_point_to_xml(plm, by_name):
    wheel_a = next(w for w in by_name("ECAR-EI_GEAR_WHEEL") if w.revision == "A")
    assert wheel_a.source.origin == "plm_xml"
    assert wheel_a.source.element == "Product"
    assert wheel_a.source.xml_id == "ID_41"
    assert wheel_a.source.line == 800
    assert wheel_a.source.xid == wheel_a.xid


def test_optional_fields(plm):
    assert sum(p.description is not None for p in plm.products()) == 3
    assert sum(p.material is None for p in plm.products()) == 6
    assert all(p.material for p in plm.leaves())


def test_search(plm):
    names = {p.name for p in plm.search("machine cooling")}
    assert names == {"ECAR-EI_ MACHINE COOLING"}
    assert {p.name for p in plm.search("ecar-3d_stator core")} >= {"ECAR-3D Stator Core", "ECAR-3D_Stator Core Tooth"}
    assert len(plm.search("100Cr6")) == 14


@pytest.mark.parametrize("query", ["ECAR-3D Screw D10 L155", "ecar-3d screw d10 l155", "ECAR-3D_Screw  D10 L155"])
def test_search_finds_the_name_as_written_in_the_xml(plm, query):
    assert [p.name for p in plm.search(query)] == ["ECAR-3D Screw D10 L155"]


def test_search_treats_underscores_and_spaces_alike(plm):
    parts = {p.name for p in plm.search("3D Screw D10") if p.is_leaf}
    assert parts == {"ECAR-3D_Screw D10 L115", "ECAR-3D Screw D10 L155"}


def test_unknown_product(plm):
    with pytest.raises(UnknownProductError):
        plm.product("prd-unknown")
    with pytest.raises(UnknownProductError):
        plm.where_used("prd-unknown")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ECAR-EI_ MACHINE COOLING", "ECAR-EI_MACHINE COOLING"),
        ("ECAR-3D Stator Core", "ECAR-3D_Stator Core"),
        ("ECAR-EI_410KW_EDRIVE_FRONT", "ECAR-EI_410KW_EDRIVE_FRONT"),
        ("  ECAR-3D_Screw   D8 L50 ", "ECAR-3D_Screw D8 L50"),
    ],
)
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


def _product(xml_id: str, plm_id: str, mass: float, material: str = "") -> str:
    return f"""
    <Product id="{xml_id}">
      <ID>{plm_id}</ID><Name>{plm_id}</Name><Description></Description>
      <UniqueID External="X{xml_id}" XID="X{xml_id}" locationOfControl="V6"/>
      <RevisionName>A</RevisionName><RevisionIndex>1</RevisionIndex><RevisionFamily>F{xml_id}</RevisionFamily>
      <Maturity>IN_WORK</Maturity><Lifecycle>LC</Lifecycle>
      <TimeCreated>2026-10-14T16:29:23.0Z</TimeCreated><TimeModified>2026-10-14T16:29:32.0Z</TimeModified>
      <Owner>o</Owner><Organization>org</Organization><Project>p</Project><SecurityLevel>-1</SecurityLevel>
      <WCGEquivalentComputedExt.V_WCG_Mass>{mass}</WCGEquivalentComputedExt.V_WCG_Mass>
      <Material>{material}</Material>
    </Product>"""


def _instance(xml_id: str, parent: str, child: str, order: float = 1.0) -> str:
    return f"""
    <ProductInst id="{xml_id}">
      <ID>{child}.1</ID><Name></Name><Description></Description>
      <UniqueID External="X{xml_id}" XID="X{xml_id}" locationOfControl="V6"/>
      <Owned>{parent}</Owned><Instancing>{child}</Instancing><ChildOrder>{order}</ChildOrder>
      <RelativeTransformation3D><Rotation3D>1 0 0 0 1 0 0 0 1</Rotation3D><Translation3D>0 0 0</Translation3D></RelativeTransformation3D>
    </ProductInst>"""


def _write(tmp_path: Path, body: str, root: str = "ID_0") -> Path:
    path = tmp_path / "extract.xml"
    path.write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><XPDMXML xmlns="{NS}">'
        f'<InfoHeader><Info name="SourceObjectRef" value="{root}"/></InfoHeader>'
        f'<ProductStructure rootRefs="{root}">{body}</ProductStructure></XPDMXML>',
        encoding="utf-8",
    )
    return path


def test_minimal_valid_extract(tmp_path):
    body = (
        _product("ID_0", "root", 2.0)
        + _product("ID_1", "leaf", 1.0, "Steel")
        + _instance("ID_2", "ID_0", "ID_1")
        + _instance("ID_3", "ID_0", "ID_1", 2.0)
    )
    repo = XmlPlmRepository.from_file(_write(tmp_path, body))
    assert repo.root().plm_id == "root"
    assert repo.children("root")[0].quantity == 2
    assert repo.root().item_kind == "assembly"
    assert repo.warnings == ()


def test_dangling_reference(tmp_path):
    body = _product("ID_0", "root", 1.0) + _instance("ID_2", "ID_0", "ID_9")
    with pytest.raises(PlmValidationError, match="unknown product 'ID_9'"):
        XmlPlmRepository.from_file(_write(tmp_path, body))


def test_orphan_product(tmp_path):
    body = _product("ID_0", "root", 1.0) + _product("ID_1", "orphan", 1.0)
    with pytest.raises(PlmValidationError, match="not reachable"):
        XmlPlmRepository.from_file(_write(tmp_path, body))


def test_cycle(tmp_path):
    body = (
        _product("ID_0", "root", 1.0)
        + _product("ID_1", "a", 1.0)
        + _product("ID_2", "b", 1.0)
        + _instance("ID_3", "ID_0", "ID_1")
        + _instance("ID_4", "ID_1", "ID_2")
        + _instance("ID_5", "ID_2", "ID_1")
    )
    with pytest.raises(PlmValidationError, match="Cycle"):
        XmlPlmRepository.from_file(_write(tmp_path, body))


def test_duplicate_plm_id(tmp_path):
    body = _product("ID_0", "root", 1.0) + _product("ID_1", "root", 1.0) + _instance("ID_2", "ID_0", "ID_1")
    with pytest.raises(PlmValidationError, match="appears twice"):
        XmlPlmRepository.from_file(_write(tmp_path, body))


def test_malformed_mass(tmp_path):
    body = _product("ID_0", "root", "heavy")
    with pytest.raises(PlmParseError, match="not a valid number"):
        XmlPlmRepository.from_file(_write(tmp_path, body))


def test_mass_mismatch_is_a_warning(tmp_path):
    body = _product("ID_0", "root", 5.0) + _product("ID_1", "leaf", 1.0) + _instance("ID_2", "ID_0", "ID_1")
    repo = XmlPlmRepository.from_file(_write(tmp_path, body))
    assert len(repo.warnings) == 1
    assert "children sum to 1.000 kg" in repo.warnings[0]


def test_unknown_elements_are_reported(tmp_path):
    body = _product("ID_0", "root", 1.0) + '<Process id="ID_9"/>'
    repo = XmlPlmRepository.from_file(_write(tmp_path, body))
    assert any("<Process>" in w for w in repo.warnings)


def test_not_well_formed(tmp_path):
    path = tmp_path / "broken.xml"
    path.write_text("<XPDMXML><unclosed></XPDMXML>", encoding="utf-8")
    with pytest.raises(PlmParseError, match="not well-formed"):
        XmlPlmRepository.from_file(path)


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        XmlPlmRepository.from_file(tmp_path / "missing.xml")
