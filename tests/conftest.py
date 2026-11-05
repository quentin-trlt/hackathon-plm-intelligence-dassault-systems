from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from plm.model import Product
from plm.parser import PlmData, XmlPlmRepository, load

REPO_ROOT = Path(__file__).resolve().parents[1]
XML_PATH = REPO_ROOT / "data" / "raw" / "XPDMXML_ECAR_PLM.xml"
ENRICHMENT_DIR = REPO_ROOT / "data" / "enrichment"
DERIVED_ID_PREFIX = "prd-99000001-"


def derive(text: str) -> str:
    """Another extract in the same format: every plm_id and product name differs from the reference one."""
    return text.replace("prd-12535825-", DERIVED_ID_PREFIX).replace("ECAR-", "TEST-")


@pytest.fixture(scope="session")
def derived_xml(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not XML_PATH.is_file():
        pytest.skip(f"OEM extract not found at {XML_PATH}")
    path = tmp_path_factory.mktemp("derived") / "XPDMXML_TEST_PLM.xml"
    path.write_text(derive(XML_PATH.read_text(encoding="utf-8")), encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def derived_enrichment(tmp_path_factory: pytest.TempPathFactory) -> Callable[..., Path]:
    """An enrichment folder for the derived extract holding only the given reference files, ids rewritten."""

    def make(*files: str) -> Path:
        folder = tmp_path_factory.mktemp("enrichment")
        for file in files:
            (folder / file).write_text(derive((ENRICHMENT_DIR / file).read_text(encoding="utf-8")), encoding="utf-8")
        return folder

    return make


@pytest.fixture(scope="session")
def plm() -> XmlPlmRepository:
    if not XML_PATH.is_file():
        pytest.skip(f"OEM extract not found at {XML_PATH}")
    return XmlPlmRepository.from_file(XML_PATH)


@pytest.fixture(scope="session")
def plm_data() -> PlmData:
    if not XML_PATH.is_file():
        pytest.skip(f"OEM extract not found at {XML_PATH}")
    return load(XML_PATH, ENRICHMENT_DIR)


@pytest.fixture(scope="session")
def by_name(plm: XmlPlmRepository) -> Callable[[str], list[Product]]:
    def find(name: str) -> list[Product]:
        return [p for p in plm.products() if p.name == name]

    return find
