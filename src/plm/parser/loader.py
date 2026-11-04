from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from plm.parser.enrichment import CsvEnrichmentRepository
from plm.parser.xml_reader import XmlPlmRepository

DEFAULT_XML_PATH = Path("data/raw/XPDMXML_ECAR_PLM.xml")
DEFAULT_ENRICHMENT_DIR = Path("data/enrichment")


@dataclass(frozen=True, slots=True)
class PlmData:
    plm: XmlPlmRepository
    enrichment: CsvEnrichmentRepository


def load(xml_path: Path = DEFAULT_XML_PATH, enrichment_dir: Path = DEFAULT_ENRICHMENT_DIR) -> PlmData:
    """Single entry point for the app. Default paths are relative to the repository root."""
    plm = XmlPlmRepository.from_file(Path(xml_path))
    return PlmData(plm=plm, enrichment=CsvEnrichmentRepository.from_dir(Path(enrichment_dir), plm))
