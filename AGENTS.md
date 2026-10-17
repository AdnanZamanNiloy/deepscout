# AGENTS.md — Operating Rules for AI Coding Agents

This file is the persistent rulebook for any AI coding agent (opencode or otherwise) working in this repository. It applies to **every** task. Read it once at the start of a session and hold yourself to it for every change you make.

One companion document gives context this file doesn't repeat:
- `DeepScout-vision-v2.md` — what the system should eventually do and why (feature rationale, UI mockups, data model).

This file exists because the codebase already shipped real, silent bugs that a bit more discipline would have caught — see Section 2. Its job is to stop that from happening again, not to describe features.

---

## 1. Project snapshot

```text
Stack:      FastAPI + LangGraph (async Python 3.10+), SQLite (aiosqlite),
            React 18 + Vite 5 frontend, httpx for outbound calls.
Generation: remote LLM APIs only — no local model inference. The provider or
            fallback chain selected in the Model Control Center governs every
            engine and helper call through app.core.llm.LLMClient.
Hardware:   target host has 8GB RAM. Concurrency and memory footprint
            are real constraints, not theoretical ones — see Section 5.

Entrypoint:      main.py (FastAPI app, lifespan init; installs the runtime
                 handles)
Pipeline:        app/research/ — the first-party research pipeline: config,
                 prompts, LLM funnel, context filters (BM25), retrieval,
                 scraping, document loaders, the researcher + skills. It calls
                 app.core.llm.LLMClient for generation and
                 app.retrieval.client.SearchClient for retrieval directly — no
                 adapter/bridge monkeypatching.
Agents:          app/agents/ — the LangGraph agent team: orchestrator (graph
                 builder), editor/planner, section research unit + reviewer/
                 reviser, and the report agents (writer, fact checker,
                 visualizer, publisher, human plan-review).
Runtime seam:    app/runtime/ — orchestrator.py (the route's entry point,
                 delegating to app.research.runner.ResearchRunner),
                 event_adapter.py (pipeline events → NDJSON),
                 plan_review.py (human-in-the-loop approval handshake),
                 __init__.py (configure/runtime handle store). The audit
                 reconstruction lives in app/evidence/audit.py; report markdown
                 normalisation in app/report/formatting.py.
Answer/audit:    the primary answer (state["final_report"]) NEVER carries
                 process metadata; the audit/trace carries confidence, quality,
                 evidence ledger, conflicts, decisions.
API:             app/api/routes.py (stream, resume, plan, trace, export,
                 sessions); app/api/providers_routes.py (providers + chains).
Common:          app/common/ — the shared leaves every layer may import:
                 config, logging, pure primitives, the semantic engine.
Core:            app/core/*.py (llm client + response cache, usage ledger,
                 confidence, contradictions, evidence grading, degradation,
                 providers, export, cache).
Shared helpers:  app/retrieval/ (the multi-provider search client + searchkit
                 providers/ranking/fetch), app/evidence/ (claim hygiene, source
                 intelligence, evidence grading inputs, audit reconstruction),
                 app/quality/ (report/answer quality metrics), app/infra/
                 (run limits, retry/breaker primitives, retrieval health).
                 These are shared infrastructure, not the agent team — the
                 agents live in app/agents/.
Persistence:     app/db/sqlite.py (auto-initializing schema)
Benchmarks:      bench/ (run_offline.py deterministic component suite,
                 datasets.py labeled fixtures, eval_contradictions/
                 eval_retrieval/eval_provider/eval_report_quality).
Frontend:        frontend/src/App.jsx consumes the NDJSON stream; components
                 in frontend/src/components/
```

> Architecture note: the retrieval + report pipeline is fully first-party under
> `app/research/`. There is no external research engine and no `sys.path`
> bootstrap. The pipeline's only outside seams are
> `app.core.llm.LLMClient` (generation) and `app.retrieval.client.SearchClient`
> (retrieval), so the Model Control Center governs every call. Do not
> reintroduce a second orchestration path; new pipeline behavior belongs in
> `app/research/`.


Commands:

```bash
# Setup (repo root; backend lives in backend/, frontend in frontend/)
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env   # optional: no key is required to start
cd frontend && npm install && cd ..

# Run
cd backend && uvicorn main:app --host 127.0.0.1 --port 8000 --reload
cd frontend && npm run dev

# Test
cd backend && pytest tests/ -v

# Lint (configured in backend/pyproject.toml; install: pip install "ruff>=0.6")
cd backend && ruff check app tests bench

# Type check (baseline, informational — see [tool.mypy] in backend/pyproject.toml)
cd backend && mypy app

# Smoke test
curl -s http://127.0.0.1:8000/api/health
curl -N -X POST http://127.0.0.1:8000/api/research/stream \
  -H "Content-Type: application/json" \
  -d '{"query": "What is retrieval augmented generation?"}'
```

