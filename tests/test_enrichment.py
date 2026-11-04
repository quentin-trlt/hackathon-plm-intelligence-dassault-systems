from __future__ import annotations

from pathlib import Path

import pytest

from plm.contract import UnknownSupplierError
from plm.parser import CsvEnrichmentRepository, EnrichmentError

REPO_ENRICHMENT_DIR = Path(__file__).resolve().parents[1] / "data" / "enrichment"

PRICES_HEADER = "material,price_eur_per_kg,source_note\n"
SUPPLIERS_HEADER = "supplier_id,name,country,source_note\n"
SOURCING_HEADER = "plm_id,part_name,material,supplier_id,make_or_buy,lead_time_days,source_note\n"

BEARING = "Bearing steel 100Cr6 (AISI 52100)"
HSH_BEA3 = "prd-12535825-00000081"
HSH = "prd-12535825-00000082"


def _write(directory: Path, prices: str = "", suppliers: str = "", sourcing: str = "") -> Path:
    (directory / "material_prices.csv").write_text(PRICES_HEADER + prices, encoding="utf-8")
    (directory / "suppliers.csv").write_text(SUPPLIERS_HEADER + suppliers, encoding="utf-8")
    (directory / "part_sourcing.csv").write_text(SOURCING_HEADER + sourcing, encoding="utf-8")
    return directory


def _problems(directory: Path, plm) -> list[str]:
    with pytest.raises(EnrichmentError) as exc:
        CsvEnrichmentRepository.from_dir(directory, plm)
    return exc.value.problems


def test_repository_templates_load(plm):
    repo = CsvEnrichmentRepository.from_dir(REPO_ENRICHMENT_DIR, plm)
    report = repo.coverage(plm)
    assert (
        len(report.leaves_without_sourcing) + len({s.plm_id for p in plm.leaves() for s in repo.sourcing(p.plm_id)})
        == 36
    )
    assert len(report.materials_without_price) + len(repo.material_prices()) == 14


def test_valid_files(plm, tmp_path):
    _write(
        tmp_path,
        prices=f'"{BEARING}",1.8,LME index Q3 2026\n',
        suppliers="SUP-001,Bearings GmbH,DE,assumption\nSUP-002,Roul SA,FR,assumption\n",
        sourcing=f"{HSH_BEA3},,,SUP-001,buy,45,assumption\n{HSH_BEA3},,,SUP-002,BUY,,assumption\n",
    )
    repo = CsvEnrichmentRepository.from_dir(tmp_path, plm)
    price = repo.material_price(BEARING)
    assert price.price_eur_per_kg == 1.8
    assert price.source.origin == "enrichment"
    assert (price.source.file, price.source.line) == ("material_prices.csv", 2)
    assert [s.supplier_id for s in repo.sourcing(HSH_BEA3)] == ["SUP-001", "SUP-002"]
    assert repo.sourcing(HSH_BEA3)[1].make_or_buy == "buy"
    assert repo.sourcing(HSH_BEA3)[1].lead_time_days is None
    assert repo.supplier("SUP-002").country == "FR"
    with pytest.raises(UnknownSupplierError):
        repo.supplier("SUP-999")


def test_unfilled_rows_are_skipped_and_reported(plm, tmp_path):
    _write(tmp_path, prices=f'"{BEARING}",,\n', sourcing=f"{HSH},ECAR-3D_HSH,steel,,,,\n")
    repo = CsvEnrichmentRepository.from_dir(tmp_path, plm)
    report = repo.coverage(plm)
    assert len(report.unfilled_rows) == 2
    assert BEARING in report.materials_without_price
    assert HSH in report.leaves_without_sourcing
    assert not report.complete


def test_partially_filled_row_is_rejected(plm, tmp_path):
    _write(tmp_path, prices=f'"{BEARING}",1.8,\n')
    assert _problems(tmp_path, plm) == ["material_prices.csv:2: source_note is empty"]


def test_unknown_material(plm, tmp_path):
    _write(tmp_path, prices="Bearing steel 100Cr6,1.8,note\n")
    assert "must match the XML exactly" in _problems(tmp_path, plm)[0]


def test_decimal_comma(plm, tmp_path):
    _write(tmp_path, prices=f'"{BEARING}","1,8",note\n')
    assert "use a decimal point" in _problems(tmp_path, plm)[0]


def test_semicolon_separator(plm, tmp_path):
    (tmp_path / "material_prices.csv").write_text("material;price_eur_per_kg;source_note\n", encoding="utf-8")
    assert "';' as separator" in _problems(tmp_path, plm)[0]


def test_missing_column(plm, tmp_path):
    (tmp_path / "suppliers.csv").write_text("supplier_id,name\n", encoding="utf-8")
    assert "missing columns ['country', 'source_note']" in _problems(tmp_path, plm)[0]


def test_sourcing_errors(plm, tmp_path):
    _write(
        tmp_path,
        suppliers="SUP-001,A,DE,note\n",
        sourcing=(
            f"{HSH},,,SUP-404,buy,10,note\n"
            "prd-unknown,,,SUP-001,buy,10,note\n"
            "prd-12535825-00000047,,,SUP-001,buy,10,note\n"
            f"{HSH},,,SUP-001,rent,10,note\n"
            f"{HSH_BEA3},,,SUP-001,make,-3,note\n"
            f"{HSH_BEA3},,,SUP-001,make,3,note\n"
        ),
    )
    problems = _problems(tmp_path, plm)
    assert any(p.startswith("part_sourcing.csv:2:") and "SUP-404" in p for p in problems)
    assert any(p.startswith("part_sourcing.csv:3:") and "not in the PLM extract" in p for p in problems)
    assert any(p.startswith("part_sourcing.csv:4:") and "is an assembly" in p for p in problems)
    assert any(p.startswith("part_sourcing.csv:5:") and "make_or_buy" in p for p in problems)
    assert any(p.startswith("part_sourcing.csv:6:") and "lead_time_days" in p for p in problems)
    assert any(p.startswith("part_sourcing.csv:7:") and "already listed" in p for p in problems)


def test_supplier_errors(plm, tmp_path):
    _write(tmp_path, suppliers="S1,A,Germany,note\nSUP-001,A,DE,note\nSUP-001,B,FR,note\n")
    problems = _problems(tmp_path, plm)
    assert any("does not match SUP-NNN" in p for p in problems)
    assert any("ISO 3166-1" in p for p in problems)
    assert any(p.startswith("suppliers.csv:4:") and "already defined" in p for p in problems)


def test_missing_directory(plm, tmp_path):
    repo = CsvEnrichmentRepository.from_dir(tmp_path / "absent", plm)
    assert repo.suppliers() == []
    assert len(repo.coverage(plm).leaves_without_sourcing) == 36


def test_price_volatility_is_optional_and_validated(plm, tmp_path):
    header = "material,price_eur_per_kg,source_note,price_volatility\n"
    (tmp_path / "material_prices.csv").write_text(header + f"{BEARING},1.6,note,MEDIUM\n", encoding="utf-8")
    repo = CsvEnrichmentRepository.from_dir(tmp_path, plm)
    assert repo.material_price(BEARING).price_volatility == "medium"

    _write(tmp_path, prices=f"{BEARING},1.6,note\n")
    assert CsvEnrichmentRepository.from_dir(tmp_path, plm).material_price(BEARING).price_volatility is None

    (tmp_path / "material_prices.csv").write_text(header + f"{BEARING},1.6,note,extreme\n", encoding="utf-8")
    problems = _problems(tmp_path, plm)
    assert len(problems) == 1 and "price_volatility" in problems[0]
