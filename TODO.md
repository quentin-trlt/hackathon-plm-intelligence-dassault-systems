# Capabilities: status and remaining work

Status as of 4 November 2026, end of D1 (updated in the evening). Every item derives from a bullet of the brief ("Solution capabilities" slide) and is limited to what our data actually holds: the XML extract (`docs/xml_mapping.md`) and the enrichment files (`docs/data_contract.md`). Ticked items are on `main`. Priorities: **P1** must be shown at the demo, **P2** strengthens the pitch, **P3** if time allows.

## What the data allows

Facts checked on the extract and the current enrichment files. They set the limits of what the application can claim.

| Topic | Reality of the data |
|---|---|
| Versions | One revision per product: 79 revision families for 79 products, no history. Revisions range from A to D (index 1 to 4): parts A 20, B 11, C 4, D 1; assemblies B 1, C 3, D 2 |
| Lifecycle | All 79 products are `IN_WORK` with the same lifecycle `VPLM_SMB_Definition_MajorRev`. All were created within 2 seconds of each other |
| 3D representations | 36, one per leaf part, always with the same revision and maturity as their part |
| Structure | 6 assemblies, 37 wrappers, 36 parts. 2 parts are used in two places (`DifferentialSideShaft`, `DifferentialBevelGearTM_Bevel`). Declared assembly masses match the roll-up of their children |
| Descriptions | Filled on 3 products out of 79 |
| Cost | Material cost only: mass x price per kg of the material (`material_prices.csv`). No purchase price, no labour, no tooling |
| Suppliers | Per leaf part: suppliers, make or buy, lead time (`part_sourcing.csv`). 19 single-sourced parts, 17 multi-sourced, 4 made in-house. No link between a supplier and the materials it can deliver |
| Quality | No quality data in the extract (no defects, tests, deviations or tolerances) |

## Capability 01: Product Explorer

Brief: navigate BOM and assemblies; inspect components and properties; compare versions and lifecycle states.

- [x] Navigate: expanded BOM tree with cumulative quantities, wrappers merged with their part or not (`app/views/explorer.py`)
- [x] Navigate: search on name, `plm_id` and material
- [x] Inspect: component card with PLM attributes, XML line, children, where-used paths, material cost, sourcing, risk criteria and sources
- [x] **P1** Compare versions and lifecycle states, within what the extract holds:
  - revision and maturity filters in the Explorer
  - revision distribution by kind and by subassembly; the products at revision C or D are the most iterated designs
  - consistency of revisions and maturities between each part, its wrapper and its 3D representation (consistent today: 36 out of 36)
  - a plain statement in the view that the extract holds a single revision per product, all `IN_WORK`, so no history can be shown
  - Done in the "Versions and lifecycle" tab of the Explorer; revision and maturity filters in the "Structure" tab
- [x] **P2** Side-by-side comparison of two or more products (attributes, mass, material, cost, suppliers, risk), for instance the four `ECAR-EI_GEAR_WHEEL`. The agent already has a `compare_products` tool
- [ ] **P3** Collapsible tree instead of an indented table

## Capability 02: Product Intelligence

Brief: identify critical components; highlight risks and dependencies; support cost and quality decisions.

- [x] Identify: risk score per leaf part, probability x impact over 6 explained and sourced criteria (`src/plm/analysis/risk.py`, rules in `docs/risk_rules.md`)
- [x] Identify: critical parts ranking in the Dashboard; criteria detail of a part and risk count of an assembly in the component card
- [x] Dependencies: supplier exposure in the Dashboard (parts delivered, single-sourced parts, share of the material cost at stake)
- [x] Cost: mass and material cost by material, material cost of the motor, material and maturity filters (`app/views/dashboard.py`)
- [x] **P1** Material dependency: display `material_exposure()`, already computed and shown nowhere (parts, units, mass and cost shares, suppliers, single-sourced parts per material)
- [x] **P1** Structural dependencies: show the parts used in several places (REUSE criterion) next to the single-sourced parts
- [x] **P2** Price exposure: display `price_ranking()` (material price volatility x cost share), already computed
- [x] **P2** Probability x impact heat map of the scored parts
- [x] **P2** Cost by subassembly: material cost, mass and risk rolled up for EMOTOR FRONT, REDUCER FRONT and HOUSING FRONT EDRIVE
- [x] **P2** Quality decisions: the extract holds no product quality data, so the honest answer is a data quality panel: consistency checks run at load time (masses, references, 3D representations), missing descriptions (76 out of 79), the two stator meshing artefacts (0.28 kg counted twice, excluded from risk), enrichment coverage
- [x] **P3** Update `PARSER.md`: it states 20 single-sourced parts, the current CSV files give 19

## Capability 03: Change Impact Analysis

Brief: select a proposed component change; find impacted products and suppliers; explore possible cost consequences.

- [x] Select: any component, with a proposed unit mass and a proposed material from the priced ones (`app/views/impact.py`)
- [x] Impacted products: every assembly above the component, with the units it contains and its projected mass, plus the where-used paths
- [x] Impacted suppliers: current suppliers of the parts concerned, with make or buy, lead time and single-source flag
- [x] Cost: change of the motor material cost, quantities included, labelled non-PLM
- [x] **P2** Replacement by another existing part of the catalogue, not only a mass or material change: current versus proposed part on mass, material, cost, suppliers, lead time and risk
- [x] **P2** Cost consequences per impacted assembly (projected material cost of each one), and price exposure of the proposed material (its volatility class)
- [x] **P2** Move the logic into a tested `ChangeImpactService` in `src/plm/analysis/`. The README already places change impact there; today it lives in the view, without tests
- [x] **P3** State the cost limit in the view: material cost only, the data holds no purchase or requalification cost

## AI assistant

Brief: query the data in natural language, retrieving and displaying the PLM sources behind each answer.

- [x] Agent with 11 tools on `load()` and the analysis services, every fact cited to its XML element or CSV row (`src/plm/agent/`)
- [x] Citation, grounding and data-origin checks before an answer is shown
- [x] Golden-question evaluation (`python -m plm.agent.eval`) and replay mode
- [x] Integrated in the Assistant view of the app; standalone test page kept (`app/assistant.py`)
- [ ] **P1** Re-record the replays with `python -m plm.agent.eval --record` (needs `PLM_LLM_API_KEY` in `.env`; do it once the data files are final, any CSV change invalidates them again): the 9 recorded answers are ignored since commit 7a822d5 changed `material_prices.csv`
- [x] **P2** Risk tool on `RiskService` (part ranking, supplier and material exposure), so that "which part is the most critical?" uses the official score
- [x] **P2** Change impact tool, once `ChangeImpactService` exists
- [ ] **P2** Golden questions on risk and change impact in the evaluation, then record them. Questions added (`most_critical`, `price_exposure`, `screw_copper_change`); recording still needs an API key, see the P1 item above

## Deliverable and cross-cutting

Brief: web application, dashboard or comparable interface.

- [x] Streamlit web application on the real parser and backend (`streamlit run app/app.py`)
- [ ] **P1** Decide what to do with the public Netlify mockup: it publishes real PLM IDs, which the README forbids for partner data
- [x] **P2** Fix the lint issues already on `main`: import order in `tests/test_risk.py`, formatting of `src/plm/analysis/risk.py`
- [x] **P3** Smoke tests of the app views with Streamlit `AppTest`