---

## 2. Confirmed bug history (read this before touching agent code)

> Path note: entries below name files as they were when the bug was found. Since
> then the misnamed `app/agents/` package (which held shared infrastructure, not
> agents) was split by purpose: `app/agents/search.py` → `app/retrieval/client.py`;
> `app/agents/evidence_utils.py` → `app/evidence/utils.py`; `app/agents/sources/` →
> `app/evidence/sources/`; `app/agents/budget.py` → `app/infra/budget.py`. The
> name `app/agents/` now denotes the real agent team (moved from
> `app/research/agents/`). Historical paths are kept for traceability against
> the referenced commits.

> v2 note: six additional bugs were found by `bench/run_offline.py` and are
> documented in CHANGELOG.md ("Benchmark-driven bug fixes") — the notable
> classes for future work: (1) bare "up"/"down" as direction tokens leak
> from hyphenated words; (2) negation words must NEVER be stopwords in any
> similarity/verification component; (3) a dedup threshold alone cannot
> merge "X"/"not X" safely — always pair lexical merging with a polarity
> guard.

These are real bugs found by reading the code, not hypotheticals. Each one below is turned into a standing rule in Section 4. Keep this list updated — when you fix a bug or find a new one, add it here with the same format, so the next session doesn't reintroduce it.

```text
[FIXED — Phase 0 task 0.1, commit f225b3c] app/agents/planner.py
  PLANNER_SYSTEM_PROMPT was referenced but never defined. Every LLM
  planning call raised NameError, caught by a broad `except Exception`,
  silently falling back to a fixed 4-question template. The planner
  never used the LLM, for the entire history of the project, and
  nothing detected it. Root cause: no import-time check, no test, and
  an exception handler broad enough to hide a NameError.

[FIXED — Phase 0 task 0.2, commit d878d07] app/agents/critic.py
  CRITIC_SYSTEM_PROMPT described one JSON schema; the code that parsed
  the LLM's response read a different, incompatible schema. Root
  cause: the prompt and the parser were edited independently and never
  cross-checked against each other.

[FIXED — Phase 0 task 0.3; fully removed in Phase 1] app/graph/workflow.py, app/api/routes.py
  RUNTIME_STATE, a module-level dict keyed by request_id, was written
  to on every request and never cleaned up — an unbounded memory leak.
  Root cause: global mutable state used for what should have been a
  local/scoped value. Phase 1 replaced it with a local `last_snapshot`
  variable in event_stream(); the global no longer exists.

[FIXED — Phase 1 task 1.4] app/agents/search.py
  Multiple bare `except: return []` / `except: return ""` blocks
  swallowed all errors, including transient ones a retry would fix.
  Root cause: exception handling written for "never crash" without
  distinguishing expected-empty from actually-broken. All bare excepts
  now log with context; provider failures degrade to empty results
  instead of killing the run.

[FIXED — Phase 2 audit] app/core/config.py
  `SettingsConfigDict(env_file=(".env", ".env.example"))` — in
  pydantic-settings the LAST file wins, so the placeholder
  `your_groq_api_key_here` from .env.example silently overrode the real
  key from .env. Every LLM call 401'd and the pipeline "worked" via
  deterministic fallbacks, hiding the misconfiguration for two phases.
  Root cause: wrong env_file precedence plus fallbacks that mask
  provider auth failures. Fixed by putting .env last and adding a
  regression test. Secondary finding: the configured Groq model
  (llama-3.1-8b-instant) had been decommissioned upstream — always
  verify model IDs against the provider when auth succeeds but calls
  still fail.
```

[FIXED — verified-flags re-emission] app/api/routes.py (findings emission)
  Findings NDJSON events were length-based, so verifier in-place annotation
  never re-emitted. The route now emits the annotated facts once when
  verification flags first appear (`verified_update: true`), and the UI
  upserts by claim text instead of appending duplicates. Eval scoring of
  persisted trace truth is unchanged.

[FIXED — answer-quality audit] app/agents/summarizer.py (stale fact cache)
  The summarizer cache key embeds PROMPT_VERSION, but the version was not
  bumped when claim cleaning landed — live runs kept serving pre-cleaner
  claims ("about tra" fragments) from cache for up to an hour, making the
  fix look broken. Any change to claim shape, cleaning, fields, or
  thresholds MUST bump PROMPT_VERSION, or verification will chase ghosts.
