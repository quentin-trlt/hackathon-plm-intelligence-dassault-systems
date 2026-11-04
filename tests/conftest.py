from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from plm.model import Product
from plm.parser import XmlPlmRepository

REPO_ROOT = Path(__file__).resolve().parents[1]
XML_PATH = REPO_ROOT / "data" / "raw" / "XPDMXML_ECAR_PLM.xml"
ENRICHMENT_DIR = REPO_ROOT / "data" / "enrichment"


@pytest.fixture(scope="session")
def plm() -> XmlPlmRepository:
    if not XML_PATH.is_file():
        pytest.skip(f"OEM extract not found at {XML_PATH}")
    return XmlPlmRepository.from_file(XML_PATH)


@pytest.fixture(scope="session")
def by_name(plm: XmlPlmRepository) -> Callable[[str], list[Product]]:
    def find(name: str) -> list[Product]:
        return [p for p in plm.products() if p.name == name]

    return find
