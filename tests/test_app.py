"""Smoke tests of the Streamlit views: every page renders without exception on the real data."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("plotly")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "app" / "app.py"
PAGES = ["Dashboard", "Product Explorer", "Change Impact", "Assistant"]


@pytest.fixture
def app(plm) -> AppTest:  # `plm` skips the module when the OEM extract is absent
    return AppTest.from_file(str(APP), default_timeout=60).run()


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(app: AppTest, page: str) -> None:
    app.sidebar.radio[0].set_value(page).run()
    assert not app.exception, [e.value for e in app.exception]
    assert app.header[0].value


def test_dashboard_sections(app: AppTest) -> None:
    subheaders = {h.value for h in app.subheader}
    assert {"By subassembly", "Risk heat map", "Price exposure", "Material dependency"} <= subheaders
    assert any(e.label == "Data quality" for e in app.expander)


def test_explorer_tabs_and_filters(app: AppTest) -> None:
    app.sidebar.radio[0].set_value("Product Explorer").run()
    assert [t.label for t in app.tabs] == ["Structure", "Versions and lifecycle", "Compare"]
    revision = next(m for m in app.multiselect if m.label == "Revision")
    revision.set_value(["D"]).run()
    assert not app.exception
    assert any("match the search and filters" in c.value for c in app.caption)


def test_change_impact_modes(app: AppTest) -> None:
    app.sidebar.radio[0].set_value("Change Impact").run()
    mode = next(r for r in app.radio if "Replace by an existing part" in r.options)
    mode.set_value("Replace by an existing part").run()
    assert not app.exception
    assert next(m for m in app.metric if m.label == "Impacted assemblies").value != "0"
