# Parser and data access

This guide explains how the parser turns the OEM XML extract into Python objects, and how every module (Explorer, Risk, Change Impact, Assistant) accesses the data. Ground rule: nobody reads the XML or the CSV files by hand. Everything goes through `load()`, which returns validated, traceable data.

For the field-by-field mapping, see [docs/xml_mapping.md](docs/xml_mapping.md). For keys, units and the CSV formats, see [docs/data_contract.md](docs/data_contract.md).

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Check that everything loads, from the repository root:

```bash
python -m plm.parser
```

The command prints the root, the object counts and how complete the enrichment CSV files are. If a file breaks the contract, it prints every problem as `file:line: reason` and stops.

## What the parser does

```
data/raw/XPDMXML_ECAR_PLM.xml ──► XmlPlmRepository ──┐
                                                     ├──► load() ──► data.plm, data.enrichment
data/enrichment/*.csv ─────────► CsvEnrichmentRepository ┘
```

1. It reads the XML with `lxml` and converts every `Product`, `ProductInst` and `Rep3DAggr` into a typed object.
2. It checks consistency: a single root, no broken reference, no cycle, unique identifiers, consistent masses. A blocking inconsistency raises an explicit exception.
3. It computes what the XML does not hold directly: BOM lines with quantities, the expanded tree, the kind of each product.
4. It reads the three enrichment CSV files, validates them and cross-checks them against the XML.

Every object keeps a reference to its origin (`SourceRef`): file, line, `@id` and `XID` for the XML, file and line for a CSV.

## Loading the data

```python
from plm.parser import load

data = load()
plm = data.plm                # product structure (XML)
enrichment = data.enrichment  # material prices, suppliers, sourcing (CSV)
```

The default paths are `data/raw/XPDMXML_ECAR_PLM.xml` and `data/enrichment/`, relative to the current directory. Run from the repository root, or pass the paths explicitly: `load(xml_path, enrichment_dir)`.

Loading takes a fraction of a second. In a Streamlit interface, cache it once:

```python
import streamlit as st

@st.cache_resource
def get_data():
    return load()
```

## Objects

All objects are immutable dataclasses defined in `src/plm/model.py`. Units are fixed: kg, EUR, days, metres, UTC dates.

| Object | What it is | Key | Count |
|---|---|---|---|
| `Product` | A part or an assembly, independent of where it is used | `plm_id` | 79 |
| `Instance` | One use of a product inside a parent, with its 3D position | `xid` | 112 |
| `BomLine` | Instances grouped by (parent, child) pair, with the quantity | (parent, child) | 80 |
| `Occurrence` | One node of the expanded tree: the path from the root and the cumulative quantity | `path` | 81 |
| `Representation` | The 3D representation of a leaf part (no geometry file) | `plm_id` | 36 |
| `SourceRef` | Where a value comes from: file, line, `@id`, `XID` | – | – |
| `Traced[T]` | A computed value (`.value`) and all its sources (`.sources`) | – | – |
| `MaterialPrice` | Price of a material in EUR/kg | `material` | 14 |
| `Supplier` | A supplier | `supplier_id` | 15 |
| `PartSourcing` | Link from a part to a supplier, with make/buy and lead time | (`plm_id`, `supplier_id`) | 52 |

### Useful `Product` fields

| Field | Example | Note |
|---|---|---|
| `plm_id` | `prd-12535825-00000081` | The key to use everywhere |
| `name` | `ECAR-3D_HSH_BEA3` | For display, never as a key |
| `name_normalized` | `ECAR-3D_HSH_BEA3` | Whitespace and prefixes harmonised, used by search |
| `revision`, `revision_index` | `A`, `1` | |
| `maturity`, `lifecycle` | `IN_WORK` | Same on all 79 products of this extract |
| `mass_kg` | `0.55` | Unit mass |
| `material` | `Bearing steel 100Cr6 (AISI 52100)` | `None` on the 6 real assemblies |
| `description` | `None` | Filled on 3 products only |
| `item_kind` | `part` | `assembly`, `wrapper` or `part` (see below) |
| `is_root`, `is_leaf` | `False`, `True` | |
| `source` | `SourceRef(..., line=78, xml_id='ID_3', ...)` | To show the origin in the XML |

### Assemblies, wrappers and parts

The extract follows a modelling convention you need to know:

- **`part`** (36): the physical `ECAR-3D_*` parts, always leaves. They carry the material, the 3D representation, the suppliers and the prices.
- **`wrapper`** (37): logical `ECAR-EI_*` items holding a single part, with the same mass and material. The Explorer can merge them with their part.
- **`assembly`** (6): the real assemblies, namely the root `410KW_EDRIVE_FRONT`, `EMOTOR FRONT`, `STATOR`, `REDUCER FRONT`, `DIFFERENTIAL_ASSEMBLY` and `HOUSING FRONT EDRIVE`.

Exception: `ECAR-3D_MOTOR SCREW 2` sits directly under the housing, with no wrapper.

## API reference

### `data.plm`

