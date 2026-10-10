# Setup Guide

Everything needed to run DeepScout on a fresh machine. Target profile: any
Linux/macOS/Windows host with **Python 3.10+**, **Node 18+**, 8 GB RAM and
internet access. No GPU, no local models, no paid services required.

**No API keys go in `.env`.** The backend starts with the config file
untouched; you add your LLM provider in the app's Model Controls tab, and the
only other setup step is a local SearXNG instance for web search.

---

## 1. Backend

```bash
# from the repository root
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
```

Dependencies (all pure-Python or wheels — nothing compiles):
FastAPI, LangGraph, httpx, tenacity, diskcache, aiosqlite, cryptography,
structlog, slowapi, pydantic-settings, numpy.
`pymupdf` is optional and only enables PDF extraction from arXiv results.

### Configure the LLM (in the app, not here)

```bash
cp backend/.env.example backend/.env      # optional — the backend runs without it
```

There is nothing to fill in. Start the backend and the UI (steps 1–2 below),
then in the app go to **Model Controls** (`/#/model-controls`; the legacy
`/#/providers` link still resolves there) and:

1. Add a provider — name, base URL, API key, model id. Any
   OpenAI-compatible `/chat/completions` host works: OpenAI, Groq, Together,
   OpenRouter, or a local vLLM / LM Studio / Ollama endpoint. Preset base URLs
   are provided for the common ones.
2. Select it as active.

Keys are encrypted at rest in the SQLite database, a selected provider becomes
**exclusive** (no other provider's key is spent), and it takes effect on the
next call — **no backend restart**.

Until you add one, the backend still runs and the UI still loads; a research
run returns a clear "no provider configured" error rather than a degraded
answer.

<details>
<summary>Prefer environment variables? (headless / CI)</summary>

All of these are optional and `.env.example` ships them commented out.

```env
# Option A — Groq free tier (fast, generous free quota)
GROQ_API_KEY=gsk_...
GROQ_MODEL=openai/gpt-oss-20b

# Option B — HuggingFace Inference
HUGGINGFACE_API_KEY=hf_...
HUGGINGFACE_MODEL=Qwen/Qwen2.5-7B-Instruct

# Option C — ANY OpenAI-compatible provider
CUSTOM_LLM_API_KEY=...
CUSTOM_LLM_BASE_URL=https://openrouter.ai/api/v1
CUSTOM_LLM_MODEL=meta-llama/llama-3.1-8b-instruct:free
```

All three `CUSTOM_LLM_*` values must be set together; when present, the custom
provider leads the chain and Groq/HF remain as fallbacks. Values starting with
`your_` are treated as unset, so the `.env.example` placeholders are inert. A
UI-selected provider always wins over these.

</details>

### Set up search (SearXNG)

Search needs no search API key. Wikipedia, arXiv and Crossref are queried
directly and work with nothing installed; SearXNG adds general web search and
is what the system queries by default.

```bash
git clone https://github.com/searxng/searxng.git
cd searxng
python3 -m venv .venv && .venv/bin/pip install -U -r requirements.txt

# give it a secret key, then run it
cp settings.yml settings.local.yml
#   edit settings.local.yml -> server: secret_key: "<any long random string>"
SEARXNG_SETTINGS_PATH=settings.local.yml .venv/bin/python -m searx.webapp
```

It listens on `127.0.0.1:8080`, which is where the backend already looks
(`SEARXNG_URL`). No container runtime is needed. Leave it running in its own
terminal. If it is down, runs still work — they fall back to Wikipedia, arXiv
and Crossref.

### Start the server

```bash
cd backend
../.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Verify:

```bash
curl http://127.0.0.1:8000/api/health
# {"status":"ok"}
```

Your first research run, without the UI:

```bash
curl -N -X POST http://127.0.0.1:8000/api/research/stream \
  -H "Content-Type: application/json" \
  -d '{"query": "What is the evidence that retrieval augmented generation reduces hallucinations?", "mode": "standard"}'
```

Each line of the response is a JSON event (`plan`, `search_progress`,
`findings`, `critic`, `final_report`, ...). The full event schema is in
`ARCHITECTURE.md`.

---

## 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open **http://127.0.0.1:5173**. The Vite dev server proxies `/api` to the
backend on port 8000.

For a production build:

```bash
npm run build          # outputs frontend/dist
npx vite preview       # serve the built bundle
```

---

## 3. Modes

Select the research mode in the composer:

| Mode | Agents | Max passes | Confidence target | Use for |
|---|---|---|---|---|
| Mode | Agents | Max passes | Confidence target | Use for |
|---|---|---|---|---|
| quick | 2 | 1 | 0.60 | fast lookups |
| standard | 3 | 3 | 0.75 | default research |
| deep | 5 | 5 | 0.80 | multi-angle investigations |

`standard` is the default. The retired `executive` and `audit` modes are still
accepted: an old run, saved URL, or older client sending one of them is mapped to
the mode that inherited its behaviour (`executive` -> `deep`, `audit` ->
`standard`) rather than rejected.

---

## 4. Benchmarks

Offline (deterministic, no keys needed):

```bash
cd backend
../.venv/bin/python bench/run_offline.py            # full suite
../.venv/bin/python bench/run_offline.py --quick    # skip the e2e pipeline
```

Writes `bench/results/benchmark_results.json` and
`bench/results/BENCHMARK_RESULTS.md`.

For a live end-to-end check, run the backend and stream a query (see the smoke
test below); the offline suite covers the deterministic components only.

---

## 5. Tests

```bash
cd backend
../.venv/bin/python -m pytest tests/ -q   # ~1460 tests, all offline
```

Every test runs without network or API keys: HTTP is mocked with respx, the
LLM cache and citation URL checks are force-disabled by `conftest.py`, the
provider keys are pinned to dummies (so the suite never depends on your real
`.env` and never spends your quota), and the provider store is isolated from
your real database.

---

## 6. Troubleshooting

**"No LLM provider configured" / a run returns that error**
No provider exists yet — expected on a fresh install. Open Model Controls
(`/#/model-controls`), add a provider, and select it. No restart needed. (If
you prefer env config, set the `CUSTOM_LLM_*` trio or `GROQ_API_KEY` in
`backend/.env`; values starting with `your_` count as unset.)

