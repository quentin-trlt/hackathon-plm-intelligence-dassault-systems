"""Load the extract and the enrichment files, then print a summary and the enrichment coverage."""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from plm.parser import (
    DEFAULT_ENRICHMENT_DIR,
    DEFAULT_XML_PATH,
    EnrichmentError,
    PlmParseError,
    PlmValidationError,
    load,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m plm.parser", description=__doc__)
    parser.add_argument("--xml", type=Path, default=DEFAULT_XML_PATH)
    parser.add_argument("--enrichment", type=Path, default=DEFAULT_ENRICHMENT_DIR)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    try:
        data = load(args.xml, args.enrichment)
    except (FileNotFoundError, PlmParseError, PlmValidationError, EnrichmentError) as exc:
        print(exc, file=sys.stderr)
        return 1

    plm, enrichment = data.plm, data.enrichment
    root = plm.root()
    kinds = Counter(p.item_kind for p in plm.products())
    print(f"Root: {root.name} ({root.plm_id}), {root.mass_kg} kg")
    print(
        f"Products: {len(plm.products())} ({kinds['assembly']} assemblies, {kinds['wrapper']} wrappers, "
        f"{kinds['part']} parts), instances: {len(plm.all_instances())}, occurrences: {len(plm.occurrences())}"
    )
    print(f"Warnings: {len(plm.warnings)}")

    report = enrichment.coverage(plm)
    print(f"\nMaterial prices: {len(enrichment.material_prices())}, suppliers: {len(enrichment.suppliers())}")
    print(f"Unfilled template rows: {len(report.unfilled_rows)}")
    if report.materials_without_price:
        print(f"Materials without price ({len(report.materials_without_price)}):")
        for material in report.materials_without_price:
            print(f"  {material}")
    if report.leaves_without_sourcing:
        print(f"Leaves without supplier ({len(report.leaves_without_sourcing)}):")
        for plm_id in report.leaves_without_sourcing:
            print(f"  {plm_id}  {plm.product(plm_id).name}")
    if report.leaves_without_material:
        print(f"Leaves without material: {', '.join(report.leaves_without_material)}")
    print("\nEnrichment complete." if report.complete else "\nEnrichment incomplete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
