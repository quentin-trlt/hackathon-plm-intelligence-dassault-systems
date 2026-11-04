# Data contract

What every module can rely on, whatever the state of the parser. The code side lives in two files:

- `src/plm/model.py`: the data classes shared by the whole app;
- `src/plm/contract.py`: the interfaces (`PlmRepository`, `EnrichmentRepository`, `CostService`) that modules code against.

Field-level origin of every PLM attribute is in [xml_mapping.md](xml_mapping.md). This document fixes the keys, units, enrichment files and computation rules on top of it. Any change to these files goes through a PR reviewed by the parser owner and by at least one module that uses them.

## Keys

| Object | Key | Format | Why |
|---|---|---|---|
| `Product` | `plm_id` | `prd-12535825-NNNNNNNN` | Unique, stable across exports, readable. Used in every function signature and as the join key for enrichment data |
| `Representation` | `plm_id` | `3sh-12535825-NNNNNNNN` | Same reasoning |
| `Instance` | `xid` | 40 hex chars | Instances have no business identifier, and `instance_name` is only unique within a parent |
| `MaterialPrice` | `material` | Exact `Material` string from the XML | Prices are per material, see [Cost rule](#cost-rule) |
| `Supplier` | `supplier_id` | `SUP-NNN` | Defined by the team in `suppliers.csv` |
| `PartSourcing` | (`plm_id`, `supplier_id`) | – | A part can have several suppliers |

The XML `@id` (`ID_41`) only exists inside one export. It is stored in `SourceRef` for traceability and never used as a key. `name` is never a key either: four products are named `ECAR-EI_GEAR_WHEEL`.

This settles open question 1 of [xml_mapping.md](xml_mapping.md#open-questions), choosing `plm_id` over `xid` as the key exposed to modules, so that the PLM model and the enrichment files share a single key.

## Units

| Quantity | Unit | Fields |
|---|---|---|
| Mass | kg | `Product.mass_kg` |
| Price | EUR | `MaterialPrice.price_eur_per_kg`, `CostService.unit_cost` |
| Lead time | days | `PartSourcing.lead_time_days` |
| Translation | m | `Instance.translation` |
| Dates | UTC | `Product.created_at`, `modified_at` |

Mass and translation units are not stated in the XML. kg and m are assumptions consistent with the values (see [xml_mapping.md](xml_mapping.md#open-questions)).

## Entities

| Entity | Key | Count | Produced by | Main consumers |
|---|---|---|---|---|
| `ExportInfo` | – | 1 | Parser | Explorer |
| `Product` | `plm_id` | 79 | Parser | All |
| `Instance` | `xid` | 112 | Parser | Explorer (3D position), traceability |
| `Representation` | `plm_id` | 36 | Parser | Explorer |
| `BomLine` | (`parent_plm_id`, `child_plm_id`) | 80 | Parser | Explorer, Risk, Change Impact |
| `Occurrence` | `path` | 81 | Parser | Change Impact, Risk |
| `MaterialPrice` | `material` | 14 | Prices | Cost, Change Impact |
| `Supplier` | `supplier_id` | – | Suppliers | Risk, Change Impact |
| `PartSourcing` | (`plm_id`, `supplier_id`) | – | Suppliers | Risk, Change Impact |

`Occurrence` is defined on BOM lines, not on individual instances: the 14 screws under `HOUSING FRONT EDRIVE` give one occurrence with `cumulative_quantity = 14`, not 14 occurrences. The expanded tree has 81 occurrences including the root.

`Product.item_kind` is derived from the structure: `assembly` for the 6 products with several children, `wrapper` for the 37 EI products holding a single leaf with quantity 1, `part` for the 36 leaves.

## Traceability

Every object read from a file carries a `SourceRef`:

| Field | XML source | CSV source |
|---|---|---|
| `origin` | `plm_xml` | `enrichment` |
| `file` | `XPDMXML_ECAR_PLM.xml` | `material_prices.csv`, ... |
| `line` | Line of the opening tag (`lxml` `sourceline`) | Line in the file, header is line 1 |
| `element` | `Product`, `ProductInst`, `Rep3DAggr`, `Info` | `None` |
| `xml_id`, `xid` | `@id`, `UniqueID/@XID` | `None` |

Every computed value is returned as `Traced[T]`: the value plus the tuple of `SourceRef` it was computed from. Risk scores, impact results and assistant answers follow the same rule. The UI shows enrichment-based values with a visible "non-PLM source" label.

## Cost rule

Prices are derived from materials, not entered per part:

```
leaf cost      = mass_kg × price_eur_per_kg(material)
assembly cost  = Σ over children (quantity × child cost)
```

Costs are computed on leaves only. A wrapper carries the same mass as its leaf, so adding both would count the part twice. Summing `mass_kg × total_quantity` over the 36 leaves gives back exactly the 100.1 kg of the root, which confirms that leaves cover the whole product.

When a leaf below a product has no material price, `unit_cost` returns `None` and the leaf appears in `missing_prices()`. A missing price is never replaced by 0. The sources of a cost are the leaves' `Product` elements, the `MaterialPrice` rows used, and the `ProductInst` elements behind each quantity.

## Enrichment files

The files sit in `data/enrichment/` and are committed: the team writes them, unlike the OEM extract. They are UTF-8 CSV with a comma separator and a decimal point. `source_note` is mandatory on every row and states where the value comes from (catalogue, public index, explicit assumption).

### `material_prices.csv`

Pre-filled with the 14 materials of the leaves, spelled exactly as in the XML.

| Column | Type | Rule |
|---|---|---|
| `material` | str | Key. Must match the XML string exactly, do not edit it |
| `price_eur_per_kg` | float | > 0 |
| `source_note` | str | Mandatory |

### `suppliers.csv`

| Column | Type | Rule |
|---|---|---|
| `supplier_id` | str | Key, `SUP-NNN` |
| `name` | str | |
| `country` | str | ISO 3166-1 alpha-2 code (`DE`, `FR`, `CN`) |
| `source_note` | str | Mandatory |

### `part_sourcing.csv`

Pre-filled with one row per leaf (36 rows), in tree order. Add one row per extra supplier of the same part; a part with a single row is single-sourced.

| Column | Type | Rule |
|---|---|---|
| `plm_id` | str | Must exist among the leaves of the XML |
| `part_name`, `material` | str | Informational, ignored by the loader |
| `supplier_id` | str | Must exist in `suppliers.csv` |
| `make_or_buy` | `make` \| `buy` | |
| `lead_time_days` | int, optional | ≥ 0 |
| `source_note` | str | Mandatory |

### Loading rules

- A row whose value columns are all empty (everything except the key and the informational columns) is "not filled yet": it is skipped and listed in the coverage report. This lets the pre-filled templates be completed in place.
- A row with some value columns filled must be complete and valid, otherwise the whole load fails with one message per problem, as `file:line: reason`.
- A missing file or directory is treated as empty, never as an error.
- `make_or_buy` is case-insensitive. A decimal comma or a `;` separator (Excel in French locale) is rejected with an explicit message.

To check the files after editing them, run `python -m plm.parser` from the repository root. It loads everything, reports any contract violation, then lists the materials without a price and the leaves without a supplier.

## Ownership

| Interface | Implemented by | Location |
|---|---|---|
| `PlmRepository` | Parser owner | `src/plm/parser/` |
| `EnrichmentRepository` | Parser owner | `src/plm/parser/` |
| `CostService` | Prices owner | `src/plm/analysis/` |
| `material_prices.csv` | Prices owner | `data/enrichment/` |
| `suppliers.csv`, `part_sourcing.csv` | Suppliers owner | `data/enrichment/` |