**A run ends with "No LLM provider is reachable right now"**
Providers exist but all failed their pre-flight probe — the error lists
per-provider reasons. Usual causes: exhausted free-tier daily quota
(resets daily), wrong key (401), or a slow custom endpoint
(raise `CUSTOM_LLM_TIMEOUT_SEC`).

**Search returns nothing / only Wikipedia**
SearXNG is not running or is unreachable. Check it directly:

```bash
curl -s "http://localhost:8080/search?q=test&format=json" | head -c 200
```

A `500` or an empty body means the service is down or misconfigured — restart
it and confirm `server.secret_key` is set in its settings. Until then, runs
still work using Wikipedia, arXiv and Crossref alone.

**Runs feel slow**
- The first run of a query pays full search + LLM latency; re-runs hit the
  disk caches (search: 1h TTL; LLM prompts: 6h TTL).
- `MAX_PARALLEL_LLM=2` is deliberate — raising it on free tiers causes
  429s that cost more time than they save.
- `deep` mode runs up to 5 passes; use `standard` for interactive work.

**Some SearXNG engines report "too many requests"**
SearXNG aggregates many upstreams and some rate-limit unauthenticated traffic.
That is normal and harmless — the circuit breaker waits
`SEARCH_SEARXNG_COOLDOWN_SEC` and the healthy engines still answer. Engines
can be pinned in SearXNG's own `settings.yml` if you want fewer.

**Groq 404 on model (only if you configured Groq via env)**
Models get decommissioned. Set `GROQ_MODEL` to a current model from
`console.groq.com/docs/models`. `openai/gpt-oss-20b` is the verified
default at the time of writing.

**Database locked / fresh-install errors (v2 fix)**
The persistence layer now auto-creates the schema on first use and accepts
`file:` / `sqlite://` DATABASE_URL forms. If you still see lock errors,
ensure only one backend process uses the same `research.db`.
