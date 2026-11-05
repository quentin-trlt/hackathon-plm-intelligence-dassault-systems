"""Dataset switching: another extract in the same format, with all, some or none of the enrichment files."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import DERIVED_ID_PREFIX as DERIVED_PREFIX
from conftest import ENRICHMENT_DIR, VIEWER_PATH, XML_PATH

from plm.parser import MATERIAL_PRICES_FILE, PART_SOURCING_FILE, SUPPLIERS_FILE

pytest.importorskip("streamlit")
pytest.importorskip("plotly")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "app" / "app.py"
VIEWS = ["dashboard", "explorer", "risks", "supply", "impact", "assistant"]
ALL_FILES = (MATERIAL_PRICES_FILE, SUPPLIERS_FILE, PART_SOURCING_FILE)

sys.path.insert(0, str(APP.parent))
import dataset  # noqa: E402
from services import Dataset, build_services  # noqa: E402


def _render_view(app_dir: str, view: str, xml_path: str, enrichment_dir: str, viewer_path: str = "") -> None:
    import importlib
    import sys
    from pathlib import Path

    sys.path.insert(0, app_dir)
    import ui
    from services import Dataset, build_services

    ui.inject_style()
    viewer = Path(viewer_path) if viewer_path else None
    services = build_services(Dataset(Path(xml_path), Path(enrichment_dir), viewer_path=viewer))
    importlib.import_module(f"views.{view}").render(services)


def _view(view: str, xml_path: Path, enrichment_dir: Path, viewer_path: Path | None = None) -> AppTest:
    args = (str(APP.parent), view, str(xml_path), str(enrichment_dir), str(viewer_path or ""))
    return AppTest.from_function(_render_view, args=args, default_timeout=60).run()


def _texts(at: AppTest) -> str:
    return " ".join(str(e.value) for e in [*at.info, *at.caption, *at.markdown])


@pytest.mark.parametrize("files", [(), (MATERIAL_PRICES_FILE,), ALL_FILES], ids=["no-csv", "prices-only", "all-csv"])
@pytest.mark.parametrize("view", VIEWS)
def test_views_render_on_another_extract(derived_xml, derived_enrichment, view: str, files: tuple[str, ...]) -> None:
    at = _view(view, derived_xml, derived_enrichment(*files))
    assert not at.exception, [e.value for e in at.exception]
    assert at.header[0].value


def test_missing_sourcing_is_stated_not_scored_as_risk(derived_xml, derived_enrichment) -> None:
    folder = derived_enrichment(MATERIAL_PRICES_FILE)
    at = _view("supply", derived_xml, folder)
    assert f"needs {PART_SOURCING_FILE}" in _texts(at)
    assert "Single-sourced parts" not in {h.value for h in at.subheader}
    risks = _view("risks", derived_xml, folder)
    assert "Partial risk score" in _texts(risks)
    services = build_services(Dataset(derived_xml, folder))
    assert services.has_prices and not services.has_sourcing
    assert all(r.criterion("SUPPLY") is None for r in services.risk.ranking())


def _html(at: AppTest) -> str:
    return " ".join(e.value for e in at.get("html"))


@pytest.mark.parametrize(("view", "label"), [("dashboard", "Material cost"), ("impact", "Motor material cost")])
def test_missing_prices_is_stated(derived_xml, derived_enrichment, view: str, label: str) -> None:
    html = _html(_view(view, derived_xml, derived_enrichment()))
    assert (
        f'<div class="plm-figure-label">{label}</div><div class="plm-figure-value">Missing data</div>'
        f'<div class="plm-figure-note">Needs {MATERIAL_PRICES_FILE}.</div>'
    ) in html


def test_app_runs_on_an_uploaded_dataset(derived_xml, derived_enrichment) -> None:
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.session_state["dataset"] = Dataset(derived_xml, derived_enrichment())
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    sheet = _html(at.sidebar)
    assert "XPDMXML_TEST_PLM.xml (uploaded)" in sheet
    assert "Not provided: material_prices.csv" in sheet


def test_app_falls_back_to_the_reference_when_the_upload_is_gone(tmp_path) -> None:
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.session_state["dataset"] = Dataset(tmp_path / "gone.xml", tmp_path)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert "dataset" not in at.session_state
    assert f"{XML_PATH.name} (reference extract)" in _html(at.sidebar)


def test_upload_with_its_own_csv_is_accepted(derived_xml, derived_enrichment) -> None:
    folder = derived_enrichment(*ALL_FILES)
    staged = dataset.stage(
        derived_xml.name, derived_xml.read_bytes(), {f: (folder / f).read_bytes() for f in ALL_FILES}
    )
    try:
        assert dataset.validate(staged) == []
        assert build_services(staged).enrichment.missing_files() == ()
    finally:
        dataset.discard(staged)
    assert not staged.xml_path.parent.exists()


def test_upload_with_the_reference_csv_is_refused(derived_xml) -> None:
    staged = dataset.stage(derived_xml.name, derived_xml.read_bytes(), {PART_SOURCING_FILE: b"", SUPPLIERS_FILE: b""})
    (staged.enrichment_dir / PART_SOURCING_FILE).write_bytes((ENRICHMENT_DIR / PART_SOURCING_FILE).read_bytes())
    (staged.enrichment_dir / SUPPLIERS_FILE).write_bytes((ENRICHMENT_DIR / SUPPLIERS_FILE).read_bytes())
    try:
        problems = dataset.validate(staged)
    finally:
        dataset.discard(staged)
    assert problems and all("is not in the PLM extract" in p for p in problems)


@pytest.mark.parametrize(
    ("content", "expected"),
    [(b"<not xml", "not well-formed"), (b'<?xml version="1.0"?><other/>', "root element is not")],
)
def test_upload_of_a_broken_extract_is_refused(content: bytes, expected: str) -> None:
    staged = dataset.stage("broken.xml", content, {})
    try:
        problems = dataset.validate(staged)
    finally:
        dataset.discard(staged)
    assert len(problems) == 1 and expected in problems[0]


def test_precheck() -> None:
    assert dataset.precheck([PART_SOURCING_FILE]) == [
        f"{PART_SOURCING_FILE} refers to suppliers by supplier_id: upload {SUPPLIERS_FILE} with it."
    ]
    assert dataset.precheck([SUPPLIERS_FILE, PART_SOURCING_FILE]) == []
    assert "not an enrichment file" in dataset.precheck(["prices.csv"])[0]


@pytest.mark.parametrize(
    ("name", "expected"),
    [("../../etc/passwd", "passwd.xml"), ("my extract (v2).XML", "my extract _v2_.XML"), ("..", "extract.xml")],
)
def test_safe_xml_name(name: str, expected: str) -> None:
    assert dataset.safe_xml_name(name) == expected


def test_the_reference_dataset_is_never_deleted() -> None:
    from services import REFERENCE

    dataset.discard(REFERENCE)
    assert XML_PATH.is_file() and ENRICHMENT_DIR.is_dir()


def test_change_dataset_opens_the_upload_dialog(plm) -> None:
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    next(b for b in at.sidebar.button if b.label == "Change dataset").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert any(b.label == "Load this dataset" and b.disabled for b in at.button)


def _upload_then_render(app_dir: str, xml_path: str, csv_folder: str) -> None:
    import sys
    from pathlib import Path

    import streamlit as st

    sys.path.insert(0, app_dir)
    import dataset
    from services import get_services

    if "uploaded" not in st.session_state:
        files = {f.name: f.read_bytes() for f in Path(csv_folder).iterdir()}
        st.session_state["problems"] = dataset.load_upload(Path(xml_path).name, Path(xml_path).read_bytes(), files)
        st.session_state["uploaded"] = True
    st.write(get_services().plm.root().name)


def test_load_upload_activates_the_dataset_for_the_session(derived_xml, derived_enrichment) -> None:
    folder = derived_enrichment(MATERIAL_PRICES_FILE)
    at = AppTest.from_function(_upload_then_render, args=(str(APP.parent), str(derived_xml), str(folder))).run()
    assert not at.exception, [e.value for e in at.exception]
    active = at.session_state["dataset"]
    try:
        assert at.session_state["problems"] == []
        assert active.name == derived_xml.name and not active.reference
        assert (active.enrichment_dir / MATERIAL_PRICES_FILE).is_file()
        assert at.markdown[0].value.startswith("TEST-")
    finally:
        dataset.discard(active)


def _frames(at: AppTest) -> list[str]:
    return [e.proto.srcdoc for e in at.get("iframe")]


@pytest.mark.parametrize(
    ("view", "expected"),
    [
        ("risks", '"color_label":"Risk"'),
        ("impact", '"color_label":"Change"'),
        ("explorer", '"mode":"full"'),
    ],
)
def test_reference_views_draw_the_3d_view(plm, view: str, expected: str) -> None:
    at = _view(view, XML_PATH, ENRICHMENT_DIR, VIEWER_PATH)
    assert not at.exception, [e.value for e in at.exception]
    frames = _frames(at)
    assert frames and any(expected in f for f in frames)
    assert any("60 of 70 positions read from the XML" in c.value for c in at.caption)
    if view == "explorer":
        assert any(m.value == "**Consistency checks**" for m in at.markdown)


def test_risk_colours_follow_the_risk_levels(plm) -> None:
    import ui

    frame = next(f for f in _frames(_view("risks", XML_PATH, ENRICHMENT_DIR, VIEWER_PATH)) if '"Risk"' in f)
    services = build_services(Dataset(XML_PATH, ENRICHMENT_DIR, viewer_path=VIEWER_PATH))
    top = services.risk.ranking()[0]
    assert f'"{top.plm_id}":"{ui.RISK_COLORS[top.level]}"' in frame


@pytest.mark.parametrize("view", ["risks", "impact", "explorer"])
def test_missing_3d_view_is_stated(derived_xml, derived_enrichment, view: str) -> None:
    at = _view(view, derived_xml, derived_enrichment())
    assert not _frames(at)
    assert "needs viewer_3d.html" in _texts(at)


def test_uploaded_3d_view_is_drawn(derived_xml, derived_enrichment, derived_viewer) -> None:
    at = _view("impact", derived_xml, derived_enrichment(), derived_viewer)
    assert not at.exception, [e.value for e in at.exception]
    assert any(DERIVED_PREFIX in f for f in _frames(at))


def test_upload_with_its_3d_view_is_accepted(derived_xml, derived_viewer) -> None:
    staged = dataset.stage(derived_xml.name, derived_xml.read_bytes(), {}, derived_viewer.read_bytes())
    try:
        assert dataset.validate(staged) == []
        assert build_services(staged).viewer is not None
    finally:
        dataset.discard(staged)


def test_upload_with_the_reference_3d_view_is_refused(derived_xml) -> None:
    staged = dataset.stage(derived_xml.name, derived_xml.read_bytes(), {}, VIEWER_PATH.read_bytes())
    try:
        problems = dataset.validate(staged)
    finally:
        dataset.discard(staged)
    assert len(problems) == 1 and "built from another extract" in problems[0]


def _card_of_side_shaft(app_dir: str) -> None:
    import sys

    sys.path.insert(0, app_dir)
    from services import get_services
    from views import explorer

    s = get_services()
    explorer.focus(s, next(p for p in s.plm.leaves() if p.name == "ECAR-3D_DifferentialSideShaft").plm_id)
    explorer.render(s)


def test_component_card_frames_the_part_in_3d(plm) -> None:
    at = AppTest.from_function(_card_of_side_shaft, args=(str(APP.parent),), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    shaft = next(p for p in plm.leaves() if p.name == "ECAR-3D_DifferentialSideShaft").plm_id
    card = next(f for f in _frames(at) if '"mode":"compact"' in f)
    assert f'"focus":["{shaft}"]' in card and '"color_label":"Selection"' in card
