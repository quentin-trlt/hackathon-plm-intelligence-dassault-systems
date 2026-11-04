# AI assistant — implementation plan

The assistant answers engineering questions about the motor in natural language, and every fact it states links back to the XML element or the enrichment CSV row it comes from. It is a tool-calling agent: the LLM never reads the extract, it calls Python tools built on `load()` and on the analysis services, and writes the answer from their results. Numbers are computed by the tools, citations are produced by the code.

```
question ─► LLM ─► tool calls ─► plm / enrichment / analysis (Traced + SourceRef)
              ▲                              │
              └──── compact JSON + [E1..En] ─┘
LLM ─► answer citing [En] ─► citation check ─► UI (evidence panel, links to Explorer)
```

## Decisions

| Topic | Decision | Reason |
|---|---|---|
| Pattern | Tool-calling agent over the `plm` API | Data is small and structured; aggregations and BOM walks must be exact; sources come from `Traced` |
| LLM provider | Groq free tier, through the OpenAI-compatible endpoint | Free, fast, good multi-step tool calling. Cloud use was approved by the team |
| Provider coupling | `openai` SDK with a configurable `base_url` | Switching to Ollama, OpenRouter or Mistral is an environment variable, not a code change |
| Framework | Hand-written agent loop (~150 lines) | Easier to debug in 48 h than LangChain or LlamaIndex, no hidden prompts |
| UI | Streamlit chat tab (`st.chat_message`, `st.chat_input`) | Matches the caching advice in PARSER.md. The agent package stays UI-agnostic |
| Text-to-SQL | Not in the core. Optional `run_query` tool in phase 3 | SQL from free models is unreliable on recursive BOM queries and weakens traceability |

### Model choice (phase 0, 4 Nov.)

`python -m plm.agent.spike` runs four questions with two tools (`search_products`, `get_total_quantity`). Q3 is a trap: wrapper plus part gives 36 instead of 18. Q4 asks for a product that is not in the extract. The text models available on the team's Groq key were `openai/gpt-oss-120b`, `openai/gpt-oss-20b` and `qwen/qwen3.8-27b`; `llama-3.3-70b-versatile` returned 404.

| Model | Automatic checks | Latency per question | Tokens per question | Problems |
|---|---|---|---|---|
| `openai/gpt-oss-120b` | 3/3 | 2 to 5 s | 1,900 to 3,200 | Q4: kept searching until the step limit |
| `openai/gpt-oss-20b` | 1/3 | 3 to 5 s | 1,000 to 3,600 | Gave up after one failed search; leaked raw `<\|channel\|>` tokens into an answer |
| `qwen/qwen3.8-27b` | 2/3 | 16 to 20 s | about 2,500 | Called tools that do not exist (`search_results`), which Groq rejects with HTTP 400 |

Decision: `openai/gpt-oss-120b` is the default. There is no automatic fallback model, since neither alternative is reliable; the replay mode covers the demo.

### Phase 0 findings that change phase 1

- `plm.search` normalised product names but not the query, so the exact XML name `ECAR-3D Screw D10 L155` returned nothing. Fixed in the parser: query and fields are compared case-insensitively, with underscores and runs of spaces treated as one space. On top of that, `search_products` falls back to an all-words match when the substring search finds nothing (for example "differential side shaft" against `DifferentialSideShaft`).
- In Q3, gpt-oss-120b counted wrappers instead of parts, despite the prompt. The total was right only because a wrapper has the same quantity as its part. Tools return one entry per physical part, carrying the `wrapper_plm_id`, so the model never sees the pair.
- Q4 burned all 6 steps on searches. The data overview lists the 36 part names (about 400 tokens), the prompt says to conclude "not in the extract" after two empty searches, and the loop's last call without tools guarantees an answer.
- gpt-oss writes citations as `【E3】` as well as `[E3]`. `citations.py` accepts both and normalises them.
- Groq can reject a malformed tool call with HTTP 400 `tool call validation failed`. The loop catches it and retries once, adding a message that lists the valid tool names.
- Budget: about 2,500 tokens per question with 2 tools. With 12 tool schemas, expect 5,000 to 6,000. Check the per-minute and per-day limits at console.groq.com/settings/limits before rehearsals.

## Package layout

