# Risk rules

How `src/plm/analysis/risk.py` scores the criticality of every leaf part. All thresholds and weights live in `RiskConfig`; change them there.

## Principle

Risk = **probability** of a disruption × **impact** of that disruption, as in a classic risk heat map. Each axis is a weighted average of criteria scored from 0 (no risk) to 1 (maximum risk).

- `criticality = 100 × probability × impact` (0 to 100), used to rank parts.
- `level` comes from the 3×3 heat map below.

A criterion without data (for example no lead time) is **left out** and its axis is re-weighted over the remaining criteria. It is never scored as 0.

When no sourcing data is loaded at all (a dataset uploaded without `part_sourcing.csv`), SUPPLY and CONCENTRATION are left out as well, and the score rests on the PLM criteria and the cost share. A missing file is not the same as a part listed without supplier: inside a loaded `part_sourcing.csv`, a part with no row still scores SUPPLY 1. The stator meshing artefacts are excluded only when their `plm_id`s are present in the loaded extract.

Every criterion keeps its raw value, a sentence explaining it, and its `SourceRef`s (XML elements and CSV rows), so the UI can show why a part is critical.

## Criteria

| Code | Axis | Weight | Data | Rule |
|---|---|---|---|---|
| `SUPPLY` | Probability | 0.5 | `part_sourcing.csv` | 1 bought source: 1.0 · 1 in-house source: 0.5 · 2 sources: 0.25 · 3+: 0 · none recorded: 1.0 |
| `CONCENTRATION` | Probability | 0.3 | `part_sourcing.csv` | For a single-sourced part: `(n − 1) / 5`, capped at 1, where `n` is the number of parts single-sourced on the same supplier. 0 for multi-sourced parts |
| `MATURITY` | Probability | 0.2 | XML `Maturity`, `RevisionIndex` | State (RELEASED 0, FROZEN 0.25, other 0.5) + 0.5 × `(revision_index − 1) / 3`, capped |
| `COST_SHARE` | Impact | 0.5 | XML mass + `material_prices.csv` | Share of the root material cost taken by all units of the part, divided by 10 %, capped at 1 |
| `LEAD_TIME` | Impact | 0.35 | `part_sourcing.csv` | Shortest lead time among the sources: 30 days or less → 0, 120 days or more → 1, linear between |
| `REUSE` | Impact | 0.15 | XML structure (`where_used`) | `(occurrences − 1) / 2`, capped at 1 |

## Price exposure

Supply disruption and price exposure are two different risks: in the second one the part is delivered, but costs more. They are scored separately, so a cheap but single-sourced part and an expensive but secured part are not mixed up.

| Code | Data | Rule |
|---|---|---|
| `PRICE_VOLATILITY` | `price_volatility` column of `material_prices.csv` | low 0.2 · medium 0.5 · high 1.0 |

`price_risk = 100 × PRICE_VOLATILITY × COST_SHARE`, with `price_level` high at 50 or more, medium at 20 or more. It is `None` when the volatility or the cost is unknown. `price_ranking()` lists parts by price risk.

Volatility classes: copper is high (LME +35 % between Nov 2025 and Aug 2026), aluminium alloys medium (about +15 % over the same period). The other classes are team assumptions, stated in each `source_note`.

## Material dependency

`material_exposure()` groups the scored leaves by material: parts, physical units, mass and cost in the root product with their shares, suppliers involved, single-sourced parts and volatility. It answers "what stops or gets more expensive if this material is short or rises?". Shares are computed on the scored perimeter, so they add up to 100 %.

## Heat map

Each axis is split into bands: low (< 0.33), medium (0.33 to 0.66), high (≥ 0.66).

| | Impact low | Impact medium | Impact high |
|---|---|---|---|
| **Probability high** | medium | **high** | **high** |
| **Probability medium** | low | medium | **high** |
| **Probability low** | low | low | medium |

## Scope

- Only leaf parts are scored: they carry the material, the suppliers and the prices. `assembly_risk(plm_id)` aggregates the parts below an assembly (worst part, count per level).
- The two stator meshing artefacts (`prd-12535825-00000118` single tooth, `prd-12535825-00000119` single coil) are excluded by default (`MESHING_ARTEFACTS`): they are not physical parts.
- `supplier_exposure()` gives, per supplier, the parts it delivers, the parts that only it delivers, and the share of the material cost that would stop if it failed.

## Limits

- Supplier, make/buy and lead time data are team assumptions (`source_note` starts with `ASSUMPTION`). The scores are only as good as these assumptions.
- `MATURITY` barely discriminates on this extract: all products are `IN_WORK`.
- `COST_SHARE` uses material cost only. Bought parts such as bearings and screws cost far more than their material, so their impact is underestimated.
- Volatility is a three-level class, not a measured statistic. Only copper and aluminium are backed by market data.
