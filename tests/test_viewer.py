"""3D view: scene rebuilt from the extract, shape profiles matched by PLM ID, safe injection in the viewer page."""

from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path

import pytest
from conftest import XML_PATH

from plm.parser import ViewerError, XmlPlmRepository, build_view
from plm.parser.viewer import display_name, load_profiles

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"
PROFILES_PATH = REPO_ROOT / "data" / "raw" / "geometry_reference.json"
ASSETS = APP_DIR / "assets" / "viewer3d"


@pytest.fixture(scope="module")
def profiles_path() -> Path:
    if not PROFILES_PATH.is_file():
        pytest.skip(f"shape profiles not found at {PROFILES_PATH}")
    return PROFILES_PATH


@pytest.fixture(scope="module")
def view(plm, profiles_path):
    return build_view(plm, profiles_path)


def _edited(tmp_path: Path, old: str, new: str) -> XmlPlmRepository:
    text = XML_PATH.read_text(encoding="utf-8")
    assert text.count(old) == 1, old
    path = tmp_path / "edited.xml"
    path.write_text(text.replace(old, new), encoding="utf-8")
    return XmlPlmRepository.from_file(path)


def _profiles(tmp_path: Path, edit) -> Path:
    data = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
    edit(data)
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _item(view, name: str) -> dict:
    return next(i for i in view.items if i["name"] == name)


def test_reference_scene_matches_the_extract(plm, view) -> None:
    data = view.data
    assert (data["n_parts"], data["n_occ"]) == (36, 70)
    assert data["total_mass"] == pytest.approx(100.1)
    assert view.placed_from_xml == 60 and view.markers == frozenset() and view.changed == frozenset()
    assert view.plm_ids == {leaf.plm_id for leaf in plm.leaves()}
    assert all(i["qty"] == plm.total_quantity(i["plm"]).value for i in view.items)
    assert sum(i["artefact"] for i in view.items) == 2 and sum(i["dup"] for i in view.items) == 2
    assert data["axes"]["d12"] == pytest.approx(0.100) and data["axes"]["d23"] == pytest.approx(0.154)
    masses = {b: math.fsum(i["mass"] for i in view.items if i["block"] == b) for b in ("E-motor", "Reducer", "Housing")}
    assert masses == pytest.approx({"E-motor": 39.73, "Reducer": 29.85, "Housing": 30.52})


def test_positions_compose_the_instance_transforms(view) -> None:
    bearing = next(i for i in view.items if i["pid"] == "ID_3")
    assert bearing["pos"][1] == pytest.approx(0.318318448769, abs=1e-12)
    assert bearing["shape"]["t"] == "bearing" and bearing["place"] == "xml"


def test_changed_mass_and_name_are_read_from_the_xml(tmp_path, profiles_path, view) -> None:
    gear = next(i for i in view.items if i["pid"] == "ID_42")
    text = XML_PATH.read_text(encoding="utf-8")
    start = text.index('<Product id="ID_42"')
    block = text[start : text.index("</Product>", start)]
    mass = re.search(r"<WCGEquivalentComputedExt.V_WCG_Mass>([^<]+)<", block).group(1)
    edited = block.replace(f">{mass}<", ">1.5<").replace("<Name>ECAR-3D_GearWheel<", "<Name>ECAR-3D_Renamed gear<")
    rebuilt = build_view(_edited(tmp_path, block, edited), profiles_path)
    changed = next(i for i in rebuilt.items if i["pid"] == "ID_42")
    assert changed["mass"] == 1.5 and changed["name"] == "Renamed gear"
    assert changed["shape"]["t"] == gear["shape"]["t"] == "gear"
    assert rebuilt.changed == {gear["plm"]}
    assert rebuilt.data["total_mass"] == pytest.approx(100.1 - gear["mass"] + 1.5)
    assert any("differ from their profile" in c.text for c in rebuilt.checks(rebuilt.data["total_mass"]))


def test_extract_without_known_parts_is_drawn_as_markers(derived_xml, profiles_path) -> None:
    derived = build_view(XmlPlmRepository.from_file(derived_xml), profiles_path)
    assert derived.data["n_occ"] == 70 and derived.markers == derived.plm_ids
    assert all(i["place"] == "xml" for i in derived.items)
    check = next(c for c in derived.checks(derived.data["total_mass"]) if "profile" in c.text)
    assert check.status == "check" and "36 without a profile" in check.text