```
src/plm/agent/
├── __init__.py       PlmAgent, AgentAnswer, Evidence
├── __main__.py       CLI: python -m plm.agent "question"
├── config.py         AgentConfig, read from the environment
├── llm.py            Thin client over the OpenAI SDK: retries, timeout, 429 handling
├── evidence.py       Evidence, EvidenceRegistry
├── tools.py          Tool definitions (JSON schema + handler) and ToolContext
├── prompts.py        System prompt and data overview
├── citations.py      Citation extraction and validation
└── agent.py          The loop: PlmAgent.ask()
app/
└── assistant.py      Streamlit chat tab (integrated by the UI lead)
tests/
├── test_agent_tools.py     Tools on the real extract, no LLM
├── test_agent_loop.py      Loop with a scripted fake LLM, no network
└── agent_eval/
    ├── questions.json      Golden questions with expected values
    └── run_eval.py         Live evaluation against the configured model
```

Dependencies go in a new optional group so the parser and analysis keep depending on `lxml` only:

```toml
[project.optional-dependencies]
agent = ["openai>=1.40", "python-dotenv>=1.0"]
app = ["streamlit>=1.38"]
```

## Configuration

`AgentConfig` is a frozen dataclass built by `AgentConfig.from_env()`:

| Variable | Default | Use |
|---|---|---|
| `PLM_LLM_API_KEY` | – (required) | Groq API key |
| `PLM_LLM_BASE_URL` | `https://api.groq.com/openai/v1` | Any OpenAI-compatible endpoint |
| `PLM_LLM_MODEL` | chosen in phase 0 | Model id |
| `PLM_AGENT_MAX_STEPS` | `6` | Maximum LLM calls per question |
| `PLM_AGENT_TEMPERATURE` | `0` | Deterministic answers for the demo |

The key lives in a local `.env`, already excluded by `.gitignore`, and is loaded with `python-dotenv`. A `.env.example` with empty values is committed. A missing key raises an explicit error at startup, not at the first question.

## Evidence registry

`SourceRef` identifies one element, but a computed value often has many: `total_quantity` of the D10 screw has 17 sources. Citing 17 ids per fact would bloat both the prompt and the answer, so the agent cites evidence, not raw refs.

```python
@dataclass(frozen=True, slots=True)
class Evidence:
    id: str                         # "E3"
    label: str                      # "Total quantity of ECAR-3D Screw D10 L155"
    refs: tuple[SourceRef, ...]     # every SourceRef behind the value
    plm_id: str | None              # for the "open in Explorer" link
    note: str | None                # source_note of the CSV row, for enrichment values
```

`EvidenceRegistry` belongs to one conversation, so ids stay stable across turns and an `[E3]` in an earlier answer still resolves. `register(label, refs, plm_id, note)` returns the id, and registering the same `(label, refs)` twice returns the same id. `is_enrichment` is true when any ref has `origin == "enrichment"`, and the UI then shows the "non-PLM source" label required by the data contract.

## Tools

Every handler takes a `ToolContext` (`plm`, `enrichment`, `costs`, `registry`) and JSON arguments. It returns a compact JSON-serialisable dict where every fact carries an `evidence` id. Common rules:

- Products are addressed by `plm_id` only. Names are never accepted as keys, since four products are called `ECAR-EI_GEAR_WHEEL`. The model must call `search_products` first.
- Lists are capped (default 10, max 30) and report `total` and `truncated`, so a result never exceeds about 2,000 characters.
- Domain errors (`UnknownProductError`, `UnknownSupplierError`) are returned as `{"error": "..."}` so the model can recover. Anything else propagates.
- Values from the CSV files carry `"origin": "enrichment"` so the model can say they are not PLM data.

### Phase 1 tools, built on what exists today