```

[FIXED — stranded approval card] app/agents/report.py, app/runtime/
  plan_review.py, app/runtime/event_adapter.py, frontend/src/App.jsx
  An interactive run's review window closed on a silent timeout: the gate
  fell through to the automatic review, research proceeded, and a report was
  generated with no approval at all — while the live "start research" card
  stayed pinned above it, still accepting clicks the backend would reject.
  Three compounding causes: (a) the timeout path emitted nothing on the wire,
  so a run that continued by itself was indistinguishable from one that
  ignored the user; (b) the card was cleared ONLY by the client submitting a
  decision, so every other exit (expiry, error, abort, stream end) stranded
  it; (c) the deadline was never communicated, making a 5-minute wait read as
  a hang. Fixed: the request frame carries the server's `deadline_ts` and the
  gate emits an `expired` frame when the window closes; the card closes on
  expiry, on error, on abort, and in `launch`'s `finally` as a catch-all; it
  counts down to the real deadline and disables itself once it passes.
  Corollary found while fixing it: a RE-request (the user sent the plan back
  for revision) reuses the run id, so per-run state in the card survived the
  outline it belonged to — cards now key on the frame's per-request id.
  Rule: ANY fallback that continues a run without the user (timeout, default,
  degradation) MUST emit the event the client needs to withdraw whatever it is
  showing for that step. An affordance must never outlive the pause it belongs
  to, and the client must never be the only thing that can close it.

[FIXED — rail desynced from the live trace] app/agents/team.py,
  app/runtime/event_adapter.py, app/api/routes.py,
  frontend/src/App.jsx, frontend/src/components/IntelligencePanel.jsx
  The right-hand Research Workflow rail showed all eight agents "Queued" and
  "0 agents working" while the trace beside it streamed live lines — the two
  views of one run disagreed by construction. Three compounding causes:
  (a) the wire only ever reported node COMPLETIONS (LangGraph's
      `stream_mode="updates"` yields after a node finishes), so the client had
      no way to know a stage had started; before the first artifact landed the
      rail read `reached = 0` and nothing could be "working";
  (b) `deriveAgents` inferred status from finished artifacts (sources → plan →
      claims → reviews → done) instead of from progress, so it could not
      represent "running", and it treated the plan-review gate as instantly
      passed even while the user was being asked to approve;
  (c) the stage key collapsed `pathfinder` and `scout_swarm` onto `retrieval`,
      so the two could never be distinguished on the ladder.
  Fixed: every graph node is wrapped to announce its start AND end on the sink
  (`ResearchTeam._staged`, end signalled from `finally` so a raising node cannot
  leave the rail spinning); the adapter maps that to a `stage` frame keyed on
  the NODE; the route persists the lifecycle to `agent_events` using the frame's
  own timestamp, so a restored run rebuilds the ladder it streamed. The rail now
  reads that lifecycle, with the artifact ladder kept only as a fallback for
  runs restored from before stage frames existed. Rule: two views of the same
  run MUST be driven by one signal — when a UI re-derives state that the wire
  already knows, it will eventually contradict the authoritative view. Corollary
  found while fixing it: `stage_frame` and the retrieval notes both dedupe on
  node names, so a single shared set made whichever ran first silence the other;
  and a `pipeline_stage` signal must be handled BEFORE any gate that reads
  `stage` with a fallback to `node`, or the signal never passes it.

[FIXED — provider-timeout trap] app/core/llm.py (retries vs research budget)
  Every provider call retried 4x in tenacity plus 3x in generate_json, each
  attempt burning the full 25s llm_timeout_sec — a slow free-tier model held
  the planner past the 90s research timeout before Groq/fallback was ever
  reached, and the UI sat on "Planner analyzing" until the route killed the
  run. Rule: timeouts fail fast to the next provider (never retried at
  either level), one timeout trips the circuit breaker immediately, and
  Retry-After honors are capped at 3s. Any retry policy whose worst case
  exceeds the research budget reintroduces this bug — do the multiplication
  before adding attempts. Corollary (2026-09, custom-provider era): the
  shared 25s timeout also broke the OTHER way — a slower OpenAI-compatible
  host needs 30-90s on big prompts, and a premature ReadTimeout trips the
  breaker and degrades whole runs; hence the separate
  CUSTOM_LLM_TIMEOUT_SEC. Also: `str(httpx.ReadTimeout())` is "" — never
  format provider errors with `.splitlines()[0]` (IndexError inside a
  `raise` replaces the fail-fast error with a retryable one), and never
  cache an agent's EMPTY result on LLM failure (the summarizer did —
  the [] poisoned its cache key for a full TTL hour, extending degradation
  past provider recovery).

[FIXED — script-UA block] app/agents/search.py (Wikipedia 403)
  Wikipedia returns 403 "Please set a user-agent" to httpx's default UA, so
  every _wiki call degraded to [] and live runs lost a free high-trust
  source. Generic page fetch now sends a browser UA (upstream parity);
  the MediaWiki API keeps an identifying UA per its UA policy. Any new
  HTTP client with a default UA against a policy-gated host repeats this.

[FIXED — overlapping backends] ops, not code (stale-code verification)
  Two uvicorn processes briefly shared port 8000 across a racy kill/relaunch
  (plus a concurrent browser run), so a "verification" run was served by a
  stale process and a real regression hunt chased a ghost for an hour.
  Restarts are now kill-by-PID → poll until the port releases → launch →
  confirm a single listener, and live verifications assert the request_id
  appears in the fresh backend log. Never trust a live result without that
  provenance check.

[FIXED — silent full-LLM outage scored "High"] app/core/llm.py, app/core/confidence.py, app/graph/workflow.py
  Live run: custom provider 402 (no credits), Groq 429 (free-tier TPD
  200K exhausted), HUGGINGFACE_API_KEY a placeholder — so ALL LLM calls
  failed and the run "completed" on the extractive fallbacks with off-topic
  sentences (a Malawi electrification paragraph on a Bangladesh query) —
  while the Confidence Engine scored it 0.772 "High" and the UI showed a
  confident, authoritative report. `degraded` was emitted on the wire but
  never rendered. Four compounding causes, all fixed:
  (a) generate_json retried the whole provider chain 3x on the
      deterministic "No LLM provider configured" error; now
      AllProvidersFailedError fails fast with per-provider detail
      (app/core/llm.py).
  (b) No LLM concurrency cap: 3 parallel ~6K-token summarizer prompts are
      what exhausted Groq's TPD in the first place; LLMClient now holds a
      MAX_PARALLEL_LLM semaphore around every call (the default has since
      been raised from 2 to 4 — see CONFIG.md).
  (c) compute_confidence ignored degradation; extractive-fallback claims
      trivially self-verify, so source/verification signals read high.
      Degraded summarizer/synthesizer now caps overall at 0.55 — below the
      0.75 sufficiency threshold — with an explanatory note in breakdown.
      REGRESSION (2026-10): this cap silently died when the audit bridge was
      rewritten. compute_audit accepted a `degraded` list and never forwarded
      it, so the engine scored an extractive run as though nothing had
      degraded and could still read "High". The state is now passed through
      (with an explicit provider_degraded flag, defaulted from the
      request-scoped context) and `test_audit_bridge.py` pins every cap path.
      Corollary: a signal enforced one layer away from where it is produced is
      one refactor from being a constant — pass it explicitly, and test the
      caller, not just the function that honors it.
  (d) Frontend never rendered degraded/answer_support/decisions;
      AnswerCard now shows a degraded-run banner, the Decision Layer, and
      citation support. A degraded run must never be mistakable for a
      healthy one.
  Rule: any new signal that makes evidence CHEAPER to produce (fallbacks,
  caching, fewer fetches) must be checked against the confidence engine —
  self-verifying evidence inflates every lexical signal by construction.

[FIXED — findings stream truncation] app/api/routes.py
  The findings emitter sliced `facts[emitted:emitted+3]` but set
  `emitted_findings = len(facts)`, so facts 4..N of any large batch were
  never streamed (only the one-time verified_update re-emit rescued them).
  Now emits every new fact. Off-by-one slicing paired with a
  consume-the-whole-batch marker is the pattern to watch for.

[FIXED — critic definition gate forced loops] app/agents/critic.py
  The deterministic gate required an " is " claim in the first 5 facts
  unconditionally. Comparative/analytical queries ("A vs B economics?")
  rarely produce one, so is_sufficient was forced False every iteration —
  guaranteed expansion loops (more search + LLM spend) and every such
  report stamped "incomplete" regardless of quality. Definitional shape is
  now required only when query_type=factual or the query itself is
  definitional ("what is/define/explain"). Gate conditions must be
  query-type-aware, not written for the most common demo query.

[FIXED — latency: serial I/O on hot paths] app/agents/search.py,
  app/agents/synthesizer.py, app/graph/workflow.py
  Four awaits ran sequentially where Section 4.6 requires gather:
  (1) Tavily's DDG rescue awaited text THEN news on every query while the
  Tavily circuit was open; (2) primary-fallback substitution queries ran
  one awaited provider round at a time; (3) the section-wise synthesizer
  wrote each outline section with a serial awaited LLM call; (4)
  route_query ran AFTER the intent/search gather, adding one serial LLM
  round-trip before every planner. Cold-cache A/B (same query): search
  pass-1 111s -> 86s, intent block 31s -> 17s. Related: per-node
  agent_events started_at was stubbed equal to ended_at, so stage timing
  was unmeasurable — node events now carry the previous superstep's
  timestamp as started_at. Rule: before awaiting inside a loop, confirm
  iterations don't depend on each other; if they don't, gather (bounded
  when sockets/concurrency matter).

[FIXED — cross-source synthesis scored as unsupported] app/agents/evidence_utils.py
  verify_answer_support scored each cited sentence by its BEST match against
  a SINGLE cited source's claims. A legitimate synthesis sentence ("Taken
  together, these findings indicate…") deliberately shares little vocabulary
  with any one source, so it scored ~0.03 and was counted as unsupported
  contamination — the relevance score punished exactly the reasoning the
  synthesizer is instructed to produce. A sentence citing two or more
  DISTINCT verified sources is now classified `synthesis` (counted
  separately; never "supported", never contamination), while fabricated
  numbers in such a sentence still fail as `numeric_failure`. Rule: any
  per-sentence check that assumes ONE source per sentence must be revisited
  when the pipeline is told to reason ACROSS sources.

[FIXED — guidance query split by a modifier adjective] app/agents/ambiguity.py,
  app/agents/planning/plan.py, app/agents/planning/contracts.py,
  app/agents/planning/prompts.py, app/core/primitives.py
  "Suggest me highly demanding research topics in computer science" was treated
  as AMBIGUOUS on the word "demanding": the intent layer's curated
  under-specification table emitted two JOB-market readings ("Stressful or
  difficult", "Requiring high skill or responsibility"), decide_ambiguity chose
  `separate` with an assumption, and `_intent_research_senses` replaced the
  plan's actual subject with those reading labels — every sub-question became
  "stressful or difficult definition explanation overview", so no on-topic
  evidence could ever be retrieved. The term match is context-free, so ANY
  multi-reading modifier misfired inside a guidance request ("recommend the
  best programming language" -> "Best fit for a use case"). Fix: a shared
  `is_guidance_query` (app/core/primitives.py) makes the ambiguity policy skip
  under-specification readings for suggestion/advice requests; the directive
  prompt gained recommendation dimensions; the fallback plan is option-shaped;
  `_query_concept` strips the guidance verb. Rule: a modifier adjective inside
  a request for suggestions is not a request-level ambiguity — only homonym
  SENSES of a noun the user typed may split a guidance request. (Note: the
  ambiguity policy this fixed was removed in the first-party-pipeline
  consolidation; `is_guidance_query` is listed here as the historical fix, not
  as a current export.)

[FIXED — recommendation query falsely stopped by the definition gate]
  app/agents/orchestrator.py, app/agents/critic.py
  A "suggest some topics" request was classified `factual` (the lexical
  classifier has no recommendation type), so the critic's definition gate
  required an "X is Y" claim that recommendation evidence never contains,
  emitted `no definitional claim`, and the depth controller's is_semantic_gap
  (whose markers include the bare token "definition") finalized the run at
  iteration 1 with planned axes still uncovered — thin evidence and a degraded
  fallback. Fix: guidance requests classify as `exploratory` and the gate
  excludes them. Rule: a query-type-driven gate must be verified for every
  query type the classifier can emit, not only the demo query's type; and a
  substring marker like "definition" inside is_semantic_gap must not be
  satisfiable by an ordinary uncovered-axis name.

[CONSOLIDATED — first-party pipeline] app/research/* (backend/engine/ removed)
  The research pipeline was fully re-authored as first-party code under
  `app/research/` and the external engine dependency was deleted. Why this
  matters for future sessions: (a) there is now ONE orchestration path — the
  LangGraph team in `app/agents/` — with no `sys.path` bootstrap, no
  monkeypatched provider factory, and no third-party research package; (b) the pipeline calls
  `app.core.llm.LLMClient` and `app.retrieval.client.SearchClient` DIRECTLY, so
  the Model Control Center governs every call with no bridge seam to keep in
  sync; (c) the dead first-party modules the engine had already replaced
  (`app/agents/orchestrator.py`, `app/agents/confidence.py`,
  `app/agents/planner.py`, `app/agents/planning/`, `app/core/schemas.py`) were
  removed, as was the inert epistemic-adjustment branch in
  `app/core/confidence.py`; (d) retrieval is unified — every retriever name
  resolves to `app/research/retrieval/base.py::SearchClientRetriever`. Rule:
  new pipeline behavior belongs in `app/research/`; do not add a second
  orchestration path or reintroduce an external research dependency.
```