def test_missing_profiles_draw_markers(plm, tmp_path) -> None:
    scene = build_view(plm, tmp_path / "absent.json")
    assert scene.profiles_file is None and scene.markers == scene.plm_ids
    assert any("No shape profiles" in c.text for c in scene.checks(plm.root().mass_kg))


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        (lambda d: d["items"][0].update(plm=None), "plm must be text"),
        (lambda d: d["items"][0].update(pos=[0, 0]), "pos must be a 3D vector"),
        (lambda d: d["items"][0]["shape"].update(t="script"), "shape.t must be one of"),
        (lambda d: d["items"][0]["shape"].update(ri="alert(1)"), "shape values must be numbers"),
        (lambda d: d["items"][0].update(place="<b>"), "place must be one of"),
        (lambda d: d.update(items="none"), "must be an object with an items list"),
    ],
)
def test_malformed_profiles_are_refused(tmp_path, profiles_path, edit, expected: str) -> None:
    with pytest.raises(ViewerError, match=re.escape(expected)):
        load_profiles(_profiles(tmp_path, edit))


def test_invalid_json_profiles_are_refused(tmp_path) -> None:
    path = tmp_path / "profiles.json"
    path.write_text("{items: [", encoding="utf-8")
    with pytest.raises(ViewerError, match="not valid JSON"):
        load_profiles(path)


def test_consistency_checks_of_the_reference_view(plm, view) -> None:
    checks = view.checks(plm.root().mass_kg)
    assert [c.status for c in checks] == ["ok", "ok", "ok", "ok", "check", "check"]
    assert "100.1 kg" in checks[1].text and "All 36 parts" in checks[2].text
    assert "100.0 mm and 154.0 mm" in checks[3].text and "ID_191 and ID_192" in checks[4].text
    assert "2 part(s) modelled for meshing" in checks[5].text
    assert view.checks(plm.root().mass_kg + 1.0)[1].status == "check"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("ECAR-3D_DifferentialSideShaft", "DifferentialSideShaft"),
        ("TEST-3D_Stator_Core", "Stator Core"),
        ("Bolt", "Bolt"),
    ],
)
def test_display_names(name: str, expected: str) -> None:
    assert display_name(name) == expected


def _app_viewer():
    pytest.importorskip("streamlit")
    sys.path.insert(0, str(APP_DIR))
    import viewer as app_viewer

    return app_viewer


def test_injected_page_cannot_be_broken_by_the_data(view) -> None:
    app_viewer = _app_viewer()
    data = json.loads(json.dumps(view.data))
    data["items"][0]["name"] = "</script><script>alert(1)</script>"
    hostile = type(view)(data=data, profiles_file=view.profiles_file)
    page = app_viewer.page(hostile, {"mode": "compact", "title": "</script>"})
    shell = (ASSETS / "index.html").read_text(encoding="utf-8")
    sources = "".join((ASSETS / f).read_text(encoding="utf-8") for f in app_viewer.SOURCES.values())
    assert page.count("</script>") == shell.count("</script>") + sources.count("</script>")
    assert "alert(1)" in page and "<script>alert" not in page
    assert app_viewer.DATA_MARK not in page and app_viewer.CONFIG_MARK not in page
    assert not re.search(r"/\*(STYLE|THREE|ORBIT|ROOM|BGU|GEOMETRY|APP)\*/", page)
    assert '"mode":"compact"' in page and "Content-Security-Policy" in page


def test_viewer_edits_are_picked_up_without_restart(monkeypatch, tmp_path) -> None:
    app_viewer = _app_viewer()
    for name in ["index.html", *app_viewer.SOURCES.values()]:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("", encoding="utf-8")
    index = tmp_path / "index.html"
    index.write_text(f"v1 /*APP*/ {app_viewer.DATA_MARK} {app_viewer.CONFIG_MARK}", encoding="utf-8")
    (tmp_path / "app.js").write_text("code1", encoding="utf-8")
    monkeypatch.setattr(app_viewer, "ASSETS", tmp_path)
    assert app_viewer._template()[0] == "v1 code1 "
    app_js = tmp_path / "app.js"
    app_js.write_text("code2", encoding="utf-8")
    os.utime(app_js, ns=(app_js.stat().st_atime_ns, app_js.stat().st_mtime_ns + 1_000_000))
    assert app_viewer._template()[0] == "v1 code2 "


def test_viewer_sources_hold_no_partner_data() -> None:
    for path in ASSETS.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert not re.search(r"prd-\d{8}|ECAR-|XPDMXML|[0-9A-F]{40}", text), path