| Tool | Arguments | Returns | Built on |
|---|---|---|---|
| `search_products` | `query`, `kind?` (`part`, `assembly`), `limit?` | `plm_id`, name, kind, material, mass, evidence | `plm.search` |
| `get_product` | `plm_id` | Full card: revision, maturity, material, mass, kind, representation, number of suppliers | `plm.product`, `plm.representation`, `enrichment.sourcing` |
| `get_children` | `plm_id` | Direct BOM lines: child, quantity, kind | `plm.children` |
| `get_where_used` | `plm_id` | Paths from the root (names), cumulative quantity, set of impacted assemblies | `plm.where_used` |
| `get_total_quantity` | `plm_id` | Units in the whole motor | `plm.total_quantity` |
| `get_unit_cost` | `plm_id` | Material cost in EUR, or `null` with the leaves lacking a price | `MaterialCostService` |
| `compare_products` | `plm_ids` (max 10) | One compact card per product: mass, material, assemblies, quantity, cost, suppliers | All of the above. Added in phase 1 for homonyms, see below |
| `get_sourcing` | `plm_id` | Suppliers, country, make/buy, lead time, `source_note` | `enrichment.sourcing`, `enrichment.supplier` |
| `get_supplier_dependencies` | `supplier_id?`, `single_source_only?` | Parts per supplier, single-source parts | `enrichment.sourcing` over the leaves |
| `aggregate_leaves` | `group_by` (`material`, `supplier`, `country`, `subassembly`, `assembly`), `metric` (`mass_kg`, `material_cost_eur`, `count`), `top?` | Sorted groups with value and evidence | Leaves × `total_quantity` × cost |

`aggregate_leaves` answers most of the "advanced" questions (mass per material, cost per subassembly, dependency on a country) without text-to-SQL. It always works on leaves weighted by `total_quantity`, the rule from PARSER.md that avoids counting wrappers twice. The test checks that the mass groups add up to the 100.1 kg of the root.

### Phase 2 tools, wrapping the analysis modules

| Tool | Wraps | Owner |
|---|---|---|
| `get_risk` (`plm_id?`, `top?`) | Risk scoring service | Risk lead |
| `simulate_change` (`plm_id`, `new_material?`, `new_supplier_id?`) | Change impact service | Change Impact lead |
| `open_view` (`tab`, `plm_id`) | No data. Returns a UI action ("open in Change Impact") | Agent |

The agent wraps these services and does not reimplement them, so the assistant and the dashboard always show the same figures. The contract to agree on today with the two owners, to be added to `src/plm/contract.py`, is that every public method returns `Traced[...]` and takes `plm_id` arguments. The agent can then build evidence without knowing the internals.

### Phase 3, optional

`run_query(sql)` over an in-memory DuckDB holding `products`, `bom_lines`, `sourcing` and `suppliers`, each with `source_file`, `source_line` and `xml_id` columns. It is read-only (`SELECT` only, checked before execution) and the result is capped at 50 rows. The rows' source columns are registered as evidence. It is only worth building if the eval in phase 3 shows questions the fixed tools cannot answer.

## System prompt

`prompts.py` builds the system prompt from two parts.

The rules part tells the model to:

- state only facts returned by a tool, and cite the evidence id right after each one, as `[E3]`;
- never do arithmetic itself, and call `aggregate_leaves` or `get_unit_cost` instead;
- call `search_products` when the user names a part, and ask the user to choose when several products match;
- say plainly when the extract does not contain the answer;
- mention that a value is not PLM data when its origin is `enrichment`, and that costs are material costs only (bought parts cost much more);
- answer in the user's language.

The data overview part is generated from `load()` at startup, not written by hand, so it stays correct if the extract changes. It holds the root, the six real assemblies with their `plm_id`, the object counts, the units, the modelling conventions (`part`, `wrapper`, `assembly`), and the fact that every product is `IN_WORK` with no version history. That is about 600 tokens, and it saves a tool call on most questions.

## Agent loop

```python
@dataclass(frozen=True, slots=True)
class AgentAnswer:
    text: str
    evidence: tuple[Evidence, ...]     # only the ids cited in `text`
    tool_calls: tuple[ToolTrace, ...]  # name, arguments, duration, error: shown in a debug expander
    actions: tuple[ViewAction, ...]    # from open_view
    warnings: tuple[str, ...]          # unknown citations, uncited figures, step limit reached


class PlmAgent:
    def __init__(self, data: PlmData, config: AgentConfig, client: LlmClient | None = None) -> None: ...
    def new_conversation(self) -> Conversation: ...
    def ask(self, conversation: Conversation, question: str) -> AgentAnswer: ...
```

`ask` runs the following loop:

1. Build the messages: the system prompt, then the conversation history, keeping only user questions and final answers. Tool messages from earlier turns are dropped to save tokens, and the evidence ids stay valid through the registry.
2. Call the LLM with the tool schemas. If it returns tool calls, run each one, append the JSON results as `tool` messages, and loop again.
3. Stop when the model answers without tool calls, or after `max_steps`. At the limit, make one last call without tools, asking for an answer from what has been gathered, and add a warning.
4. Validate the citations and return an `AgentAnswer`.