| Method | Returns | Use |
|---|---|---|
| `root()` | `Product` | The motor root |
| `product(plm_id)` | `Product` | Raises `UnknownProductError` if the `plm_id` does not exist |
| `products()` | `list[Product]` | The 79 products, in file order |
| `leaves()` | `list[Product]` | The 36 leaf parts |
| `search(text)` | `list[Product]` | Case-insensitive search on name, `plm_id` and material |
| `children(plm_id)` | `list[BomLine]` | Direct children, in PLM order |
| `parents(plm_id)` | `list[BomLine]` | Direct parents |
| `occurrences()` | `list[Occurrence]` | The whole expanded tree, root first |
| `where_used(plm_id)` | `list[Occurrence]` | Every place the product appears |
| `total_quantity(plm_id)` | `Traced[int]` | Number of units in the complete motor |
| `instances(parent, child)` | `list[Instance]` | The instances behind a BOM line (3D position, instance name) |
| `representation(plm_id)` | `Representation \| None` | The 3D representation of a leaf |
| `export_info` | `ExportInfo` | Export metadata |
| `warnings` | `tuple[str, ...]` | Non-blocking anomalies found at load time (none on this extract) |

### `data.enrichment`

| Method | Returns | Use |
|---|---|---|
| `material_price(material)` | `MaterialPrice \| None` | Price of a material, from the exact XML string |
| `material_prices()` | `list[MaterialPrice]` | All prices |
| `suppliers()` | `list[Supplier]` | All suppliers |
| `supplier(supplier_id)` | `Supplier` | Raises `UnknownSupplierError` if absent |
| `sourcing(plm_id)` | `list[PartSourcing]` | One entry per supplier able to deliver the part, empty if unknown |
| `coverage(plm)` | `CoverageReport` | Materials without price, parts without supplier, unfilled CSV rows |

### Costs

Cost computation lives in `src/plm/analysis/cost.py`. It applies the contract rule: leaf cost = mass × material price, assembly cost = sum of children weighted by quantity.

```python
from plm.analysis.cost import MaterialCostService

costs = MaterialCostService(data.plm, data.enrichment)
costs.unit_cost(plm.root().plm_id).value   # 266.68 (EUR, material cost of the motor)
costs.missing_prices()                     # []: everything can be priced
```

`unit_cost` returns `None` as soon as a part below has no price, never 0. This is a material cost only: for bought parts such as bearings and screws, the real price is much higher.

## Examples per module

Every example below was run on the real extract, and the outputs shown in comments are the ones obtained.

### Explorer: browsing the tree

```python
root = plm.root()
for line in plm.children(root.plm_id):
    child = plm.product(line.child_plm_id)
    print(line.quantity, child.name, child.mass_kg)
# 1 ECAR-EI_EMOTOR FRONT 39.73
# 1 ECAR-EI_REDUCER FRONT 29.85
# 1 ECAR-EI_HOUSING FRONT EDRIVE 30.52
```

Full recursive walk:

```python
def walk(plm_id: str, depth: int = 0, quantity: int = 1) -> None:
    product = plm.product(plm_id)
    print(f"{'  ' * depth}{product.name} x{quantity} [{product.item_kind}]")
    for line in plm.children(plm_id):
        walk(line.child_plm_id, depth + 1, line.quantity)

walk(plm.root().plm_id)
```

### Explorer: component card, search and filters

```python
part = plm.product("prd-12535825-00000081")
part.name, part.revision, part.maturity, part.material, part.mass_kg
# ('ECAR-3D_HSH_BEA3', 'A', 'IN_WORK', 'Bearing steel 100Cr6 (AISI 52100)', 0.55)
plm.representation(part.plm_id).name   # 'ECAR-01_HSH_BEA3'
part.source.line                       # 78, line of the <Product> in the XML

[p.name for p in plm.search("bearing")][:3]
# ['ECAR-EI_HSH_BEA3', 'ECAR-3D_HSH_BEA3', 'ECAR-EI_01_HSH_BEA1']

aluminium = [p for p in plm.leaves() if p.material and "Aluminium" in p.material]
```

Search copes with naming inconsistencies: `plm.search("machine cooling")` finds `ECAR-EI_ MACHINE COOLING` despite the stray space.

### Risk: supplier dependencies

```python
from collections import Counter

single_source = [leaf for leaf in plm.leaves() if len(enrichment.sourcing(leaf.plm_id)) == 1]
len(single_source)   # 20 single-source parts out of 36

dependency = Counter(s.supplier_id for leaf in plm.leaves() for s in enrichment.sourcing(leaf.plm_id))
for supplier_id, count in dependency.most_common(3):
    supplier = enrichment.supplier(supplier_id)
    print(supplier_id, supplier.name, supplier.country, count)
# SUP-003 GKN Driveline (Dowlais) GB 8
# SUP-001 SKF Automotive SE 7
# SUP-002 Schaeffler Technologies (INA/FAG) DE 7
```

### Risk and sustainability: mass per material

To get the actual mass in the motor, multiply by the total quantity. Use leaves only, otherwise wrappers count twice:

