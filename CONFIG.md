# Configuration Reference

Every environment variable, its default, and what it actually controls.
All variables live in `backend/.env` (see `.env.example`); real environment
variables override `.env` values, which override `.env.example`.

**Nothing in this file is required.** The backend starts and serves the UI
with an untouched `.env`. The LLM key is added from the app's Model Controls
tab (`/#/model-controls`, legacy `/#/providers`) and stored encrypted in the
database; the variables below are optional overrides for headless use.

Every default in this table is the value in `app/core/config.py`, which is the
single source of truth. If you change a default there, change it here.

## LLM providers (all optional)

Configure the LLM in the UI — that is the supported path. These variables
exist for headless/CI setups or to seed a provider before the UI is reachable.

| Variable | Default | Description |
|---|---|---|
| `GROQ_API_KEY` | `""` | Optional Groq key. Values starting with `your_` count as unset. |
| `GROQ_MODEL` | `openai/gpt-oss-20b` | Groq model id. `llama-3.1-8b-instant` was decommissioned upstream; check console.groq.com if this 404s. |
| `HUGGINGFACE_API_KEY` | `""` | Optional Hugging Face inference key (secondary fallback). |
| `HUGGINGFACE_MODEL` | `Qwen/Qwen2.5-7B-Instruct` | Hugging Face model id. |
| `CUSTOM_LLM_API_KEY` | `""` | Custom OpenAI-compatible provider (any host serving `/chat/completions`: OpenRouter, Together, Ollama+ngrok, vLLM, LM Studio). All three of the `CUSTOM_LLM_*` values must be set. |
| `CUSTOM_LLM_BASE_URL` | `""` | Base URL for the custom provider. |
| `CUSTOM_LLM_MODEL` | `""` | Model id for the custom provider. |
| `ACTIVE_PROVIDER_FALLBACK` | `false` | Whether a *failing* UI-selected active provider falls through to the env chain instead of degrading the run. `false` (strict) never spends another provider's key without your say-so. |
| `PROVIDER_CHAINS_ENABLED` | `true` | Kill switch for provider fallback chains. An **enabled chain replaces the single-provider selection entirely** — the two modes are mutually exclusive by construction (selecting a provider disables any chain; enabling a chain clears the active provider). `false` disables chain resolution without deleting stored chain data. |
| `DEEPSCOUT_SECRET_KEY` | `""` | Encrypts provider keys at rest. Unset falls back to a `backend/.deepscout_secret` file created once with `0600` permissions. |

## Retrieval

| Variable | Default | Description |
|---|---|---|
| `MAX_PARALLEL_SEARCH` | `3` | Concurrent searches. |
| `SEARCH_FETCH_TOP_N` | `10` | Top-ranked results per sub-question that get their content fetched. |
| `MAX_PARALLEL_FETCH` | `4` | Concurrent page fetches, bounded separately from query fan-out. |
| `SEARCH_RETRY_ATTEMPTS` | `3` | Transient-error retries per provider search (search only; LLM timeouts fail fast). |
| `MAX_QUERIES_PER_CONTRACT` | `3` | Query variants (incl. primary-source fan-out) per search contract. |
| `SEARCH_MAX_RESULTS` | `15` | Results kept per provider search before dedup/rank. |
| `SEARCH_TIMEOUT_SEC` | `20.0` | Per-search timeout. |
| `SEARCH_DOMAIN_COOLDOWN_SEC` | `90.0` | How long a registrable domain is skipped after a hard block or repeated failures. |
| `SEARCH_DOMAIN_FAILURE_THRESHOLD` | `3` | Transient failures before a domain is cooled down. |
| `SEARCH_DOMAIN_REGISTRY_MAX` | `512` | LRU cap on the run-scoped domain registry. |
| `SEARCH_FAILED_URL_MEMORY_MAX` | `2048` | Cap on remembered failed URLs per run. |
| `SEARCH_FETCH_RETRY_ATTEMPTS` | `2` | Retries for a single page fetch. |
| `SEARCH_PRIMARY_FALLBACK_ENABLED` | `true` | Try a substitution query when a primary-source search returns nothing usable. |
| `SEARCH_PRIMARY_FALLBACK_MAX` | `2` | Substitution queries per failed primary-source search. |

## SearXNG

| Variable | Default | Description |
|---|---|---|
| `SEARXNG_URL` | `http://localhost:8080` | Where SearXNG listens. |
| `SEARXNG_ENABLED` | `true` | Set `false` to skip SearXNG entirely (Wikipedia/arXiv/Crossref only). |
| `SEARXNG_AUTOSTART` | `true` | **The backend spawns and stops SearXNG itself.** With autostart on, one command runs the whole system; the child is stopped on shutdown. Set `false` to manage SearXNG in your own terminal. |
| `SEARXNG_STARTUP_TIMEOUT_SEC` | `45.0` | How long startup waits for `/healthz` before degrading. Generous because a cold first boot imports a large dependency set. |
| `SEARXNG_HOME` | `""` | Where the SearXNG clone lives. Empty = `<repo_root>/searxng`. |
| `SEARXNG_PYTHON` | `""` | Interpreter used to run it. **Must be SearXNG's own virtualenv** (flask, lxml, curl_cffi are not backend dependencies). Empty = `<searxng_home>/.venv/bin/python`. |
| `SEARXNG_TIMEOUT_SEC` | `30.0` | Per-request timeout to SearXNG. |
| `SEARXNG_MAX_RESULTS` | `30` | Results requested from SearXNG. |
| `SEARXNG_CATEGORIES` | `general,science` | Comma-separated SearXNG categories. |
| `SEARXNG_LANGUAGE` | `en` | SearXNG language preference. |
| `SEARXNG_SAFESEARCH` | `0` | SearXNG safesearch level. |
| `SEARCH_SEARXNG_COOLDOWN_SEC` | `60.0` | Cooldown after a SearXNG failure before falling back. |

