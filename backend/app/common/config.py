from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Last file wins: .env (real values) must override .env.example
        # (placeholders). Real environment variables beat both.
        env_file=(".env.example", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # --- LLM providers -------------------------------------------------
    # NONE of the keys below are required. The primary way to configure the
    # LLM is the Providers tab (UI: `/#/model-controls`, legacy `/#/providers`):
    # keys are stored Fernet-encrypted in SQLite and an explicitly selected
    # provider is EXCLUSIVE — it wins over everything configured here.
    #
    # The env keys are an optional convenience for headless/CI use or for
    # seeding the very first provider before the UI is reachable. Values
    # starting with `your_` are treated as unset, so the `.env.example`
    # placeholders are inert and can be left alone.
    groq_api_key: str = ""
    # llama-3.1-8b-instant was decommissioned on Groq (2026); gpt-oss-20b is
    # the verified replacement. Check console.groq.com if this 404s again.
    groq_model: str = "openai/gpt-oss-20b"
    huggingface_api_key: str = ""
    huggingface_model: str = "Qwen/Qwen2.5-7B-Instruct"
    # Custom OpenAI-compatible provider (any host serving /chat/completions:
    # OpenRouter, Together, Ollama+ngrok, vLLM, LM Studio, ...). All three
    # must be set; when present it leads the chain, Groq/HF stay as fallback.
    custom_llm_api_key: str = ""
    custom_llm_base_url: str = ""
    custom_llm_model: str = ""
    # At-rest encryption for user-added provider keys (Providers tab).
    # Any string works (hashed into key shape); unset falls back to a
    # backend/.deepscout_secret file created once with 0600 permissions.
    # Whether a FAILING UI-selected active provider falls through to the
    # env chain above instead of degrading the run. Strict exclusivity
    # (False) never spends another provider's key without your say-so.
    active_provider_fallback: bool = False
    # Provider fallback chains (Providers tab): when True, an ENABLED user
    # chain (ordered list of saved providers) replaces the single-provider
    # selection entirely for LLM calls. False disables chain resolution
    # regardless of what is stored — an operator kill switch that leaves
    # chain data intact. Default True so the feature works out of the box;
    # with no chain enabled, resolution changes nothing.
    provider_chains_enabled: bool = True

    deepscout_secret_key: str = ""

    # Persistence
    database_url: str = "./research.db"

    # Pipeline limits
    max_parallel_search: int = 3
    max_parallel_agents: int = 3  # hardware cap (8GB host) — raise only after load-testing
    # Concurrent LLM calls across the whole pipeline (planner + N summarizer
    # workers + critic + synthesizer). The old default of 2 was sized for a
    # free tier whose TPM/TPD quotas a few parallel large prompts would exhaust.
    # A paid/large-context endpoint has no such ceiling and stalls badly at 2
    # (the semaphore, not the provider, becomes the bottleneck). Lower it again
    # only for a strict free tier.
    max_parallel_llm: int = 4
    max_iterations: int = 3
    # Retrieval depth: how many top-ranked results per sub-question get full
    # content fetched. Each fetch is ~6KB cleaned text kept only until
    # summarization; 10 pages per angle gives deeper reports more primary
    # material (concurrency bounded by MAX_PARALLEL_SEARCH, content released
    # after verification).
    search_fetch_top_n: int = 10

    # v3 retrieval knobs (defaults mirror the v3 modules' getattr fallbacks,
    # so adding them changes nothing until the v3 search is ported):
    # concurrent page fetches per search, bounded separately from query fan-out
    max_parallel_fetch: int = 4
    # transient-error retries per provider search (timeouts fail fast in the
    # LLM chain and stay that way — this covers search providers only)
    search_retry_attempts: int = 3
    # query variants (incl. primary-source fan-out) per search contract
    max_queries_per_contract: int = 3
    # top results kept per provider search before dedup/rank. Higher so the
    # ranker has more to choose the fetched top-N from.
    search_max_results: int = 15

    # Retrieval access hardening: a registrable domain that returns a hard
    # block (403/451) or `search_domain_failure_threshold` transient failures
    # is skipped for `search_domain_cooldown_sec`, so later passes stop
    # re-paying for a publisher that has already refused. Run-scoped; the
    # registry is LRU-bounded by `search_domain_registry_max`.
    search_domain_cooldown_sec: float = 90.0
    search_domain_failure_threshold: int = 3
    search_domain_registry_max: int = 512
    # Canonical URLs that already failed this run are never re-fetched.
    search_failed_url_memory_max: int = 2048
    # Bounded retry budget for TRANSIENT fetch failures only (429/timeout/
    # 5xx/connection). 403 is never retried. Each retry is exponential
    # full-jitter backoff and honours Retry-After when the host sends one.
    search_fetch_retry_attempts: int = 2
    # When a primary/authoritative host is unavailable, issue one targeted
    # fallback query through the EXISTING primary-source machinery aimed at a
    # DIFFERENT authoritative publisher, so equivalent evidence is still
    # acquired. 0 disables the fallback (cooldown alone still applies).
    search_primary_fallback_enabled: bool = True
    search_primary_fallback_max: int = 2

    # ------------------------------------------------------------------
    # SearXNG: the self-hosted metasearch backend (primary web search).
    #
    # Replaces the Tavily/DuckDuckGo provider layer entirely, so the search
    # stack needs NO external search API key. The endpoint is a plain HTTP
    # service; run it however you like (the bundled ./searxng clone runs
    # natively -- no container runtime required).
    # ------------------------------------------------------------------
    searxng_url: str = "http://localhost:8080"
    searxng_enabled: bool = True
    # SearXNG fans out to many upstream engines before it can answer, so it is
    # legitimately slower than a single-provider API. Kept generous because a
    # premature timeout here discards a whole aggregate response.
    searxng_timeout_sec: float = 30.0
    # Upstream result count requested from the aggregate. Higher than
    # search_max_results on purpose: the ranker picks the best few, and a
    # metasearch returns duplicates and low-quality engines that need
    # something to reject.
    searxng_max_results: int = 30
    # `general` = open web, `science` = scholarly indexes. Science is on by
    # default because a research system lives on primary literature; add
    # `news` for current-events questions at the cost of noise.
    searxng_categories: str = "general,science"
    searxng_language: str = "en"
    # 0=off 1=moderate 2=strict. Off: this is a research tool, and a
    # safesearch filter silently truncates legitimate academic/medical
    # results without telling us why they vanished.
    searxng_safesearch: int = 0
    # Circuit-breaker cooldown for the SearXNG backend. Longer than the LLM
    # breaker's 15s on purpose: unlike a remote LLM endpoint this is a LOCAL
    # service we control, so a restart is usually the fix and there is no point
    # re-probing an instance that is down or misconfigured every 15 seconds.
    search_searxng_cooldown_sec: float = 60.0

    # --- SearXNG lifecycle (auto-start) ---------------------------------
    # SearXNG is a separate application and can only be reached over HTTP, so
    # "integration" here means lifecycle, not embedding: the backend spawns the
    # service on startup, waits for /healthz, and stops it on shutdown, so one
    # command runs the whole system instead of three terminals.
    #
    # An instance that is ALREADY reachable is never adopted or killed -- this
    # only ever manages a process it started itself.
    searxng_autostart: bool = True
    # Generous: the webapp imports a large dependency set before it binds, and
    # on a cold page cache the first boot is the slowest.
    searxng_startup_timeout_sec: float = 45.0
    # The searxng clone. Defaults to <repo_root>/searxng, resolved at runtime so
    # a fresh clone needs no configuration.
    searxng_home: str = ""
    # Interpreter used to run it. MUST be SearXNG's own virtualenv (flask, lxml,
    # curl_cffi, valkey are not backend dependencies, so the backend's python
    # cannot run it). Defaults to <searxng_home>/.venv/bin/python.
    searxng_python: str = ""

    # Research run operational limits. Wall-clock and call ceilings are liveness
    # guards (a stalled provider must not hold the run past the interactive
    # deadline; the fan-out needs a runaway guard). No cost/dollar budget.
    max_llm_calls: int = 60
    max_research_seconds: float = 300.0

    # Timeouts (seconds)
    # 25s was sized for a free-tier 8B model that must fail fast to the next
    # provider. A stronger large-context model legitimately needs 30-60s on a
    # planner- or writer-sized prompt, and a premature timeout trips the
    # breaker and degrades whole runs. 60s is the working default for a
    # single good provider; lower it only when a free-tier chain must fail over.
    llm_timeout_sec: float = 60.0
    # How long an LLM provider's circuit breaker stays OPEN after `threshold`
    # consecutive failures. Sized against the request budget a user actually
    # waits on: a healthy direct answer is ~2-3s end to end, so the previous
    # 60s window meant one upstream 503 degraded the whole chain for a full
    # minute — measured as a 68s wait for a one-line answer. Short enough that a
    # transient blip clears inside a user's patience, long enough not to hammer
    # a provider that is genuinely down (a 429 still does NOT open the breaker
    # — see CircuitBreaker.note_throttled).
    llm_breaker_cooldown_sec: float = 15.0
    # Consecutive failures before a provider's breaker opens.
    llm_breaker_threshold: int = 3
    # How long a single call may wait for an open breaker to close before it
    # gives up and the run degrades. Only one wait ever happens per call.
    #
    # This matters because the breaker was designed for a provider CHAIN, where
    # "skip this one, try the next" is the recovery. With a single provider — a
    # UI-selected one is exclusive and runs no fallbacks — there is no next, so
    # one slow call opened the breaker and every later call in the run failed
    # instantly: measured live, one analyst timeout degraded the analyst AND all
    # four synthesizer sections in the same second. Waiting out the cooldown
    # once turns that into a slightly slower, fully executed run. The cap keeps
    # the added latency well inside the research budget; set 0 to disable the
    # wait and restore fail-fast-everywhere.
    llm_breaker_wait_cap_sec: float = 25.0
    # Slower OpenAI-compatible providers take 15-30s on planner-sized
    # prompts (measured live). 60s gives 2-3x headroom while halving the
    # cost of a stalled provider: a free proxy that will not answer at all
    # previously burned 90s (x2 with the ladder second chance) per stage
    # before the breaker opened. Applies to the custom provider only;
    # Groq/HF keep llm_timeout_sec.
    custom_llm_timeout_sec: float = 60.0
    # Sampling temperature sent to a custom/OpenAI-compatible provider that does
    # not specify its own. A per-provider value (set in the Providers tab) wins;
    # this is the fallback. It has to be configurable because some models accept
    # only a fixed set — a provider that allows exactly 0, 0.6 or 1 rejects an
    # unsupported value with a 400 on EVERY call, which silently degraded the
    # whole pipeline to extraction with no way for the user to fix it.
    # 0.3 is a deliberate middle: the writer wants enough variance for natural,
    # non-repetitive prose, while extraction/planning stay stable. Set it lower
    # (0.1/0.0) for strictly deterministic structured callers.
    llm_temperature: float = 0.3
    search_timeout_sec: float = 20.0
    # A full research run takes minutes (retrieval + several LLM stages).
    # Per-provider fail-fasts (auth/402/timeouts) keep doomed calls from eating
    # this budget.
    research_timeout_sec: float = 1000.0

    # Human-in-the-loop plan review. When enabled, a run pauses after planning
    # and waits for the user to approve or edit the proposed outline before
    # section research begins. Off by default so API/headless callers keep the
    # automatic approval; the UI opts in per request.
    plan_review_interactive: bool = False
    # How long a paused run waits for the user before falling back to automatic
    # approval. A run must never hang on an absent user.
    plan_review_timeout_sec: float = 300.0
    # How many alternative outlines the planner proposes for the user to choose
    # from (1 disables alternatives).
    plan_review_alternatives: int = 3

    # Cache (Phase 1.4)
    cache_size_limit_bytes: int = 250_000_000  # 250MB, diskcache size cap
    cache_ttl_sec: int = 3600  # 1 hour (search results)
    # LLM response cache: exact-prompt disk cache. Research pipelines re-issue
    # identical prompts (critic re-evals, re-runs, deterministic preambles) —
    # serving those from disk saves free-tier tokens and seconds of latency.
    # Tests disable it (conftest) so respx mocks are never bypassed.
    llm_cache_enabled: bool = True
    llm_cache_ttl_sec: int = 21600  # 6 hours — sources age out

    # Rate limiting (Phase 1.7)
    rate_limit: str = "5/minute"

    # --- research pipeline composition (app/research/) ---------------------
    # These shape how a run is composed (report length, retrieval breadth,
    # context filtering). Model/provider selection is NOT here — the Model
    # Control Center owns it.
    research_total_words: int = 1200
    research_report_format: str = "APA"
    research_language: str = "english"
    research_report_source: str = "web"
    research_smart_token_limit: int = 12000
    research_strategic_token_limit: int = 8000
    research_max_results_per_query: int = 5
    research_context_filter: str = "auto"  # auto | keyword | none
    research_compression_threshold: int = 8000
    research_keyword_relative_threshold: float = 0.5
    research_keyword_max_results: int = 25
    research_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0"
    )
    research_max_scraper_workers: int = 15
    research_scraper_rate_limit_delay: float = 0.0
    research_doc_path: str = "./my-docs"
    research_curate_sources: bool = False
    # Per-document character cap for context building (guards against
    # pathologically large pages).
    research_max_content_chars: int = 50000
    # Opt out of the SSRF/local-file guard when intentionally fetching internal
    # or self-hosted resources. Off by default.
    allow_private_urls: bool = False

    # NOTE: there is deliberately NO "at least one provider" validator here.
    # It used to raise at import time when GROQ_API_KEY / HUGGINGFACE_API_KEY /
    # CUSTOM_LLM_* were all empty, which made a stock `.env.example` copy
    # unbootable. That is wrong now that the Providers tab is the primary
    # configuration path: keys live in the database, added from the UI, and
    # nothing about them is knowable from the environment at boot.
    #
    # Misconfiguration is still reported loudly, just later and in the right
    # place: `LLMClient.probe_all()` / the call chain raise "No LLM provider
    # configured" per-call, and routes.py turns that into a clean NDJSON
    # `error` event telling the user to add one in the Providers tab. That
    # fails in seconds and keeps the API reachable so the tab can be used,
    # where the old import-time raise killed the whole server instead.


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
