<div align="center">

![Python](https://img.shields.io/badge/Python-3.10+-FFD43B?style=flat&labelColor=306998)
![FastAPI](https://img.shields.io/badge/FastAPI-0.116-white?style=flat&labelColor=009485)
![LangGraph](https://img.shields.io/badge/LangGraph-0.6-white?style=flat&labelColor=333)
![React](https://img.shields.io/badge/React-18-white?style=flat&labelColor=222&color=149eca)
![SQLite](https://img.shields.io/badge/SQLite-aiosqlite-white?style=flat&labelColor=003B57&color=0a6a8a)
![License](https://img.shields.io/badge/License-MIT-2da44e?style=flat&labelColor=555)

# DeepScout — Multi-Agent Research System

**Deep research with verifiable evidence, honest confidence, and a hard cost ceiling.**

</div>

DeepScout decomposes a question, runs parallel investigations across multiple search
providers, verifies every claim against the source it cites, detects
contradictions, re-checks cited URLs, and produces an auditable, cited report —
while tracking exactly what the run cost in tokens, calls, dollars, and time.

It is designed to run on **modest hardware** (8 GB RAM, no GPU) against
**free-tier remote LLM APIs**, and to be more trustworthy than a general chatbot
on the things that matter for research: evidence, verification, contradiction
handling, and calibrated confidence. Measured results are in
[`BENCHMARK_RESULTS.md`](BENCHMARK_RESULTS.md); measured design rationale is in
[`ARCHITECTURE.md`](ARCHITECTURE.md).

> **Design principle:** an ungrounded answer must never be mistakable for a
> researched one. Direct answers are confidence-capped below the research
> sufficiency threshold, degraded runs are capped and labeled, and every claim
> carries a source.

---

## Table of contents

- [Capabilities](#capabilities)
- [Architecture](#architecture)
- [The research pipeline](#the-research-pipeline)
- [Reliability and degradation](#reliability-and-degradation)
- [Research memory and replay](#research-memory-and-replay)
- [Sessions and conversations](#sessions-and-conversations)
- [API](#api)
- [Frontend](#frontend)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Testing and benchmarks](#testing-and-benchmarks)
- [Operational practices](#operational-practices)
- [Project layout](#project-layout)
- [Documentation map](#documentation-map)
- [License](#license)

---

## Capabilities

| Capability | What it does | Where |
|---|---|---|
| **Multi-agent research team** | A LangGraph team (browser → editor/planner → researcher → writer → fact checker → visualizer → publisher) conducts the research and assembles the report. | `backend/engine/multi_agents/`, `app/engine/orchestrator.py` |
| **Parallel section research** | Report sections are researched concurrently, each running a research → review → revise loop; siblings are named so writers do not overlap. | `backend/engine/multi_agents/agents/` |
| **Multi-provider search** | Self-hosted SearXNG metasearch plus Wikipedia, arXiv and Crossref — no search API key — with canonical-URL dedup, domain diversity, circuit breakers, and a bounded disk cache. | `app/agents/search.py` |
| **Provider control** | The Model Control Center selects a single OpenAI-compatible provider or an ordered fallback chain; engine generation is routed through it, with breakers, response cache and usage ledger. | `app/core/providers.py`, `app/engine/llm_bridge.py` |
| **Contradiction detection** | Numeric (unit-aware divergence), polarity (affirms vs. negates), and temporal (same measure, different periods) detectors with severity ranking. | `app/core/contradictions.py` |
| **Confidence engine** | Multi-signal score (citation support, axis coverage, source quality, contradiction penalty, …). Degraded runs are capped below the sufficiency threshold with an explanatory breakdown. | `app/core/confidence.py` |
| **Evidence grading** | Claim→source→verification→independence→corroboration→contradiction records; per-claim investigation memory tracks corroboration attempts. | `app/core/evidence_grade.py`, `app/core/investigation_state.py` |
| **Replay, trace & export** | Every run persists its events, sources, final report and verbatim NDJSON frames; replay reconstructs the timeline and reports export to MD/DOCX/PDF. | `app/api/routes.py`, `app/db/sqlite.py` |
| **Intelligent stopping** | Marginal-gain analysis, a no-re-novel-query memory, mode-aware targets, and hard walls (iterations, expansions, money, tokens, time). | `app/core/depth_controller.py` |
| **Cost-aware reasoning** | Every LLM call records tokens/USD from provider usage fields; the budget governor refuses passes that cannot be paid for; the ledger streams live to the UI. | `app/core/usage.py`, `app/agents/budget.py` |
| **LLM response cache** | Exact-prompt disk cache — repeated critic re-evals and re-runs are served from disk with zero provider spend. Bounded size and TTL. | `app/core/llm_cache.py` |
| **Semantic engine** | CPU-light TF-IDF hybrid (stemming, synonym canonicalization, negation weighting) powering dedup, contradiction banding, and citation support. | `app/core/semantic.py` |

---

## Architecture

```
Query
  → Multi-agent engine team (LangGraph StateGraph, one graph per request)
      Browser      initial research on the query
      Editor       plan the section outline
      Human        plan review gate (auto-accepted in the API)
      Researcher   parallel per-section research: research → review → revise
      Writer       introduction, table of contents, conclusion, sources
      Fact checker review the draft; send back to the writer or accept
      Visualizer   optional diagram
      Publisher    assemble the report
  → final_report frame + persisted report
```

The engine team is vendored under `backend/engine/` and driven by
`app/engine/orchestrator.py`. Its two external seams are bridged to the rest of
the product: generation goes through `app.core.llm.LLMClient` (so the **Model
Control Center** decides what runs), and retrieval goes through
`app.agents.search.SearchClient`. Persistent state lives in SQLite
(`aiosqlite`); streaming is NDJSON over a single HTTP response.

A full component map, data model, and the reasoning behind each design
decision are in [`ARCHITECTURE.md`](ARCHITECTURE.md). The product vision and
feature rationale are in [`DeepScout-vision-v2.md`](DeepScout-vision-v2.md).

---

## The research pipeline

1. **Scope.** The engine team's browser stage performs initial research on the
   query.
2. **Plan.** The editor plans an outline of report sections (max sections
   scales with the quick/standard/deep mode). In the API context the plan is
   auto-accepted at the human-review gate.
3. **Research.** Sections are researched in parallel; each section runs a
   research → review → revise loop, and every section knows its siblings so
   writers do not overlap. Retrieval fans out through `SearchClient` (SearXNG
   with Wikipedia / arXiv / Crossref fallbacks), with the same dedup, domain
   diversification, caching and circuit breakers as the rest of the product.
4. **Write.** The writer composes the introduction, table of contents,
   conclusion and source list from the researched sections.
5. **Fact-check.** The fact checker reviews the assembled draft and either
   accepts it or returns notes, sending the draft back to the writer.
6. **Assemble.** The publisher assembles the final report (optionally a
   diagram from the visualizer).
7. **Persist.** The route writes the run, sources, events, final report and the
   verbatim NDJSON frames, joinable by `run_id` for replay and export.

---

## Reliability and degradation

DeepScout assumes remote, rate-limited, occasionally-unavailable providers.

- **Fail fast, not slow.** Auth errors, payment errors, and timeouts are never
  retried within the provider chain; one timeout trips the circuit breaker
  immediately. `Retry-After` is honored but capped.
- **Per-provider fallback chain.** Custom OpenAI-compatible provider → Groq →
  HuggingFace, each with its own timeout profile.
- **Deterministic fallbacks.** Every LLM-calling agent has a rule-based default,
  so an outage degrades output instead of failing the run.
- **Degradation is disclosed, never hidden.** Fallbacks are recorded and
  streamed; the confidence engine caps degraded runs. A degraded report is
  visibly marked in the UI.
- **Preflight provider probe.** A run with zero reachable providers fails in
  seconds with per-provider reasons instead of producing minutes of garbage.
- **Budget walls.** Iterations, expansion passes, searches, LLM calls, tokens,
  dollars, and wall-clock time are all bounded independently.
- **Memory discipline.** Fetched content is released after verification; caches
  and per-run memories are size-bounded and LRU-evicted (8 GB target host).

The full bug-class history and prevention rules that these mechanisms come from
are documented in [`AGENTS.md`](AGENTS.md).

---

## Research memory and replay

Each run persists an auditable trace in SQLite, joinable by `run_id`:

`research_runs`, `agent_tasks`, `sources`, `claims`, `agent_events`, `evidence`,
`critic_reviews`, `decisions`, `final_reports`, `contradictions`,
`verification_results`, `citations`.

- **Trace** — `GET /api/research/{run_id}/trace` reconstructs the whole run:
  plan, sources, claims with verification flags, node events, critic reviews,
  decisions, contradictions, citations, and the final report.
- **Resume** — `POST /api/research/{run_id}/resume` re-enters the graph at the
  critic node with state rebuilt from durable rows, so failed/timeout runs
  continue without re-running retrieval. A user-cancelled run is recorded as
  `cancelled` and is deliberately **not** resumable.
- **Replay in the UI** — selecting a chat restores its full persisted history
  and per-run pipeline steps; no re-fetch of the research is required.

---

## Sessions and conversations

A chat is a first-class **session**, not a sequence of unrelated runs.

- **Stable session id.** One id per chat, generated on first use and persisted.
  Every follow-up reuses it; only **New Chat** mints a new one.
- **Append-only history.** Each question appends a user turn and its run result
  to the same session. Opening a chat restores the complete ordered thread.
- **Stable title.** A chat is named by its first message; follow-ups never
  rename it.
- **Interrupt and edit.** While a response streams it can be stopped cleanly
  (frontend abort + backend `cancelled` status, no partial report persisted).
  The user can then edit an earlier message and resend; the thread truncates
  from that turn and the new request runs in the **same** session with no
  leftover state from the interrupted one.
- **Sidebar** shows one entry per chat (not per run), with status and activity.

Endpoint: `GET /api/sessions` (list) and `GET /api/sessions/{id}` (full ordered
history).

---

## API

Base path `/api`. The research stream is rate-limited (`RATE_LIMIT`, default
`5/minute`).

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness probe |
| `POST` | `/research/stream` | Run research; streams NDJSON events |
| `POST` | `/research/{run_id}/resume` | Resume a failed/timeout run from checkpoint |
| `GET` | `/research/{run_id}/trace` | Full persisted run reconstruction |
| `GET` | `/sessions` | List chat sessions (one row per chat) |
| `GET` | `/sessions/{session_id}` | Full ordered chat history |
| `GET` | `/providers` | List configured LLM providers (keys masked) |
| `POST` | `/providers` | Add a provider |
| `PUT` | `/providers/{id}` | Update a provider |
| `DELETE` | `/providers/{id}` | Delete a provider |
| `POST` | `/providers/{id}/active` | Activate a provider |
| `POST` | `/providers/active/clear` | Clear the active provider |
| `POST` | `/providers/{id}/test` | Probe a provider's reachability |

**Stream event types:** `progress`, `intent`, `route`, `direct_answer`, `plan`,
`search_progress`, `critic`, `findings`, `decisions`, `final_report`, `error`.
Budget telemetry rides on `critic` and `final_report`.

Example:

```bash
curl -N -X POST http://127.0.0.1:8000/api/research/stream \
  -H "Content-Type: application/json" \
  -d '{"query": "What is retrieval augmented generation?", "mode": "standard"}'
```

---

## Frontend

React 18 + Vite 5 single-page console (`frontend/src/App.jsx`) that consumes the
NDJSON stream and renders the thread, per-run pipeline steps, the intelligence
panel (plan, budget, confidence breakdown, citation health), the
claim drawer, and the research library (Evidence, Knowledge). It includes
session management (stable ids, restore-on-reload, interrupt/edit), the
provider management tab, and a docs view. Development proxies `/api` to
`http://127.0.0.1:8000`.

---

## Quick start

**Requirements:** Python 3.10+, Node 18+, 8 GB RAM, internet access. No GPU, no
local models, no paid services required. **No API keys in `.env`** — the LLM
provider is added in the app.

### Backend

```bash
# from the repository root
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt

cd backend
../.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

`backend/.env` is optional — the backend starts without it. Add your LLM
provider in the app's **Model Controls** tab (`/#/model-controls`; the legacy
`/#/providers` link still resolves there): any OpenAI-compatible host, stored
encrypted at rest, effective immediately with no restart. A selected provider
is exclusive, so no other key is spent.

<details>
<summary>Prefer environment variables? (headless / CI)</summary>

All optional; `.env.example` ships them commented out.

```bash
cp backend/.env.example backend/.env   # then uncomment what you need
```

```env
GROQ_API_KEY=...                  # model: openai/gpt-oss-20b
HUGGINGFACE_API_KEY=...
# or any OpenAI-compatible host (all three required together):
# CUSTOM_LLM_API_KEY=...
# CUSTOM_LLM_BASE_URL=https://...
# CUSTOM_LLM_MODEL=...
```

A UI-selected provider always wins over these.

</details>

### Search

Search needs no search API key: Wikipedia, arXiv and Crossref are queried
directly. General web search comes from a local
[SearXNG](https://github.com/searxng/searxng) instance on
`http://localhost:8080` — see [SETUP.md](SETUP.md#set-up-search-searxng) for the
one-time install. Runs still work without it.

### Frontend

```bash
cd frontend
npm install
npm run dev                        # http://127.0.0.1:5173
```

### Smoke test

```bash
curl -s http://127.0.0.1:8000/api/health
curl -N -X POST http://127.0.0.1:8000/api/research/stream \
  -H "Content-Type: application/json" \
  -d '{"query": "What is retrieval augmented generation?"}'
```

Full setup, including platform notes and troubleshooting, is in
[`SETUP.md`](SETUP.md).

---

## Configuration

All configuration goes through `app/core/config.py` (`pydantic-settings`) and is
documented in `backend/.env.example` and [`CONFIG.md`](CONFIG.md). Highlights:

| Setting | Default | Purpose |
|---|---|---|
| `RESEARCH_TIMEOUT_SEC` | `1000` | Wall-clock ceiling per run |
| `MAX_ITERATIONS` | `3` | Expansion iterations ceiling |
| `MAX_PARALLEL_SEARCH` | `2` | Concurrent search workers |
| `MAX_PARALLEL_AGENTS` | `3` | Concurrent agents (hardware cap) |
| `MAX_PARALLEL_LLM` | `2` | Concurrent LLM calls (quota protection) |
| `MAX_BUDGET_USD` | `0.50` | Per-run cost ceiling |
| `MAX_BUDGET_TOKENS` | `400000` | Per-run token ceiling |
| `SUFFICIENCY_THRESHOLD` | `0.75` | Confidence required to stop expanding |
| `RATE_LIMIT` | `5/minute` | Research stream rate limit |
| `DATABASE_URL` | `./research.db` | SQLite path |
| `ROUTER_ENABLED` | `true` | Direct-answer vs. research routing |
| `QUALITY_GATE_ENABLED` | `true` | Pre-delivery answer scoring |
| `CITATION_CHECK_ENABLED` | `true` | Live cited-URL re-validation |

`.env` overrides `.env.example`; real environment variables override both.

---

## Testing and benchmarks

DeepScout is test-heavy by design; several reliability mechanisms exist because a
specific bug shipped once (see [`AGENTS.md`](AGENTS.md) §2).

```bash
# Backend (pytest)
cd backend && pytest tests/ -v

# Lint (ruff)
cd backend && ruff check app tests bench scripts

# Type check (informational baseline)
cd backend && mypy app

# Frontend unit tests (Node's built-in runner, no extra deps)
cd frontend && npm test

# Frontend production build
cd frontend && npm run build
```

- **Backend test suite** covers retrieval, evidence grading, contradictions,
  confidence, budget, degradation, sessions, trace/replay, providers, resume,
  and API contracts.
- **Deterministic offline suite** (`bench/run_offline.py`) runs without live
  providers; labeled fixtures live in `bench/datasets.py`.
- **Frontend regression tests** cover session persistence, interrupt/edit, and
  auto-scroll helpers.
- Measured results: [`BENCHMARK_RESULTS.md`](BENCHMARK_RESULTS.md) and
  `benchmark_results.json`.
- Per-change changelog and bug-fix history: [`CHANGELOG.md`](CHANGELOG.md).

---

## Operational practices

- **One task = one reviewable commit**, with a scope tag (e.g. `[v3-search]`).
- **Run the verification command before calling a task done** — a task is done
  when it's checked, not when the code is written.
- **Never commit secrets or databases.** `git status` is checked before every
  commit; `.env`, `*.db`, caches, and build artifacts are gitignored.
- **Additive, idempotent schema changes only.** `CREATE TABLE IF NOT EXISTS` and
  guarded `ALTER`; existing readers never break.
- **Every new LLM agent ships a deterministic fallback** and a matching frontend
  case for any new stream event.
- **Every new setting goes through `Settings`** and gets an `.env.example`
  placeholder — no ad-hoc `os.getenv()`.
- **Bounded caches and concurrency by default**, sized for the 8 GB target host.
- **Provenance over vibes:** live runs are verified against a fresh backend log
  and process check before results are trusted.

The complete rulebook, including the bug classes each rule prevents, is
[`AGENTS.md`](AGENTS.md).

---

## Project layout

```text
backend/
  main.py                 FastAPI app + lifespan init (installs engine bridges)
  engine/                 vendored multi-agent engine (gptr/ core + multi_agents/)
  app/engine/             engine adapters: orchestrator, llm/retriever bridges,
                          event adapter
  app/agents/             retained search + evidence/source helpers
  app/graph/              shared helpers (evidence acquisition, research state)
  app/api/routes.py       stream, resume, trace, export, sessions
  app/api/providers_routes.py  providers + fallback chains (Model Control Center)
  app/core/               config, llm client + cache, usage ledger, semantic
                          engine, confidence, contradictions, evidence grading,
                          isolation, degradation, providers
  app/db/sqlite.py        auto-initializing schema + persistence
  bench/                  deterministic component suite + labeled datasets
  tests/                  pytest suite
frontend/
  src/App.jsx             NDJSON stream consumer + session/thread state
  src/components/         thread, answer card, intelligence panel, model controls
  test/                   Node-runner regression tests (session, edit, scroll)
```

---

## Documentation map

| Document | Contents |
|---|---|
| [`SETUP.md`](SETUP.md) | Install and run on a fresh machine |
| [`CONFIG.md`](CONFIG.md) | Every setting, with rationale |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Components, data flow, design decisions |
| [`DeepScout-vision-v2.md`](DeepScout-vision-v2.md) | Product vision and feature rationale |
| [`BENCHMARK_RESULTS.md`](BENCHMARK_RESULTS.md) | Measured benchmark results |
| [`CHANGELOG.md`](CHANGELOG.md) | Per-phase changes and bug-fix history |
| [`AGENTS.md`](AGENTS.md) | Operating rules for contributors (incl. AI agents) |

---

## License

MIT — see [`LICENSE`](LICENSE).