[FIXED — confidence measured nothing it claimed to measure] app/evidence/verify.py (new), app/evidence/audit.py, app/research/runner.py, app/core/confidence.py
  Live confidence was structurally capped near 0.67 and the UI's "High" band
  (>= 0.8) was unreachable, for four compounding reasons: `build_facts` stamped
  `verified: True` on every reconstructed claim (claim_verification_strength
  read a structural 0.0 — every run silently lost its 0.15 weight), the
  arch-upgrade deleted the only writers of `corroboration_count`
  (cross_source_agreement read ~0.04-0.08 from a weakened fallback),
  `compute_audit` passed `critique={}` so critic survival sat on the 0.6
  "model hedge" branch forever, and citation coverage was 1.0 by construction
  (the same hardcoded `verified: True`). Fixed: claims are verified against
  the cited source's captured text (app/evidence/verify.py — weighted token
  overlap, numeric grounding, polarity; a source without text is explicitly
  unverified); cross-publisher restatements are attached via
  `apply_corroboration` using the engine's own `claim_similarity` at its own
  band (one metric, one threshold, defined next to its consumer — the hybrid
  metric scored true paraphrases 0.33-0.51 against a 0.55 band and would have
  found nothing); the run's fact-check verdict and plan axes reach
  `compute_audit`; unmeasured signals are OMITTED and the remaining weights
  renormalized (`ENGINE_VERSION` bumped to confidence-v3). Rule: every input a
  scoring signal reads must be produced by the pass that owns it — a
  hardcoded default ("verified: True") buys a number that looks measured and
  is not, and a signal whose input has no writer is a constant wearing a
  formula. Corollary: any per-claim similarity check must use the metric its
  band was calibrated on, or recall silently collapses.

