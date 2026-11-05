"""Dataset switching: another extract in the same format, with all, some or none of the enrichment files."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import ENRICHMENT_DIR, XML_PATH

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


def _render_view(app_dir: str, view: str, xml_path: str, enrichment_dir: str) -> None:
    import importlib
    import sys
    from pathlib import Path

    sys.path.insert(0, app_dir)
    import ui
    from services import Dataset, build_services

    ui.inject_style()
    services = build_services(Dataset(Path(xml_path), Path(enrichment_dir)))
    importlib.import_module(f"views.{view}").render(services)


def _view(view: str, xml_path: Path, enrichment_dir: Path) -> AppTest:
    args = (str(APP.parent), view, str(xml_path), str(enrichment_dir))
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
    assert "Team data missing" in sheet


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