```python
from collections import defaultdict

mass_by_material = defaultdict(float)
for leaf in plm.leaves():
    mass_by_material[leaf.material] += leaf.mass_kg * plm.total_quantity(leaf.plm_id).value
# Electrical steel M235-35A (laminated): 21.62
# Case-hardening steel 20MnCr5: 20.50
# Aluminium alloy EN AC-AlSi9Cu3(Fe) (HPDC): 19.60
```

The sum over all leaves gives back the 100.1 kg of the root.

### Change Impact: where a part is used

```python
screw = next(p for p in plm.leaves() if p.name == "ECAR-3D Screw D10 L155")
for occ in plm.where_used(screw.plm_id):
    print(" > ".join(plm.product(pid).name for pid in occ.path), occ.cumulative_quantity)
# ECAR-EI_410KW_EDRIVE_FRONT > ECAR-EI_HOUSING FRONT EDRIVE > ECAR-EI_Screw D10 L155 > ECAR-3D Screw D10 L155 14
```

Assemblies impacted by replacing a shared part:

```python
shaft = next(p for p in plm.leaves() if p.name == "ECAR-3D_DifferentialSideShaft")
impacted = {pid for occ in plm.where_used(shaft.plm_id) for pid in occ.path[:-1]}
sorted(plm.product(pid).name for pid in impacted)
# ['ECAR-EI_410KW_EDRIVE_FRONT', 'ECAR-EI_DIFFERENTIAL SIDE SHAFT LEFT',
#  'ECAR-EI_DIFFERENTIAL SIDE SHAFT RIGHT', 'ECAR-EI_DIFFERENTIAL_ASSEMBLY', 'ECAR-EI_REDUCER FRONT']

[(s.supplier_id, enrichment.supplier(s.supplier_id).name, s.lead_time_days) for s in enrichment.sourcing(shaft.plm_id)]
# [('SUP-004', 'Dana Incorporated', 84), ('SUP-003', 'GKN Driveline (Dowlais)', 98)]
```

### Assistant and interface: showing sources

Every displayed value must trace back to its origin. Objects read from files carry `.source`, computed values are `Traced` with `.sources`:

```python
quantity = plm.total_quantity(screw.plm_id)
quantity.value          # 14
len(quantity.sources)   # 17: the root, the housing instance, the 14 screw instances and the part instance

def cite(ref) -> str:
    if ref.origin == "plm_xml":
        return f"{ref.file}, line {ref.line}, <{ref.element} id=\"{ref.xml_id}\">"
    return f"{ref.file}, line {ref.line} (non-PLM source)"
```

Values coming from the CSV files (`origin == "enrichment"`) must be flagged as non-PLM in the interface. Each CSV row also carries a `source_note` explaining where the figure comes from.

## Pitfalls

- **Never use the name as a key.** Four products are called `ECAR-EI_GEAR_WHEEL`: they are four different gear wheels (1.3 to 7.4 kg), not four versions. Always go through `plm_id`.
- **Quantities live on `BomLine`**, not on products. `line.quantity` is the quantity under one parent, `plm.total_quantity(plm_id).value` the quantity in the whole motor.
- **Masses and costs: leaves only** when summing over a flat list. A wrapper has the same mass as its part, so adding both counts the part twice. Rolling up through `children()` is always correct.
- **A part can appear in several places.** `DifferentialSideShaft` is used under two parents: `where_used` returns two occurrences.
- **Maturity tells nothing apart** in this extract: everything is `IN_WORK`, and there is no version history.
- **The stator probably double counts a tooth and a coil** modelled for meshing (0.28 kg in total). `MaterialCostService` accepts an `excluded_plm_ids` parameter to leave them out if the team decides so.
- **Objects are immutable.** For derived data (score, impact status), build a separate structure keyed by `plm_id` instead of modifying the objects.

## Editing enrichment data

The CSV files in `data/enrichment/` are versioned and editable by the team. After every change:

```bash
python -m plm.parser
pytest
```

The rules (mandatory columns, `source_note` on every row, decimal point, comma separator) are in [docs/data_contract.md](docs/data_contract.md#enrichment-files). A row left entirely empty is skipped and listed in the coverage report. A partially filled row makes loading fail, with the offending line reported.

## Load-time errors

| Exception | Cause |
|---|---|
| `FileNotFoundError` | The XML is not where expected (run from the repository root) |
| `PlmParseError` | Malformed XML, or a mandatory field missing or unreadable |
| `PlmValidationError` | Inconsistent structure: broken reference, cycle, duplicate, orphan product |
| `EnrichmentError` | A CSV breaks the contract; `exc.problems` lists every problem with its line |

## Where the code is

| File | Content |
|---|---|
| `src/plm/model.py` | Shared dataclasses |
| `src/plm/contract.py` | The `PlmRepository`, `EnrichmentRepository` and `CostService` interfaces |
| `src/plm/parser/xml_reader.py` | XML reading and validation |
| `src/plm/parser/enrichment.py` | CSV reading and validation, coverage report |
| `src/plm/parser/loader.py` | The `load()` entry point |
| `src/plm/analysis/cost.py` | Material cost computation |
| `tests/` | Parser and enrichment tests |