[FIXED — checkpoint resume looped forever mid-revision] app/db/store/checkpoints.py, app/core/usage.py, app/research/runner.py
  Resuming a crashed run could end in GraphRecursionError. Two compounding
  causes: (a) per-node fire-and-forget writes committed OUT OF ORDER — an
  older snapshot could overwrite a newer one, so the row a resume loaded
  could predate the node the run actually stopped after; (b) `completed_nodes`
  stores node NAMES, and a checkpoint taken after the fact-checker rejected
  (or the plan gate rejected) listed the rejecting node as done — the resumed
  run skipped the rerun routing still demanded, routing re-read the saved
  rejection, and the graph cycled until the recursion limit (reproduced live
  by re-running a crashed run id: 180 events, no search, immediate error).
  Fixed: `queue_checkpoint` serializes at call time and writes latest-wins
  single-flight (the committed row is always the last completed node);
  `_resume_completed_nodes` reopens [architect, review_gate] and
  [narrative_writer, evidence_auditor] when the saved state carries a pending
  objection (marker tests mirror routing exactly — including that an empty
  notes string is an unavailable check, not an objection); `clear_checkpoint`
  drops pending writes and waits out in-flight ones so a terminal delete
  cannot be resurrected. Rule: a completion list keyed on node names cannot
  represent "ran, but its last act was a rejection" — and fire-and-forget
  writes to one row are not ordered; if the last write must win, say so in
  the code.

