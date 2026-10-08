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
    # Query fan-out: max search queries issued per pass (questions + their
    # alternate phrasings). Bounds latency when plans carry variants.
    search_max_queries_per_pass: int = 12
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
    # Hard cap on accumulated search results across expansion passes. Raw page
    # content is blanked after verification, but the result list still grows
    # with every pass on a long deep run; the oldest entries are dropped once
    # this ceiling is crossed. Never set below a single pass's output.
    search_max_results_retained: int = 80
    # Fix B: hard per-run budget on the expansion loop, a wall alongside the
    # iteration ceiling and the money/time budget. `max_expansion_passes`
    # counts expansion passes actually issued (the first research pass is not
    # an expansion); `max_expansion_searches` counts every extra search query
    # issued by those passes. A broad deep query cannot run unbounded even if
    # every other soft stop is disabled.
    max_expansion_passes: int = 12
    max_expansion_searches: int = 48
    # Corroboration ACQUISITION: how many times each pending claim's
    # corroboration query may be issued before the claim is left as a recorded
    # limitation (bounded, identical re-issues are also deduped by query text);
    # and the claim-vs-page semantic similarity band at/above which a NEW
    # publisher's text counts as independent corroboration.
    max_corroboration_attempts: int = 2
    corroboration_similarity: float = 0.55
    # How many times a research DIMENSION (plan axis) may be searched before it
    # is marked exhausted and stops generating candidates. The dimension-level
    # counterpart of max_corroboration_attempts, and the main convergence lever:
    # without it a dimension that yields no evidence is re-searched every round
    # (the run reached 80 sources with the same gaps open). Kept low because the
    # goal is the best-supported answer, not an exhaustive map — two differently
    # angled searches that both fail are strong evidence the material is not
    # there, and the gap is then reported as a limitation.
    max_dimension_attempts: int = 2
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
    # Full multi-agent runs take minutes (retrieval + 6 LLM stages), the
    # same as upstream GPT Researcher. Per-provider fail-fasts (auth/402/
    # timeouts) keep doomed calls from eating this budget.
    research_timeout_sec: float = 1000.0

    # Cache (Phase 1.4)
    cache_size_limit_bytes: int = 250_000_000  # 250MB, diskcache size cap
    cache_ttl_sec: int = 3600  # 1 hour (search results)
    # LLM response cache: exact-prompt disk cache. Research pipelines re-issue
    # identical prompts (critic re-evals, re-runs, deterministic preambles) —
    # serving those from disk saves free-tier tokens and seconds of latency.
    # Tests disable it (conftest) so respx mocks are never bypassed.
    llm_cache_enabled: bool = True
    llm_cache_ttl_sec: int = 21600  # 6 hours — sources age out
    # Citation validation v2: live URL re-check of the sources the final
    # answer actually cites. Bounded (top N sources, small timeout) and
    # never fatal — a dead link becomes a report warning, not an error.
    citation_check_enabled: bool = True
    citation_check_timeout_sec: float = 5.0
    citation_check_max: int = 10

    # Rate limiting (Phase 1.7)
    rate_limit: str = "5/minute"

    # Intent classification (understand-before-searching): one small LLM call
    # before planning that resolves ambiguous queries ("transformer": AI model
    # vs electrical device), sets the domain and the explanation level, and
    # grounds the plan in the user's likely meaning. Disable to skip the call.
    intent_enabled: bool = True

    # Query router (direct answer vs. research): one small LLM call that
    # decides whether a query can be answered from stable general knowledge
    # or needs the full research pipeline. The deterministic freshness/
    # verification gate always applies; disabling this only skips the
    # optional LLM clearance, which always fails safe to research.
    router_enabled: bool = True
    # Self-confidence at/above which the model's direct-answer clearance is
    # trusted. Below it, research.
    router_min_direct_confidence: float = 0.75
    # Higher bar for clearing a question whose only objection was a question
    # SHAPE ("how many X", a comparative framing). Those are overridable — a
    # textbook constant like "how many legs does a spider have" needs no
    # source — but the model must be markedly more certain when a
    # deterministic signal disagreed with it. Freshness, contested, decision
    # and ambiguity are absolute and no confidence clears them.
    router_min_direct_confidence_clearing_blocker: float = 0.90
    # Direct answers carry no sources, so their delivered confidence is
    # capped strictly below SUFFICIENCY_THRESHOLD (0.75): an ungrounded
    # answer must never be mistakable for a researched one.
    direct_answer_confidence_cap: float = 0.55

    # Answer quality gate (final editor): every synthesized answer is scored
    # 0-100 on accuracy/relevance/evidence/clarity/reasoning from measured
    # pipeline state (no LLM). SYNTHESIS_REVISION_ENABLED adds the LLM half:
    # the writer ALWAYS rewrites its draft once — fed the measured failures
    # when the gate failed, a polish mandate when it passed — and the better-
    # scoring draft ships (never a loop).
    quality_gate_enabled: bool = True
    quality_threshold: float = 70.0
    synthesis_revision_enabled: bool = True

    # Answer-first outline + section-wise synthesis (GPT Researcher parity):
    # the synthesizer derives the report's section shape from the query and
    # evidence before writing. Broad questions (3+ outline dimensions) are
    # written section by section and assembled, which stops a broad query
    # collapsing into one narrow thesis or a source dump. Degrades to the
    # single-pass writer when disabled or when a section call fails.
    #
    # DEFAULT OFF: one strong writer pass over the full evidence pool with a
    # large-context model produces more coherent prose than N independent
    # section calls stitched together (section-wise fragments voice and forces
    # cross-section de-duplication). Enable for very long reports on
    # small-context providers where a single prompt will not fit.
    synthesis_outline_enabled: bool = True
    synthesis_section_wise_enabled: bool = False
    # Single-pass evidence view: how many facts the writer prompt carries (the
    # first rung of the adaptive cap ladder). A large-context model can read a
    # wide slice of the pool, which is what lets it synthesise across sources.
    synthesis_single_pass_fact_cap: int = 80
    # Raw source text the writer is shown alongside the distilled claims.
    # Claims alone read thin: the writer cannot quote, connect or qualify what
    # it never sees. A bounded excerpt per top source gives it primary material
    # WITHOUT re-introducing the full-page memory footprint (the excerpt is
    # capped and only the top sources are included).
    synthesis_source_excerpt_chars: int = 1800
    synthesis_source_excerpt_sources: int = 8
    # Whether the writer prompt carries the process contracts (definition lock,
    # ranking basis, convergence, consistency, construction, evidence balance).
    # They are ALWAYS computed and kept on the run's audit/trace; this only
    # controls whether they are ALSO injected into the writer prompt. A strong
    # model writes more coherent prose with fewer stacked constraints — the
    # audit layer still enforces the same conclusions post-hoc. Set False for
    # prose quality; True to steer the writer explicitly.
    synthesis_writer_process_contracts: bool = False
    # Voice-flattening cleanup: drop sentences that narrate the pipeline's own
    # metrics, and collapse repeated "the evidence does not establish…" phrasing
    # to one instance. Valuable on the fixed-format audit profile (always on
    # there); heavy-handed on a prose report, so off by default.
    synthesis_strict_cleanup: bool = False
    # Context compression: merge near-duplicate claims into one thematic
    # entry before the writer sees them. Distinct claims are never dropped.
    synthesis_context_compression: bool = True
    synthesis_compression_threshold: float = 0.72
    # LLM Analytical Synthesis: one small call between the deterministic
    # SynthesisPlan and the writer that produces a central thesis, major
    # insights, relationships, counter-evidence and cross-source conclusions.
    # Off disables the stage entirely (writer runs on the deterministic plan);
    # a failed or empty call degrades to the same path (AGENTS.md 4.7).
    synthesis_analyst_enabled: bool = True

    # Dynamic Research Depth (Phase 2.8)
    sufficiency_threshold: float = 0.75
    min_marginal_gain: float = 0.03
    # Hard ceiling on expansion depth; 0 means "use MAX_ITERATIONS".
    max_research_depth: int = 0

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
