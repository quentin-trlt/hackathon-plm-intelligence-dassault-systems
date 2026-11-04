"""Smoke tests of the Streamlit views: every page renders without exception on the real data."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("plotly")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "app" / "app.py"
VIEWS = ["dashboard", "explorer", "impact", "assistant"]


def _render_view(app_dir: str, view: str) -> None:
    import importlib
    import sys

    sys.path.insert(0, app_dir)
    import ui
    from services import get_services

    ui.inject_style()
    importlib.import_module(f"views.{view}").render(get_services())


def _view(view: str) -> AppTest:
    return AppTest.from_function(_render_view, args=(str(APP.parent), view), default_timeout=60).run()


@pytest.fixture
def app(plm) -> AppTest:  # `plm` skips the module when the OEM extract is absent
    return AppTest.from_file(str(APP), default_timeout=60).run()


def test_app_renders_default_page(app: AppTest) -> None:
    assert not app.exception, [e.value for e in app.exception]
    assert app.header[0].value == "Overview"


@pytest.mark.parametrize("view", VIEWS)
def test_view_renders(plm, view: str) -> None:
    at = _view(view)
    assert not at.exception, [e.value for e in at.exception]
    assert at.header[0].value


def test_dashboard_sections(app: AppTest) -> None:
    subheaders = {h.value for h in app.subheader}
    assert {"By subassembly", "Risk heat map", "Price exposure", "Material dependency"} <= subheaders
    assert any(e.label == "Data quality" for e in app.expander)


def test_explorer_tabs_and_filters(plm) -> None:
    at = _view("explorer")
    assert {"Structure", "Versions and lifecycle", "Compare"} <= {t.label for t in at.tabs}
    revision = next(m for m in at.multiselect if m.label == "Revision")
    revision.set_value(["D"]).run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("match the search and filters" in c.value for c in at.caption)


def test_change_impact_modes(plm) -> None:
    at = _view("impact")
    mode = next(r for r in at.radio if "Replace by an existing part" in r.options)
    mode.set_value("Replace by an existing part").run()
    assert not at.exception, [e.value for e in at.exception]
    impacted = next(d.value for d in at.dataframe if "Projected mass (kg)" in d.value.columns)
    assert not impacted.empty