[FIXED — chatty fact-check verdicts fabricated objections] app/agents/report.py, app/agents/routing.py
  The auditor asked for exactly the string 'None' on a clean draft; chatty
  free-tier models replied with a description of their review process, every
  consumer read it as a REJECTION, and every run paid a pointless writer
  revision plus a second audit pass — then shipped with a "standing
  objection" the now-honest confidence signal priced (critic_survival 0.58 on
  every smoke run). The structured replacement declares {"issues": [...]} and
  parses exactly that key; the schema demand lives in the USER message
  (models ignored a schema stated only in the system message and returned
  prose with no JSON at all — generate_json failed all retries); a failed or
  unavailable check is "" (no verdict): routing accepts it (never a blind,
  feedback-less revision) and the audit records it unmeasured rather than as
  a pass or a fabricated fail. Rule: when a model output feeds a SECOND
  system (routing, scoring), a free-text sentinel is a parsing contract in
  disguise — make the shape explicit and keep the fallback distinguishable
  from both verdicts.

[FIXED — audit-capture pass killed every completed run] app/runtime/orchestrator.py,
  app/research/runner.py, app/api/routes.py
  The audit-time source capture was implemented on ResearchRunner, but the
  routes call methods on the PipelineRunner FACADE (orchestrator.py). The
  first live run after the change completed all research and then died with
  `AttributeError: 'PipelineRunner' object has no attribute
  'capture_cited_sources'` at the final frame — stream truncated, no report
  frame, no persistence. Caught only by the live smoke, not by unit tests
  (which construct ResearchRunner directly). Fixed by delegating on the
  facade + a regression test that pins the delegation. Rule: this codebase
  has two runner identities — anything routes call must exist on
  PipelineRunner; when touching the runner seam, grep both sides. A facade
  that forwards some methods is not an abstraction, it is a list of the
  methods someone remembered.

[FIXED — docs page rendered blank, every check green]
  frontend/public/docs.content.js, frontend/test/docs-accuracy.test.js
  The built-in docs (`public/docs.html` + `docs.app.js` + `docs.content.js`)
  are served VERBATIM into an iframe, so the Vite build never parses them, and
  every test that mentioned them matched them as text. Two line-based edits
  left a missing comma between two array entries and three benchmark rows with
  their closing quote eaten. The payload failed to parse, `window.DEEPSCOUT_DOCS`
  stayed undefined, `DocsView` threw on `undefined.id`, and `#/docs` rendered
  completely blank — with the build green and all 327 tests green. A stale
  duplicate paragraph from the same edit also claimed a "stale count" and
  "unreachable sources" in the verifier output; neither exists (the census is a
  log line of failure classes). Fixed, and the suite now COMPILES AND EXECUTES
  the payload in a `node:vm` sandbox, then asserts the tree it publishes:
  every nav entry resolves to a non-empty page, no HTML string ends mid-tag.
  Rule: a file that no build step and no test ever EXECUTES has unverified
  correctness no matter how many assertions mention it — asserting on text is
  not the same as running the thing. Anything in `public/` that ships as
  script must be executed by a test; when editing it line-by-line, run
  `node --check` on it before you run the suite, because the suite's regexes
  will happily pass over a syntax error.

