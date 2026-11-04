# PLM Intelligence — turning a PLM extract into traceable decisions

Project 7 of the ESILV A5 DPM/PLM hackathon (4 to 6 November 2026), "PLM Intelligence Challenge", proposed by Dassault Systèmes.

We are the engineering and digital team of an electric motor supplier for the automotive industry. Our OEM customer runs its developments in 3DEXPERIENCE and only sends us a raw XML extract of the product structure. The application turns this extract into information that supports decisions on cost, risk, quality and sustainability: exploring the BOM, spotting critical components and assessing the impact of a component change. Every piece of information displayed links back to the XML element it comes from.

## Context

In this use case, the OEM is the customer and we are its electric motor supplier. The OEM manages the lifecycle of its vehicles in 3DEXPERIENCE, but we have no access to its PLM environment. Our only source is an XML extract describing the product structure, assemblies, components, their attributes, versions and maturity states.

Everything the application computes or displays must therefore be traceable to a specific element of this extract. This is the guiding principle of the project.

## Features

- **Product Explorer**: navigate the BOM and assemblies, search and filter, inspect a component and its properties, compare versions and lifecycle states.
- **Product Intelligence**: identify critical components, supplier dependencies and lifecycle risks.
- **Change Impact Analysis**: starting from a proposed component replacement, find the impacted assemblies, suppliers and costs.
- **AI assistant (optional)**: query the data in natural language, with every answer showing the PLM sources that support it.

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
| `docs/` | XML mapping, data contract, risk rules, demo scenarios |
| `src/plm/parser/` | XML and enrichment loading, mapping to the data model |
| `src/plm/analysis/` | BOM exploration, cost, risk, change impact |
| `src/plm/agent/` | AI assistant with source citation |
| `app/` | Web interface |
| `tests/` | Automated tests |
| `presentation/` | Pitch material |

## Data

The OEM XML extract lives in `data/raw/` and is versioned in this private repository. It is partner data: it must never be published or deployed publicly. The `.gitignore` excludes every `*.xml` file outside `docs/` and `data/raw/`.

The files in `data/enrichment/` hold data that is not in the extract (prices, suppliers, sourcing). They are written by the team, and every row states its source in a `source_note` column.

## Installation and usage

Requirement: Python 3.11 or later.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
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
ruff check src tests && ruff format --check src tests
```

TODO: command to launch the interface, once the framework is chosen.

## Team organisation

| Workstream | Scope | Folders | Owner |
|---|---|---|---|
| Data | XML parsing and mapping, data model | `src/plm/parser`, `docs/` | |
| Analysis | BOM graph, risks, change impact | `src/plm/analysis` | |
| UI | Interface and demo flow | `app/` | |
| Agent | AI assistant with sources (optional) | `src/plm/agent` | |
| Pitch | Demo scenarios, slides | `presentation/`, `docs/` | |

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
