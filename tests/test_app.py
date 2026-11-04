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


@pytest.fixture(scope="module")
def explorer(plm):
    import sys

    sys.path.insert(0, str(APP.parent))
    from services import get_services
    from views import explorer

    return explorer, get_services()


def test_tree_rows_cover_the_structure(plm, explorer) -> None:
    view, services = explorer
    rows = view._tree_rows(services, merge=True, matches=None)
    ids = [row["id"] for row in rows]
    assert len(rows) == sum(1 for occ in plm.occurrences() if plm.product(occ.plm_id).item_kind != "wrapper")
    assert len(set(ids)) == len(ids)
    assert rows[0]["parent"] is None and rows[0]["plm_id"] == plm.root().plm_id
    assert all(row["parent"] in ids for row in rows[1:])
    assert {row["plm_id"] for row in rows if row["has_children"]} == {
        p.plm_id for p in plm.products() if p.item_kind == "assembly"
    }


def test_tree_rows_search_keeps_the_path(explorer) -> None:
    view, services = explorer
    target = next(p for p in services.plm.products() if p.name == "ECAR-3D_LSH_BEA2")
    rows = view._tree_rows(services, merge=True, matches={target.plm_id})
    assert [row["name"] for row in rows] == ["ECAR-EI_410KW_EDRIVE_FRONT", "ECAR-EI_REDUCER FRONT", target.name]
    assert [row["match"] for row in rows] == [False, False, True]


def test_structure_tree_expand_and_collapse_all(plm, explorer) -> None:
    view, services = explorer
    root = plm.root().plm_id
    key = f"{view.TREE_EXPANDED}-True"
    at = _view("explorer")
    assert at.session_state[key] == {root}

    next(b for b in at.button if b.label == "Expand all").click().run()
    assert not at.exception, [e.value for e in at.exception]
    parents = {row["id"] for row in view._tree_rows(services, merge=True, matches=None) if row["has_children"]}
    assert at.session_state[key] == parents

    next(b for b in at.button if b.label == "Collapse all").click().run()
    assert at.session_state[key] == {root}


def test_change_impact_modes(plm) -> None:
    at = _view("impact")
    mode = next(r for r in at.radio if "Replace by an existing part" in r.options)
    mode.set_value("Replace by an existing part").run()
    assert not at.exception, [e.value for e in at.exception]
    impacted = next(d.value for d in at.dataframe if "Projected mass (kg)" in d.value.columns)
    assert not impacted.empty