If you find a new instance of any of these patterns anywhere in the codebase while working on something else, fix it or flag it in your commit message — don't leave it for later just because it's outside your current task's file scope.

---

## 3. Non-negotiable workflow rules

```text
1. One task = one commit, small and reviewable. Use a short scope tag
   in the message, e.g. "[v3-B2] port v3 search".

2. Branch per phase/feature, off the default branch. Never force-push.
   Never rewrite already-pushed history.

3. Run the task's verification command before moving on. A task isn't
   done because the code was written — it's done because you checked
   it does what it claims.

4. Never commit .env, API keys, or database files with real data.
   research.db was already committed once despite being gitignored —
   check `git status` before every commit, not just the first one.

5. Every new setting goes through Settings (app/common/config.py) and
   gets a placeholder in .env.example. Never call os.getenv() ad hoc
   in a new module — it's how settings drift out of sync with what's
   documented.

6. If a task requires an architecture-level decision not covered by
   DeepScout-vision-v2.md (e.g. changing the DB engine,
   removing an existing working code path, changing the public API
   contract in a breaking way), stop and surface the question instead
   of deciding unilaterally. Small implementation choices within a
   task's stated scope don't need escalation — make the smallest
   reasonable choice and note it in the commit message.

7. Do not remove or weaken an existing deterministic fallback path
   (planner.py, critic.py, synthesizer.py already have this pattern:
   try LLM → on failure, fall back to a rule-based default) when adding
   new LLM-driven behavior. Every new agent must have the same shape —
   see Section 6.
```

---

## 4. Bug-class prevention rules

Each rule below exists because of a specific, real bug (Section 2). Apply all of them to every change you make, not just new files.

### 4.1 — Never reference a constant/prompt without defining it in scope

The exact failure mode that broke the planner. Before finishing any task that adds or edits a pipeline module:

```bash
python -c "import app.research.<module_you_touched>"
```

This alone would have caught the undefined `PLANNER_SYSTEM_PROMPT` immediately. Run it for every module you touch, every time — it costs one second and it is the single highest-value check in this repo.

### 4.2 — A system prompt's declared schema and the parser must match, always

If a `*_SYSTEM_PROMPT` documents an output JSON shape, the function that calls `llm.generate_json(...)` and reads `payload.get(...)` must read exactly those keys — not a different, more convenient shape written separately. Before considering any prompt "final":

```text
1. List every key the system prompt's schema promises.
2. Grep the parsing code in the same function for every .get("key")
   call it makes.
3. The two lists must be identical. If they're not, fix the prompt or
   the parser — don't leave both and hope the model guesses right.
```

If there's a separate `*_USER_TEMPLATE` constant, it must actually be used — a defined-but-unused template (as `CRITIC_USER_TEMPLATE` was) is a sign the prompt and the code were edited independently and drifted.

### 4.3 — No module-level mutable state without an explicit cleanup path

Any `dict`/`list`/`set` declared at module scope that gets written to per-request or per-run (keyed by `request_id`, `run_id`, etc.) is a leak waiting to happen. Prefer a local variable scoped to the request's coroutine. If module-level state is genuinely necessary, every write path must have a matching cleanup — `pop()` in a `finally` block, a TTL, or a bounded size with eviction. State that only grows is not acceptable at any scale on an 8GB host.

### 4.4 — No bare or silently-swallowing exception handlers

```text
Not acceptable:  except: return []
Not acceptable:  except Exception: return ""
Minimum bar:     except Exception as exc: logger.warning(..., exc_info=exc); return fallback_value
```

If a fallback is genuinely intended behavior (not just "don't crash"), say so in a comment. A silent catch is exactly what let the planner bug survive undetected — every broad exception handler is a place a future bug can hide the same way. The standard in `app/research/` is the pattern every skill follows: `logger.warning("[Name] ...", exc_info=exc)` on the way to a documented deterministic fallback, as in `ResearchConductor._search` (logs the retriever that failed, returns `[]` for that retriever only) and `app/research/runner.py::_sanitize_markdown` (falls back to a plain whitespace tidy rather than dropping the report).

### 4.5 — No dead code

If you define a constant, template, or function and don't call it in the same commit, either wire it in or delete it. Before finishing a task:

