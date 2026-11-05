"""3D view data: extraction from the exported viewer, strict check against the extract, safe injection."""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

import pytest
from conftest import VIEWER_PATH

from plm.parser import ViewerError, XmlPlmRepository, load_viewer
from plm.parser.viewer import check, extract

APP_DIR = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture(scope="module")
def reference_data(plm) -> dict:
    if not VIEWER_PATH.is_file():
        pytest.skip(f"3D view not found at {VIEWER_PATH}")
    return extract(VIEWER_PATH.read_text(encoding="utf-8"))


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "viewer_3d.html"
    path.write_text(f"<html><script>\nconst DATA = {json.dumps(data)};\n</script></html>", encoding="utf-8")
    return path


def test_reference_view_matches_the_extract(plm) -> None:
    viewer = load_viewer(VIEWER_PATH, plm)
    assert len(viewer.items) == 70 and viewer.warnings == ()
    assert all(viewer.covers(leaf.plm_id) for leaf in plm.leaves())
    assert viewer.placed_from_xml == 60


def test_view_of_another_extract_is_refused(derived_xml) -> None:
    with pytest.raises(ViewerError, match="built from another extract"):
        load_viewer(VIEWER_PATH, XmlPlmRepository.from_file(derived_xml))


def test_view_rebuilt_for_the_derived_extract_is_accepted(derived_xml, derived_viewer) -> None:
    assert load_viewer(derived_viewer, XmlPlmRepository.from_file(derived_xml)).warnings == ()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<html><body>No data here</body></html>", "not an exported 3D view"),
        ("<script>const DATA = {items: [}</script>", "not valid JSON"),
        ("<script>const DATA = [1, 2];</script>", "not a JSON object"),
    ],
)
def test_unreadable_files_are_refused(tmp_path, plm, text: str, expected: str) -> None:
    path = tmp_path / "viewer_3d.html"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ViewerError, match=expected):
        load_viewer(path, plm)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("name", {"html": "<b>"}, "name is missing or has the wrong type"),
        ("mass", "1 kg", "mass must be a number"),
        ("pos", [0, 0], "pos must be a 3D vector"),
        ("shape", {"t": "script", "pos": [0, 0, 0], "ax": [0, 1, 0]}, "shape.t must be one of"),
        ("shape", {"t": "cyl", "r": "alert(1)", "h": 1, "pos": [0, 0, 0], "ax": [0, 1, 0]}, "must be numbers"),
    ],
)
def test_fields_are_type_checked(plm, reference_data, field: str, value, expected: str) -> None:
    data = copy.deepcopy(reference_data)
    data["items"][0][field] = value
    problems, _ = check(data, plm)
    assert any(expected in p for p in problems), problems


def test_an_assembly_is_refused(plm, reference_data) -> None:
    data = copy.deepcopy(reference_data)
    data["items"][0]["plm"] = plm.root().plm_id
    problems, _ = check(data, plm)
    assert any("assemblies" in p for p in problems)


def test_partial_view_is_accepted_with_a_warning(tmp_path, plm, reference_data) -> None:
    data = copy.deepcopy(reference_data)
    dropped = data["items"][0]["plm"]
    data["items"] = [item for item in data["items"] if item["plm"] != dropped]
    viewer = load_viewer(_write(tmp_path, data), plm)
    assert not viewer.covers(dropped)
    assert any("not drawn" in w for w in viewer.warnings)


def test_injected_page_cannot_be_broken_by_the_data(plm, reference_data, tmp_path) -> None:
    pytest.importorskip("streamlit")
    sys.path.insert(0, str(APP_DIR))
    import viewer as app_viewer

    data = copy.deepcopy(reference_data)
    data["items"][0]["name"] = "</script><script>alert(1)</script>"
    loaded = load_viewer(_write(tmp_path, data), plm)
    page = app_viewer.page(loaded, {"mode": "compact", "title": "</script>"})
    template = app_viewer.TEMPLATE.read_text(encoding="utf-8")
    assert page.count("</script>") == template.count("</script>")
    assert "alert(1)" in page and "<script>alert" not in page
    assert app_viewer.DATA_MARK not in page and app_viewer.CONFIG_MARK not in page
    assert '"mode":"compact"' in page


def test_consistency_checks_of_the_reference_view(plm) -> None:
    checks = load_viewer(VIEWER_PATH, plm).checks(plm.root().mass_kg)
    assert [c.status for c in checks] == ["ok", "ok", "ok", "check", "check"]
    assert "100.1 kg" in checks[1].text and "Screw D8 L50" in checks[3].text
    assert "2 part(s) modelled for meshing" in checks[4].text


def test_mass_gap_is_flagged(plm) -> None:
    checks = load_viewer(VIEWER_PATH, plm).checks(plm.root().mass_kg + 1.0)
    assert checks[1].status == "check"


def test_template_edits_are_picked_up_without_restart(monkeypatch, tmp_path) -> None:
    pytest.importorskip("streamlit")
    sys.path.insert(0, str(APP_DIR))
    import viewer as app_viewer

    template = tmp_path / "template.html"
    template.write_text(f"v1 {app_viewer.DATA_MARK} {app_viewer.CONFIG_MARK}", encoding="utf-8")
    monkeypatch.setattr(app_viewer, "TEMPLATE", template)
    assert app_viewer._template()[0] == "v1 "
    template.write_text(f"v2 {app_viewer.DATA_MARK} {app_viewer.CONFIG_MARK}", encoding="utf-8")
    os.utime(template, ns=(template.stat().st_atime_ns, template.stat().st_mtime_ns + 1_000_000))
    assert app_viewer._template()[0] == "v2 "