`LlmClient` is a small protocol (`complete(messages, tools) -> LlmMessage`), so the tests inject a scripted fake. The real implementation retries a 429 using the `retry-after` header, at most twice, and has a 30 s timeout.

## Citation check

`citations.py` reads the `[E\d+]` markers in the final answer and checks three things:

- An id the registry does not know is removed from the text and reported as a warning.
- A sentence that contains a figure but no citation gets an "unsourced" warning. This is a heuristic, shown discreetly in the UI.
- The returned `evidence` only holds the cited ids, in order of first appearance, and gives each one a display number (1, 2, 3).

## Interface

`app/assistant.py` exposes `render_assistant(agent: PlmAgent)`, which the UI lead calls from the "AI assistant" tab:

- The agent is cached with `@st.cache_resource`, and the `Conversation` is kept in `st.session_state`.
- The answer is shown as Markdown, with `[E3]` replaced by superscript numbers.
- Below the answer, an evidence panel lists one entry per citation: the label, then for each ref `file, line N, <Product id="ID_41">`, or `file, line N (non-PLM source)` with its `source_note`. A button opens the product in the Explorer when `plm_id` is set.
- A "How I got this" expander lists the tool calls with their arguments. It shows the jury that the figures come from code.
- There are 4 or 5 example questions as buttons, one per business question, for the demo.
- A `PLM_AGENT_REPLAY=1` mode serves answers recorded during rehearsal from `tests/agent_eval/replays/`. It is the fallback if the network or the rate limit fails during the pitch.

## Tests

| File | Covers | Needs |
|---|---|---|
| `test_agent_tools.py` | Each tool on the real extract: `search_products("bearing")`, `get_where_used` on `DifferentialSideShaft` (2 occurrences, 5 impacted assemblies), `get_unit_cost` of the root (266.68), `aggregate_leaves(material, mass_kg)` adding up to 100.1, unknown `plm_id` returning an error, every fact carrying a registered evidence id | `plm` fixture (skipped without the XML) |
| `test_agent_loop.py` | A tool call followed by a final answer, an unknown tool, a failing tool, the step limit, an unknown citation removed, evidence ids stable across two turns | Scripted fake `LlmClient`, no network |
| `agent_eval/run_eval.py` | The golden questions against the real model: expected `plm_id`s cited, figures within 1 %, no warning | API key. Run by hand, not in `pytest` |

The golden questions come from the business questions table in the README, and their expected values are the ones already measured in PARSER.md:

| Question | Expected |
|---|---|
| What are the major subassemblies of the motor? | EMOTOR FRONT, REDUCER FRONT, HOUSING FRONT EDRIVE |
| What is the material cost of the motor? | 266.68 EUR, with the material-cost-only caveat |
| How many D10 L155 screws are in the motor? | 14 |
| Which parts are single-sourced? | 20 of the 36 parts |
| Which supplier do we depend on the most? | SUP-003 GKN Driveline, 8 parts |
| Which three materials weigh the most? | Electrical steel M235-35A 21.62 kg, 20MnCr5 20.50 kg, AlSi9Cu3 19.60 kg |
| If the differential side shaft is replaced, what is impacted? | The 5 assemblies, SUP-004 and SUP-003, lead times 84 and 98 days |
| Tell me about the gear wheel | Clarification asked: 4 different products |
| What is the price of the rotor magnet from supplier X? | Says it is not in the data |

The eval reaches about 15 questions once the risk and change impact tools exist.

## Schedule

| When | Phase | Deliverable | Done when |
|---|---|---|---|
| D1 evening, 1 h | 0. Spike | `config.py`, `llm.py`, a one-tool smoke script, model chosen | A Groq model calls `get_total_quantity` and answers 14 |
| D2 morning, 3 h | 1. Core | `evidence.py`, phase 1 tools, `agent.py`, `prompts.py`, CLI, the two pytest files | `pytest tests/test_agent_tools.py tests/test_agent_loop.py` passes, the CLI answers the first 6 golden questions |
| D2 afternoon, 3 h | 2. Integration | `app/assistant.py`, `get_risk`, `simulate_change`, `open_view` | The chat tab works in the app, the evidence panel opens the Explorer |
| D2 evening / D3 morning, 2 h | 3. Hardening | Golden eval, prompt tuning, recorded replays, optional `run_query` | Eval at least 80 % correct, replay mode tested offline |