```bash
grep -rn "YOUR_NEW_NAME" app/ --include="*.py" | grep -v __pycache__
```

If it only appears once (the definition), it's dead. This is also how `CRITIC_USER_TEMPLATE` was caught.

### 4.6 — Concurrent by default for independent I/O

```text
Not acceptable:  for item in items: await do_io(item)   # when calls
                                                          # are independent
Preferred:       await asyncio.gather(*(do_io(item) for item in items))
```

`SearchClient.run_search` already does this correctly across sub-questions. `_search`'s top-3 content-fetch loop does not — don't copy that pattern into new code; fix it if you touch that function, and don't introduce the sequential version elsewhere.

### 4.7 — Every LLM-calling agent needs a deterministic fallback

Match the shape used throughout `app/research/` (a model call wrapped so any
failure or empty output degrades to a deterministic default, never a crash):

```python
async def name_step(llm: LLMFunnel, ...) -> ...:
    try:
        payload = await llm.generate_json(SYSTEM_PROMPT, user_prompt)
    except Exception as exc:
        logger.warning("[Name] LLM failed, using fallback", exc_info=exc)
        return deterministic_fallback(...)

    result = extract_and_validate(payload)
    if not result:
        logger.warning("[Name] empty/invalid LLM output, using fallback")
        return deterministic_fallback(...)
    return result
```

Every new LLM-calling step must follow this shape. A new step with no non-LLM
fallback is a regression relative to the rest of the codebase, even if it
"works" when the API is up.

### 4.8 — DB schema changes are additive and idempotent

`CREATE TABLE IF NOT EXISTS` only. Never drop or destructively alter an existing table (`research_reports` in particular must keep working for any existing reader). New tables get added, old ones stay.

### 4.9 — New NDJSON event types need a frontend case in the same commit

`app/api/routes.py`'s `event_line()` and `frontend/src/App.jsx`'s `applyEvent()` switch are an implicit contract. `applyEvent` has a `default: break` — a backend event type with no matching `case` is silently dropped, not an error. If you add an event type (e.g. `decisions`), add the frontend case in the same commit, or it will look like the feature does nothing.

---

## 5. Hardware-aware defaults

The target host has 8GB RAM and no local model inference — every constraint here is about orchestration memory and concurrency, not compute:

```text
- Default concurrency caps (MAX_PARALLEL_SEARCH, MAX_PARALLEL_FETCH and
  MAX_PARALLEL_AGENTS) start low (3-4) and only get raised after you've
  actually measured memory headroom under load — not because a feature
  spec suggests a bigger number. MAX_PARALLEL_AGENTS is the wave size
  for section research, so lowering it also tightens cross-section
  de-duplication at the cost of latency.
- Release raw fetched content (SearchResult.content) from memory once
  it's been summarized. Don't let full source text accumulate across a
  whole research run's state.
- Any new cache (diskcache or otherwise) must have an explicit size
  cap. An unbounded cache is a slower way to hit the same OOM as
  RUNTIME_STATE was causing.
- No Redis, no message queue, no self-hosted vector DB, no local
  embedding models. Nothing in this project's roadmap needs them, and
  they cost RAM this host doesn't have.
```

---

## 6. Definition of Done

Apply this checklist to every task:

```text
[ ] python -c "import <every module you touched>" succeeds
[ ] No bare `except:` / silent `except Exception: return <empty>`
    introduced — failures are logged with context
[ ] No new dead code (grep for anything you defined but never call)
[ ] Any new module-level mutable state has an explicit cleanup path
[ ] Any new LLM-calling function has a deterministic fallback (4.7)
[ ] Any new setting is in Settings + .env.example, not a raw os.getenv()
[ ] Any new DB table uses CREATE TABLE IF NOT EXISTS and doesn't break
    existing readers
[ ] Any new NDJSON event type has a matching case in frontend/src/App.jsx
[ ] Independent I/O calls use asyncio.gather, not a sequential loop,
    unless there's a stated reason otherwise
[ ] Tests added/updated and passing (pytest tests/ -v) — add coverage
    for what you built
[ ] Manual smoke test run against the real running server, not just
    unit tests in isolation
[ ] git status shows nothing unexpected (no .env, no *.db, no
    accidentally-included data) before committing
```

If any box can't be checked, the task isn't done — say so explicitly rather than reporting completion.

---

## 7. When unsure

```text
- Feature intent/rationale unclear -> check DeepScout-vision-v2.md first.
- Whether something counts as "done" for a specific feature -> check
  DeepScout-vision-v2.md's acceptance criteria — several features (Feature
  02/03, Feature 06, Feature 14) have explicit rules about not being
  marked complete on a partial implementation.
- Still unclear, or the question is architectural rather than
  implementation-level -> stop and ask (Section 3, rule 6). Don't
  guess on anything that would be expensive to undo.
```
