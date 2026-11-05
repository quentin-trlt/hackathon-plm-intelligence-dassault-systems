# PLM Intelligence — turning a PLM extract into traceable decisions

Project 7 of the ESILV A5 DPM/PLM hackathon (4 to 6 November 2026), "PLM Intelligence Challenge", proposed by Dassault Systèmes.

We are the engineering and digital team of an electric motor supplier for the automotive industry. Our OEM customer runs its developments in 3DEXPERIENCE and only sends us a raw XML extract of the product structure. The application turns this extract into information that supports decisions on cost, risk, quality and sustainability: exploring the BOM, spotting critical components and assessing the impact of a component change. Every piece of information displayed links back to the XML element it comes from.

Static web mockup: https://dassault-systemes-plm-project.netlify.app/ (source in `presentation/web_mockup/`; hardcoded data, not connected to the parser).

## Context

In this use case, the OEM is the customer and we are its electric motor supplier. The OEM manages the lifecycle of its vehicles in 3DEXPERIENCE, but we have no access to its PLM environment. Our only source is an XML extract describing the product structure, assemblies, components, their attributes, versions and maturity states.

Everything the application computes or displays must therefore be traceable to a specific element of this extract. This is the guiding principle of the project.

## Features

- **Product Explorer**: navigate the BOM and assemblies, search and filter, inspect a component and its properties, compare versions and lifecycle states.
- **Product Intelligence**: identify critical components, supplier dependencies and lifecycle risks.
- **Change Impact Analysis**: starting from a proposed component replacement, find the impacted assemblies, suppliers and costs.
- **AI assistant**: ask questions about the motor in natural language. Every figure is computed by the analysis code and every answer cites the XML elements and enrichment rows it comes from. See [AI assistant](#ai-assistant).

## Business questions

| Question | What the application must answer |
|---|---|
| Understand | What are the major subassemblies of the motor? Which versions and properties exist? |
| Identify risks | Which components are critical? Where are the supplier or lifecycle dependencies? |
| Assess a change | If a component is replaced, which assemblies, suppliers and costs are affected? |

## Architecture

```
OEM XML ──► parser ──► data model ──┬──► analysis ──┬──► interface
                                    └──► agent ─────┘
```

The parser reads the extract and translates it into an internal data model that keeps, for every object, a reference to its original XML element. The analysis modules and the agent work on this model only, and the interface displays their results together with their sources.

| Folder | Role |
|---|---|
| `data/raw/` | XML extract provided by the OEM (see [Data](#data)) |
| `data/enrichment/` | Non-PLM data written by the team: material prices, suppliers, part sourcing |
| `data/agent_replays/` | Assistant answers recorded for offline tests and rehearsals, off by default (see [Replay mode](#replay-mode)) |
| `docs/` | XML mapping, data contract, risk rules, assistant design |
| `src/plm/parser/` | XML and enrichment loading, mapping to the data model |
| `src/plm/analysis/` | BOM exploration, cost, risk, change impact |
| `src/plm/agent/` | AI assistant with source citation |
| `app/` | Streamlit interface: `app.py` entry point, `services.py` data access, one module per view in `views/`; `app/assistant.py` is the assistant's standalone test page |
| `tests/` | Automated tests |
| `presentation/` | Pitch material; `web_mockup/` holds the first static UI mockup |

## Data

The OEM XML extract lives in `data/raw/` and is versioned in this private repository. It is partner data: it must never be published or deployed publicly. The `.gitignore` excludes every `*.xml` file outside `docs/` and `data/raw/`.

The files in `data/enrichment/` hold data that is not in the extract (prices, suppliers, sourcing). They are written by the team, and every row states its source in a `source_note` column.

The AI assistant sends tool results to the Groq API: product names, masses, quantities, suppliers. It never sends the whole extract. The team approved this use of a cloud API for the hackathon. The answers recorded in `data/agent_replays/` are derived from partner data and follow the same rules as the extract.

## Installation and usage

Requirement: Python 3.11 or later.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"        # parser, analysis, tests
pip install -e ".[dev,app]"    # adds the AI assistant and its Streamlit page
```

Load the extract and the enrichment files, then print a summary and the enrichment coverage:

```bash
python -m plm.parser
```

In code, everything goes through a single entry point. The full guide (objects, API, examples per module, pitfalls) is in [PARSER.md](PARSER.md).

```python
from plm.parser import load

data = load()
data.plm.where_used("prd-12535825-00000081")
data.enrichment.coverage(data.plm)
```

Tests and lint:

```bash
pytest
ruff check src tests app && ruff format --check src tests app
```

Launch the main interface (Overview, Product structure, Risks, Supply and materials, Change impact, Assistant):

```bash
streamlit run app/app.py
```

Every figure it shows comes from `load()` and the analysis services; values from `data/enrichment/` are labelled "non-PLM source". The Assistant view needs the setup described in [AI assistant](#ai-assistant).

The **Change dataset** button in the sidebar loads another XPDMXML extract in the same format, with its enrichment files (`material_prices.csv`, `suppliers.csv`, `part_sourcing.csv`) as optional uploads. The files are checked like the reference ones: a malformed extract or a CSV that breaks [the data contract](docs/data_contract.md) is refused and the active dataset is kept. They are kept in a temporary folder for the browser session only, never in the repository. Figures, charts and columns that need a missing file say so instead of showing a value, and the risk score then rests on the criteria that remain (see [docs/risk_rules.md](docs/risk_rules.md)). The assistant works on the active dataset; recorded replays only exist for the reference extract.

The **3D view** draws the product from the viewer exported for the extract (`data/raw/viewer_3d.html` for the reference one): the whole product in the "3D view" tab of Product structure, the selected product in the "3D" tab of its card, the changed component and its subassembly in Change impact, and every part in the colour of its risk level in Risks. Shapes are schematic primitives sized from mass and typical density, not the OEM CAD; most positions are read from the XML and the others are estimated, as each view states. The viewer can be uploaded with another extract. Only its `const DATA` JSON is read and type-checked, then drawn in the app's own template (`app/assets/viewer_3d_template.html`); its code never runs. A viewer whose parts are not in the extract is refused. The reference viewer holds partner data and follows the same rules as the XML.

## AI assistant

The assistant answers questions such as "Which supplier do we depend on the most?" or "If the differential side shaft is replaced, what is impacted?", in English or French. The design and its test results are in [docs/agent_plan.md](docs/agent_plan.md).

### How it works

```
question ─► LLM ─► tool calls ─► plm / enrichment / analysis (Traced + SourceRef)
              ▲                              │
              └──── compact JSON + [E1..En] ─┘
LLM ─► answer citing [En] ─► citation check ─► answer with its sources
```

The model never reads the extract. It calls Python tools built on `load()` and on the analysis services: search, product card, BOM children, where-used, quantities, material costs, sourcing, supplier dependencies, comparisons and aggregations. Every fact a tool returns carries an evidence id, which groups the `SourceRef`s behind it: XML line and element, or CSV row. Totals, shares and costs are computed by the tools, never by the model.

Before an answer is shown, three checks run:

- **Citations**: ids the model invented are removed, and sentences with a figure but no citation are flagged.
- **Grounding**: an answer that cites no evidence, and does not rest on tool results, is replaced by a scope message. This also covers off-topic requests.
- **Data origin**: values from `data/enrichment/` are labelled "non-PLM source", and costs are material costs only.

The model is `openai/gpt-oss-120b` on the Groq free tier, called through the OpenAI-compatible API. Another provider only needs `PLM_LLM_BASE_URL` and `PLM_LLM_MODEL`.

### Setup

Create a free key at console.groq.com/keys, then:

```bash
cp .env.example .env    # paste the key in PLM_LLM_API_KEY; .env is never committed
```

`.env.example` lists the other settings: model, step limit, retries and replay mode.

### Usage

```bash
streamlit run app/app.py                           # Assistant view of the main interface
streamlit run app/assistant.py                     # standalone test page: chat, sources, tool calls
python -m plm.agent "Which parts are single-sourced?" --trace
python -m plm.agent                                # interactive session in the terminal
python -m plm.agent.eval                           # golden-question evaluation (live model)
```

`python -m plm.agent.eval` runs 17 reference questions and checks each answer automatically. The checks cover figures, names, the tools used, citations, and the refusal of off-topic requests. Expected values are computed from the current data, so the evaluation stays valid when the enrichment files change. The evaluation needs the network and is not part of `pytest`. The offline tests cover the tools, the loop (with a scripted model), the citation check, the evaluation checks and the replay mode.

### Replay mode

The free tier allows 8,000 tokens per minute and 200,000 tokens per day per Groq organisation. A question costs 5,000 to 14,000 tokens, so the team gets a few dozen live questions a day. Answers that pass the evaluation can be recorded and replayed:

```bash
python -m plm.agent.eval --record    # stores passing answers in data/agent_replays/answers.json
```

| `PLM_AGENT_REPLAY` | Behaviour |
|---|---|
| `off` (default) | Every question goes to the model |
| `fallback` | Recorded answer if there is one, live model otherwise. Use it for rehearsals |
| `only` | Recorded answers only. Needs no API key and no network |

Each recorded answer stores a fingerprint of the XML and CSV files, and is ignored as soon as the data changes. A replayed answer is always labelled with its recording date.

### Current limits

- Risk scoring and change-impact simulation are not tools yet. They will wrap the analysis services once those exist.
- The model sometimes writes a simple sum itself, or cites a table once instead of row by row. The uncited-figure warning shows both.
- An off-topic answer that cites a real evidence id would pass the grounding check. The scope rule in the prompt is the only defence against it.

## Team organisation

| Workstream | Scope | Folders | Owner |
|---|---|---|---|
| Data | XML parsing and mapping, data model | `src/plm/parser`, `docs/` | |
| Analysis | BOM graph, risks, change impact | `src/plm/analysis` | |
| UI | Interface and demo flow | `app/` | |
| Agent | AI assistant with sources | `src/plm/agent`, `app/views/assistant.py`, `app/assistant.py` | |
| Pitch | Slides | `presentation/` | |

## Git conventions

- `main` is demo-ready at all times.
- Short-lived branches named `feat/<workstream>-<topic>` or `fix/<workstream>-<topic>`.
- Every change goes through a PR to `main`, reviewed by at least one person.
- Squash merge.
- Frequent merges, every 2 to 3 hours, to limit divergence.

## Schedule

| Day | Goals |
|---|---|
| D1 (4 Nov.) | XML analysis and Product Explorer |
| D2 (5 Nov.) | Risks, change impact and assistant |
| D3 (6 Nov.) | Demo finalisation and pitch |

## Team

- Quentin TARLET
- Chloé DAUNAS
- Ayoub EL AMRANI
- Shubham BOLLU
- Téo MAHIEU
- Kishan Kumar MANTHRI
- Saad ZAINOU