## LLM calls

| Variable | Default | Description |
|---|---|---|
| `MAX_PARALLEL_LLM` | `4` | Concurrent LLM calls. The old default of `2` was sized for a free tier whose TPM/TPD quotas a few parallel large prompts exhaust; a paid or large-context endpoint stalls at a low value, so raise it there. |
| `MAX_LLM_CALLS` | `60` | Per-run LLM-call ceiling. |
| `MAX_RESEARCH_SECONDS` | `300.0` | Per-run wall-clock ceiling used by the research budget. |
| `MAX_ITERATIONS` | `3` | Upper bound on planned research iterations. |
| `MAX_PARALLEL_AGENTS` | `3` | **Wave size for section research**: how many report sections are researched at once. Each wave's writers also receive the sections already written, so a smaller wave means tighter de-duplication at the cost of latency. |
| `LLM_TIMEOUT_SEC` | `60.0` | Per-call timeout for Groq/Hugging Face. |
| `CUSTOM_LLM_TIMEOUT_SEC` | `60.0` | Same, for the custom OpenAI-compatible provider, which is typically slower. Applies to the custom provider only. |
| `LLM_TEMPERATURE` | `0.3` | Sampling temperature. A provider row may override it. |
| `LLM_BREAKER_THRESHOLD` | `3` | Consecutive failures that open the circuit breaker. |
| `LLM_BREAKER_COOLDOWN_SEC` | `15.0` | How long the breaker stays open. Kept short because one upstream 503 otherwise degrades the whole chain for a full minute. |
| `LLM_BREAKER_WAIT_CAP_SEC` | `25.0` | Ceiling on how long a call waits for the breaker to clear. |
| `LLM_CACHE_ENABLED` | `true` | Cache identical prompts. |
| `LLM_CACHE_TTL_SEC` | `21600` | 6 hours — sources age out. |

## Run limits

| Variable | Default | Description |
|---|---|---|
| `RESEARCH_TIMEOUT_SEC` | `1000.0` | Hard ceiling on one research request. |
| `RATE_LIMIT` | `5/minute` | Per-client request rate limit. |
| `DATABASE_URL` | `./research.db` | SQLite path. `file:` / `sqlite://` forms are accepted and the schema is created automatically. |

## Research pipeline (`app/research/`)

These shape how a run is composed. Model and provider selection are **not**
here — the Model Control Center owns those.

| Variable | Default | Description |
|---|---|---|
| `RESEARCH_TOTAL_WORDS` | `1200` | Target report length in words. |
| `RESEARCH_REPORT_FORMAT` | `APA` | Citation format named in the prompts. |
| `RESEARCH_LANGUAGE` | `english` | Output language. |
| `RESEARCH_REPORT_SOURCE` | `web` | Evidence source: `web`, `local`, or `hybrid`. |
| `RESEARCH_FAST_TOKEN_LIMIT` | `6000` | Output-token cap for low-stakes calls. |
| `RESEARCH_SMART_TOKEN_LIMIT` | `12000` | Output-token cap for report writing. |
| `RESEARCH_STRATEGIC_TOKEN_LIMIT` | `8000` | Output-token cap for query planning. |
| `RESEARCH_MAX_RESULTS_PER_QUERY` | `5` | Results per search query. |
| `RESEARCH_CONTEXT_FILTER` | `auto` | `auto` \| `keyword` \| `none`. `keyword` is the BM25 filter — local, no API, no model. |
| `RESEARCH_COMPRESSION_THRESHOLD` | `8000` | Content below this many characters is passed through without filtering. |
| `RESEARCH_KEYWORD_RELATIVE_THRESHOLD` | `0.5` | Chunks below this fraction of the best BM25 score are dropped. |
| `RESEARCH_KEYWORD_MAX_RESULTS` | `25` | Cap on BM25-selected chunks. |
| `RESEARCH_MAX_CONTENT_CHARS` | `50000` | Per-document character cap when building context. |
| `RESEARCH_USER_AGENT` | browser UA | User-Agent for pages the retrieval layer did not pre-fetch. Some hosts reject a non-browser UA outright. |
| `RESEARCH_MAX_SCRAPER_WORKERS` | `15` | Concurrent page fetches. |
| `RESEARCH_SCRAPER_RATE_LIMIT_DELAY` | `0.0` | Minimum seconds between fetches, enforced **globally across all sections** (0 = no limit). |
| `RESEARCH_DOC_PATH` | `./my-docs` | Directory walked when `RESEARCH_REPORT_SOURCE` is `local` or `hybrid`. |
| `RESEARCH_CURATE_SOURCES` | `false` | Run the model-based source-curation pass before writing. |
| `ALLOW_PRIVATE_URLS` | `false` | **Disables the SSRF / local-file guard** so internal or self-hosted targets can be fetched. Off by default; only enable deliberately. |

## Caches

| Variable | Default | Description |
|---|---|---|
| `CACHE_SIZE_LIMIT_BYTES` | `250000000` | 250MB — diskcache size cap. |
| `CACHE_TTL_SEC` | `3600` | 1 hour (search results). |