### Phase 1 status (4 Nov.)

Done, ahead of schedule: `evidence.py`, `tools.py` (10 tools), `prompts.py`, `citations.py` (moved up from phase 2, since the loop needs it to return evidence), `agent.py` and the CLI `python -m plm.agent`. 117 offline tests pass. All 10 golden questions were run against `openai/gpt-oss-120b`, and the final answers are correct and cited. Fixes made after the live runs:

- The model ranked suppliers by units with `aggregate_leaves` (screw suppliers first) instead of by distinct parts. The tool descriptions now send dependency questions to `get_supplier_dependencies`.
- The single-source list was cut at 10 of 20 and the model presented it as complete. The part lists now default to 30 items, and the prompt requires stating the total when a result is truncated.
- "Tell me about the gear wheel" queried the 4 homonyms one tool at a time, hit the step limit (18,500 tokens), then reported data it had not checked as missing. `compare_products` answers it in 2 calls (8,700 tokens), and the forced final answer must now say "not checked" instead.
- The impact question omitted suppliers. A prompt rule and the `get_where_used` description now ask for suppliers, lead times and cost.

Still open: the model sometimes writes simple arithmetic itself (for example "2 x 1.17 = 2.34"). The values match the tools, and the uncited-figure warning flags these sentences.

**Rate limit.** The free tier caps `openai/gpt-oss-120b` at 8,000 tokens per minute. A question costs 4,000 to 12,000 tokens, because about 2,000 tokens of schemas and prompt are resent at every step. That allows one or two questions per minute. `PLM_LLM_MAX_RETRIES` (default 5) makes the SDK wait out the window instead of failing, so 5 questions took about 4 minutes in a row. For the pitch, the replay mode (phase 3) is no longer optional.

### Phase 2 status (4 Nov.)

The scope was reduced by team decision. `get_risk` and `simulate_change` wait until the Risk and Change Impact services exist, and `open_view` waits for the main app's tabs. What was delivered instead is a standalone test page, `streamlit run app/assistant.py`. It has the chat, citations renumbered `[1]`, `[2]` with a sources panel (file, line, XML element, non-PLM label, `source_note`), the tool calls, the warnings, the token usage, example questions and a reset. It was checked headlessly with `streamlit.testing` on one example question and a follow-up in the same conversation.

Still open: on table answers the model often cites only the root instead of each row (rule 2), and the uncited-figure warning shows it. This goes to the phase 3 prompt tuning.

**Scope guard.** Testing the page showed the model answering off-topic requests from general knowledge (an apple pie recipe). There are now two layers. A scope rule in the system prompt tells the model to decline. A check in `PlmAgent.ask` keeps an answer only if it cites at least one evidence, or if it used tools without stating an uncited figure. Otherwise the answer is replaced by a fixed scope message, in French or English depending on the question, and a warning is added. The check also rejects answers built only from the prompt's data overview, which carry no evidence. Limit: an off-topic answer that cites a real evidence id would pass, and the prompt rule is the only defence against that.

## Risks

| Risk | Mitigation |
|---|---|
| Free-tier rate limit: 8,000 tokens per minute measured on 4 Nov. | Compact tool results, history without tool messages, `compare_products` for multi-product questions, SDK retries that wait out the window, replay mode for the pitch |
| The model invents figures or cites wrong ids | Arithmetic is forbidden in the prompt, unknown ids are removed, figures without a citation are flagged, `temperature = 0` |
| The model picks the wrong product among homonyms | Names are never accepted as keys, and the prompt asks for clarification when the search returns several matches |
| The risk and change impact services are late or change their API | The `Traced` contract is agreed on D1, and phase 1 already answers most questions without them |
| Groq removes the chosen model | Model and endpoint are environment variables, and the eval re-checks a replacement in minutes |
| Partner data leaves the machine | Only tool results are sent, never the full extract. Cloud use was approved by the team, and the key and data are never committed |

## Open points

- Agree with the Risk and Change Impact owners on the exact signatures of their services (phase 2).
- Confirm with the UI lead that the app is in Streamlit, and how tabs are wired, before `app/assistant.py` is written.
- Decide whether the stator meshing parts are excluded from the costs (`excluded_plm_ids`), because the assistant must use the same setting as the dashboard.
