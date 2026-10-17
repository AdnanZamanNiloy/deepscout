# Changelog

## Unreleased — Confident where measured, quiet where not

Live confidence was structurally depressed: the audit bridge stamped
`verified: True` on every reconstructed claim (the verification signal read a
structural 0.0 — every run quietly lost its 0.15 weight), no pass had written
`corroboration_count` since the arch-upgrade (agreement read ~0.04-0.08), the
critic signal sat on a "model hedge" branch forever, and citation coverage was
1.0 by construction. The UI's "High" band (>=0.8) was unreachable.

- **Claims are verified, not assumed** (`app/evidence/verify.py`, new). Each
  reconstructed claim is checked against its cited source's captured text:
  weighted token overlap, numeric grounding (a number absent from the source
  fails the claim), polarity. A source without captured text is explicitly
  unverified rather than silently trusted.
- **Corroboration is measured again** (`app/evidence/audit.py`). Claims
  restated by a different publisher's source are attached through the
  sanctioned `apply_corroboration` path, using the engine's own
  `claim_similarity` at its own band — one metric, one threshold, defined
  next to its consumer.
- **The fact-check verdict reaches the score** (`app/research/runner.py`).
  The auditor's verdict and the plan's axes now reach `compute_audit`. The
  auditor returns structured `{"issues": [...]}` (schema demand lives in the
  user message — chatty free-tier models ignored a system-only schema and
  returned prose, so every check read as an objection); named issues price
  individually; a failed/unavailable check is "no verdict" — routing accepts
  it instead of buying a blind revision, and the audit records it as
  unmeasured, never as a pass.
- **Unmeasured signals are omitted, not zeroed** (`app/core/confidence.py`,
  engine `confidence-v3`). A pool with no verification data renormalizes the
  remaining weights and the breakdown notes the omission, instead of
  multiplying a structural 0.0. Degraded runs still cap at 0.55.
- **Checkpoint resume can no longer loop or rewind**
  (`app/db/store/checkpoints.py`, `app/research/runner.py`). Per-node
  fire-and-forget writes could commit out of order, so the row a resume
  loaded could predate the node the run actually stopped after; and a
  checkpoint saved mid-revision listed the rejecting node as completed, so
  the resumed run skipped the rerun routing still demanded — until
  `GraphRecursionError`. Writes now serialize latest-wins on a snapshot taken
  at call time; resume reopens [rejector, consumer] for any pending
  plan/fact-check objection; a terminal clear cannot be resurrected by a
  queued write.
- **Multi-cited sentences verify against the union of their sources**
  (`app/evidence/audit.py`, `app/evidence/verify.py`). A sentence citing two
  sources was judged against the FIRST one only, so cross-source synthesis
  sentences failed the overlap band by construction — the same bug class as
  the answer-support fix. Attribution now collects every known link in the
  sentence; each fact carries its full `sources` list.
- **Cited pages are fetched at audit time, bounded and SSRF-guarded**
  (`app/research/source_capture.py`, new). Captured text for many cited URLs
  was only the search snippet, so the verifier failed claims the page itself
  supports. After a report completes — outside the mode timeout — thin cited
  URLs are fetched (≤`AUDIT_FETCH_MAX_URLS`, one `AUDIT_FETCH_TIMEOUT_SEC`
  budget, concurrency `MAX_PARALLEL_FETCH`, every URL through the same
  `validate_url` guard as search results) and the captured-source list is
  enriched in place. Never raises; failures leave the snippet in place.
- **The verifier logs why claims fail** (`app/evidence/audit.py`): one
  census line per run — verified count plus failures by class
  (overlap/numeric/no_text/polarity/other) — so the remaining failure mass is
  visible instead of inferred.
- **The runtime facade must mirror the runner's route-facing methods**
  (`app/runtime/orchestrator.py`). The capture pass was added to
  `ResearchRunner` but routes call the `PipelineRunner` facade — every
  completed live run died with `AttributeError` at the final frame until the
  facade delegated the method. A regression test now pins delegation.
- **A live run streams the same claims its replay shows**
  (`app/api/routes.py`, `frontend/src/App.jsx`). The panel's "Total claims" /
  "Verified claims" read `run.findings`, which only the REPLAY path ever
  populated (`replayPanelRun` rebuilds it from the persisted `claims` rows) —
  the `findings` NDJSON emission was lost in the first-party consolidation
  and the live handler had no case for the frame, silently dropped by
  `applyEvent`'s `default: break`. The same run read 0 claims live and real
  numbers on reopen. Both stream paths now emit one end-of-run `findings`
  frame, projected to the frontend `Finding` shape (internal verification
  checks stay server-side, `challenged` derived with the same normalized
  match the DB flagging uses), and the live case assigns it. The resume path
  also flags contradictions now — it previously never called
  `mark_challenged_claims`, so a resumed run persisted unmarked claims.
  Verified live: run `0e84a07c` streamed 120 items / 61 verified, identical
  to its `claims` rows and its replay.
- **Fast mode answers in one pass, with no figures**
  (`app/research/runner.py`, `app/agents/report.py`,
  `app/runtime/event_adapter.py`, `frontend/src/components/IntelligencePanel.jsx`).
  Quick-mode runs were taking 132-175s. Measured stage spans showed why: a
  fact-check objection bought a FULL second round (narrative_writer re-drafted
  at 17-20s, evidence_auditor re-audited at 7-11s), and the run then arrived at
  the diagram with no budget left, so the ladder shed `second_audit_pass` and
  `diagram` — the "Degraded run" banner was a SYMPTOM of the second round, not
  a separate problem. Now: `max_fact_check_revisions` is 0 for quick (one pass;
  the standing objection is still reported because the notes stay on state),
  and DiagramDesigner returns before its model call in quick mode.
  Because quick mode has no diagram *by design*, skipping it is no longer
  recorded as a degradation — the banner no longer claims "the model response
  for ladder:diagram was unavailable", which was false (no call was attempted).
  The stage line says "Skipping figures in fast mode" instead of announcing
  visuals it will not build, and the rail drops the Diagram Designer row for
  quick runs (a permanently Queued stage misdescribes the pipeline).
  Verified live: 104s and 147s on two queries, `degraded: []`, one narrative
  and one auditor pass each; the spread is retrieval latency (scout_swarm
  38-68s), not structure.
- **The mode picker states the expected time, not the enforced cap**
  (`frontend/src/lib.js`, `frontend/src/components/Landing.jsx`). Both
  surfaces quoted `MODE_HARD_CAP_SECONDS` (~3 / 6 / 12 min) as if it were how
  long a run takes. Those caps exist to stop a stalled provider; measured p50
  was ~2.2 / 3.4 / 3.9 min, so every mode looked 2-3x slower than it is. The
  hints now read ~2 / ~3.5 / ~5 min. `mode-claims.test.js` no longer asserts
  "hint == cap" (that was the defect); it asserts the stated time is BELOW the
  cap and ABOVE a measured floor, so the copy cannot drift into either
  overstatement. The landing Quick copy also stops claiming a revision loop —
  fast mode has none now.
- **The report is stamped with the time, not just the date**
  (`app/agents/architect.py`, `app/agents/report.py`). The title line read
  `*17 October 2026*`, which cannot tell a reader whether the report predates a
  same-day development. It now reads `*17 October 2026, 18:25 +06*` — the run's
  own clock (never the model's guess), local zone, month named so it stays
  unambiguous across locales.

Live smoke (cold, quick mode): confidence 0.714 with every signal measured —
source_quality 0.808, coverage 0.40, agreement 1.0, verification 0.344,
critic_survival 1.0 (the structured check returned a clean verdict and the
run no longer pays a pointless revision).

Live A/B on the same query (warm-cache, so content-level, not latency-level):
before 0.598 (coverage 0.242, verification 0.229) -> after 0.700 (coverage
0.442, verification 0.368) with the capture bound at its shipped default
(32 URLs / 20s wall clock). Census: 53/120 verified, failures overlap=45,
numeric=22, no_text=0 — the missing-text class the capture targets is gone;
what remains is substantive mismatch the verifier exists to catch. Capture:
30 cited URLs, 23 fetched, 4 failed, 0 over budget, ~9s after the run's
timeout scope closed.

- **The degraded cap is wired again, and explains itself**
  (`backend/app/evidence/audit.py`, `backend/app/research/runner.py`,
  `backend/app/runtime/event_adapter.py`, `backend/app/api/routes.py`,
  `backend/tests/test_audit_bridge.py`,
  `frontend/src/App.jsx`, `frontend/src/components/IntelligencePanel.jsx`).
  The `confidence-v3` entry above claims "degraded runs still cap at 0.55",
  but that promise was dead in production: `compute_audit` accepted a
  `degraded` list and never forwarded it to `compute_confidence`, so a run
  whose synthesizer fell back to deterministic extraction scored on its
  extractive claims — which trivially self-verify — and could read "High".
  The same omission silently disabled the provider-outage cap. The state now
  reaches the engine (`degraded` plus an explicit `provider_degraded` flag),
  defaulting to the request-scoped degradation context when a caller omits
  it, with regression tests covering each cap path (healthy strong-summarizer,
  synthesizer fallback, provider outage, ambient fallback record). A new
  `confidence_notes` field carries the reasons — including a cap and why it
  applied — on the `final_report` frame and in the Research health panel, so
  a capped score is never just a number.
- **Session rows are actionable again: the "…" is back on the rail**
  (`frontend/src/components/Sidebar.jsx`, `frontend/src/layout.css`,
  `frontend/src/App.jsx`, `frontend/src/lib.js`,
  `frontend/test/session-row-menu.test.js`). Flattening the rail (commit
  `0347fb0d`) removed the per-row menu on the reasoning that session management
  belongs on the session header. That left the rail as a list you could read but
  not act on. The "…" is restored on every row with the same three actions and
  the same handlers — Pin, Rename (inline, in the row's own box), Delete — and
  the header keeps its own menu, because a pinned row is not always the open
  session. The button appears on hover/focus/open so the rail stays calm, and it
  is a SIBLING of the row button rather than nested inside it: the row must keep
  one hit target and one accessible name, and a control nested in a button is
  invalid HTML and unreachable by keyboard.
- **Two real bugs surfaced the moment that menu was usable** — both found by
  driving the real app, not by reading it. Neither is new code; both were
  sitting behind a UI that had been removed:
  - **The header menu's actions were no-ops.** `PageTitle` passed
    `mission.runId` to `toggleMissionPin` / `deleteMission` / `onRename`, which
    key on `sessionId`. A mission's `runId` and `sessionId` are different values
    by construction (one session holds many runs), so all three matched nothing.
    Now keyed on `sessionId`.
  - **Any mutation on a backend-synced session deleted every session.** The
    `/api/sessions` sync called `setMissions` directly, so those missions lived
    in React state and never in localStorage — while `updateMission` /
    `removeMission` / `upsertMission` read and wrote localStorage. Renaming,
    pinning or deleting one of them read an EMPTY cache and wrote it straight
    back: pinning one of 30 sessions emptied the rail and left 0 of 30 in the
    cache. Fixed with a single effect that persists whatever list is visible
    (`persistMissions` in `lib.js`, sliced to the 30-session cap), so no write
    path can leave the cache behind the UI again. Verified live on the real
    30-session list: Pin keeps 30 and flips the row to "Unpin", Rename stores
    the new title, Delete removes exactly one, and all of it survives a reload.
    Note `Delete` remains client-side only — a reload re-syncs the session from
    the backend, since removing it for real needs a backend endpoint.
- **The docs page rendered blank, and every check said it was fine**
  (`frontend/public/docs.content.js`, `frontend/test/docs-accuracy.test.js`).
  The built-in docs are served verbatim from `public/` into an iframe, so the
  Vite build never parses them, and every test that referenced them matched them
  as TEXT. A missing comma between two array entries in the verification table
  glued the two strings together; a truncated latency edit had eaten the closing
  quote off three benchmark rows. The payload failed to parse, so
  `window.DEEPSCOUT_DOCS` stayed undefined, `DocsView` threw
  `Cannot read properties of undefined (reading 'id')`, and the page was blank —
  with the build green and all tests green, because nothing had ever *run* the
  file. Both syntax faults are fixed, along with a stale duplicate paragraph
  that had been left behind by the same line-based edit (it claimed a "stale
  count" and "unreachable sources" in the verifier output — neither exists; the
  census is a log line of failure classes). The docs are now compiled and
  **executed** in a sandbox by the suite, then asserted structurally: every nav
  entry resolves to a non-empty page, and no HTML string ends mid-tag. Verified
  by reintroducing the missing comma — the guard failed, then passed on restore.
  Root cause worth keeping: a file that no build step and no test ever
  *executed* is a file whose correctness is unverified no matter how many
  assertions mention it.
- **`MAX_PARALLEL_LLM` was documented as 2 in one place and 4 in another**
  (`frontend/public/docs.content.js`, `AGENTS.md`). The real default is 4 (2 was
  raised once the free-tier key was no longer the only path); the hero and
  CONFIG.md were right, the Reliability section and AGENTS.md were stale. Both
  now say 4 and explain why it was raised.
- **The console preview on the landing page shows a sidebar that no longer
  exists** (`frontend/src/components/Landing.jsx`, `frontend/src/components.css`,
  `frontend/test/landing-hero.test.js`). The hero draws a picture of the console
  and the whole point is that it is the product's actual shape — but it had
  drifted after the rail was redesigned, and markup cannot disagree with the
  product at runtime, so nothing failed. The preview still drew a `Research 30`
  count badge, a `Recent` / `+24 more` section head, and two-line session rows
  with the mode beneath each title; the real rail has been a flat single-line
  list with a status dot, no count and no head. It also drew a `‹` chevron as the
  panel collapse control (the real console collapses the run-insights panel to
  an `IconPanel` button pinned top-right) and stamped the report
  `Date: 17/10/2026` (the report's own markdown line is
  `*17 October 2026, 18:25 +06*`, written by the run itself). All four now
  mirror the real components. The preview was already accurate about the trace
  bar ("Less steps" / "Worked for"), and that is now asserted too.
  Five new tests read the preview against `Sidebar.jsx`, `App.jsx` and
  `trace/AgentPipelineTrace.tsx` — including the backend's own date format — so
  the picture and the console cannot diverge again. Verified by reintroducing
  both drift shapes: the guards failed, then passed again on restore.
- **Documentation describes the system that actually ships**
  (`README.md`, `ARCHITECTURE.md`, `CONFIG.md`, `SETUP.md`,
  `DeepScout-vision-v2.md`, `frontend/public/docs.content.js`,
  `frontend/src/components/Landing.jsx`,
  `frontend/test/docs-accuracy.test.js`, new). A documentation audit found
  the published docs describing subsystems that do not exist (an intent
  classifier, live citation re-validation), understating what the run
  actually does, and quoting stale numbers. Corrected: the event tables now
  list `findings` (which is emitted) and stop denying it; the resume docs
  describe per-node checkpointing instead of claiming no checkpointing; the
  degraded-cap wording distinguishes the unconditional synthesizer cap from
  the evidence-conditional summarizer one; `CONFIG.md` drops a deleted
  setting, adds `AUDIT_FETCH_*`, and explains that `.env.example` wins over
  class defaults (so a stock clone runs 25 s / 90 s / fallback-on); mode
  tables quote the enforced ceilings and quick's zero fact-check round;
  counts match the suites (672 backend / 340 frontend); the landing page's
  GitHub link points at the real repository; and the vision doc is labelled
  the design record it is, so its conceptual role names stop reading as
  current code. `docs-accuracy.test.js` reads the backend source of truth and
  fails when the prose and the code drift — the stale count and the
  un-emitted `findings` frame were both caught by it on first run.

672 backend and 340 frontend tests pass; ruff clean.

## Unreleased — First-party research pipeline

The multi-agent research engine that previously drove every request was
replaced by first-party code, and the code it had already displaced was removed.

- **Pipeline re-authored under `app/research/`.** 36 modules covering the run
  config, prompt families, the generation funnel, BM25 context filtering,
  retrieval, scraping, document loaders, the researcher and its skills, and the
  LangGraph agent team. The pipeline calls `app.core.llm.LLMClient` (generation)
  and `app.agents.search.SearchClient` (retrieval) **directly**, so the Model
  Control Center governs every call with no monkeypatched provider factory and
  no `sys.path` bootstrap. `app/engine/llm_bridge.py` and
  `app/engine/retriever_bridge.py` are gone.
- **External research dependency deleted.** The third-party research package
  that previously drove runs is gone entirely.
- **Dead first-party code removed.** `app/agents/orchestrator.py`,
  `app/agents/confidence.py`, `app/agents/planner.py`, `app/agents/planning/`,
  `app/core/schemas.py`, and the inert epistemic-adjustment branch in
  `app/core/confidence.py` — all unreachable since the engine landed.
- **Retrieval unified.** Every retriever name resolves to
  `app/research/retrieval/base.py::SearchClientRetriever`.
- **34 dead settings deleted** from `app/core/config.py` (and the matching
  `.env.example` keys): the depth controller, intent classifier, query router,
  answer quality gate, citation checker and the whole `SYNTHESIS_*` block.
  107 settings -> 73. `CONFIG.md` was rewritten against the live config so it
  cannot drift again.
- **Report quality.** Sections are researched in bounded waves, and each wave's
  writers receive the sections already written, which is what the
  anti-duplication prompt instructions were always written against. Duplicate
  Introduction/Conclusion headings are collapsed; a source-relevance gate drops
  pages that engage nothing in the report's subject; references must name a
  specific publication and "Author not specified" filler is stripped; the
  report is dated with the actual run date; mermaid diagrams render as figures
  in the UI.
- **Docs brought back in line with the code.** README, ARCHITECTURE, CONFIG,
  SETUP, AGENTS and this file now describe the shipped system: no cost-ceiling
  claims, correct resume semantics, the five stream events actually emitted, the
  full endpoint list, and real defaults throughout.

577 backend tests pass; ruff clean.

## Unreleased — Backend consolidation

Continuing the reduction of the backend surface, without changing behaviour.

- **Facade shims collapsed.** The synthesis/quality/depth/epistemics layers
  each carried a module that only re-exported the package's names. Every import
  now points at the real package and the shims are gone:
  `agents/epistemics.py` → `agents/epistemic.*`, `agents/research_quality.py` →
  `agents/quality.*`, `core/depth_controller.py` → `core/depth.*`,
  `core/synthesis_intelligence.py` → `core/synthesis_intel.*`. Orphaned
  `agents/schemas_ext.py` and never-called
  `synthesis/sections.py::_merge_into_section` deleted.
- **Contradiction resolution folded in.** `core/contradiction_resolution.py` is
  merged into `core/contradictions.py` (detection + resolution now live
  together; the resolution pass no longer imports four private helpers across a
  module boundary). Public names `resolve_contradiction` and
  `resolve_contradictions` are re-exported from the merged module, so imports
  are unchanged in effect.

> Note: most modules named above were subsequently removed by the
> first-party-pipeline consolidation (the facade targets `core/depth.*`,
> `core/synthesis_intel.*` and `agents/epistemic.*` no longer exist). This
> entry is kept as a record of work done at the time.

## Unreleased — Question-shaped answers

Synthesis no longer follows a fixed report template. The question and the
evidence in hand decide the structure, length and depth; the evidence decides
the conclusion. This builds on the `ReportProfile` / `AnswerBlueprint` layers
and closes the last fixed-template path the live run exposed.

**What changed:**

- **A `recommendation` query family.** "Suggest some research topics", "what
  should I study", "recommend projects" are recognised as requests for
  actionable options, not surveys. `infer_query_type`/`_format_guidance`
  (`synthesis/profiles.py`) and `_blueprint_key` (`outline.py`) share the shape,
  which asks for a short ranked set of concrete options, each with its method
  and a feasibility note (skills, data, compute, supervision). The live defect —
  a definitional survey that answered none of the suggestions — is fixed at the
  planner, router, blueprint and writer-prompt layers.
- **Conformance enforces the recommendation shape.** `answer_conformance` now
  fails a suggestion request that ships no concrete, actionable options, the
  same way it already fails a comparison without a verdict or a "why" without a
  mechanism.
- **Guidance requests are not "ambiguous".** A multi-reading term inside a
  request for suggestions no longer triggers the disambiguation opener, which
  buried the answer under a "which meaning did you mean" block.

**Preserved unchanged:** citations and their verification, evidence safeguards
(definition lock, ranking basis, convergence, temporal/independence contracts),
interpretation locks, degraded-run handling and capping, and the LLM JSON
schema (`{"answer": "..."}`).

## Unreleased — Red team removed

The standalone **Red Team** agent (`app/agents/redteam.py`) has been deleted, along
with the `redteam` research mode and every trace of the concept in the API, graph
state, synthesis layer, and UI. Historical runs keep whatever they already
persisted; nothing was rewritten or purged.

**Behaviour changes to be aware of:**

- **Confidence no longer prefers a "survival" score.** `compute_confidence` took
  `redteam_survival` in preference to `critic_confidence`. It now uses
  `critic_confidence` — the fallback that was already implemented and, in
  production, was never reached.
- **The critic gate lost a blocking condition.** A survival score below 0.45
  previously forced `is_sufficient=False`, which guaranteed another research pass
  and more LLM spend. That loop is gone; sufficiency is now gated on evidence and
  critic signals alone.
- **`synthesis._objection_blocks` was deleted as dead code.** It read
  `redteam_findings` and `what_would_change_our_mind`, and the second was never
  populated by anything, so it always returned an empty list.
- **The `redteam` mode is gone from the composer.** Contested medium-complexity
  queries now route to `deep`, which is where they already routed above medium.

**What was deliberately kept:** the adversarial self-check inside the critic
prompt (weakest assumption / invalidating alternative / absent counter-evidence).
It was reframed from "RED TEAM QUESTIONS" to "ADVERSARIAL SELF-CHECK" rather than
deleted — removing working critique would have made reports weaker, which is the
opposite of the intent. `answer_quality` now scores objections on the report's own
wording rather than on the presence of red-team findings.

## v2.4 — Question-Driven Coverage & Research (Phase 10)

Measured finding: DeepScout could rank evidence by relevance (v2.3 §1), but a broad
question was still researched and answered through whatever the first retrieval
returned — required dimensions the search never covered simply vanished from the
plan, so partial coverage was presented as the whole landscape.

### Question-driven coverage (`core/synthesis_planner.py`)

- `build_synthesis_plan(required_dimensions=...)`: the plan now plans against the
  QUESTION's required dimensions (the research contracts' own dimension labels),
  not only the axes the retrieved facts happened to fill.
- `required_dimensions_from_plan()`: derives that set deterministically from the
  research contracts, so no new LLM call is spent.
- `SynthesisPlan.uncovered`: a required dimension with no evidence is now named
  (and rendered to the writer as "REQUIRED DIMENSIONS WITH NO EVIDENCE") instead
  of disappearing. Distinct from `under_researched` (thin-but-present).
- `SynthesisPlan.required_total` / `covered_total` / `coverage_ratio`: an explicit
  coverage summary. Partial coverage adds a writer caution that the evidence-
  backed findings are what the research established, not the complete landscape.
- `PlannedDimension.required`: marks dimensions the question demanded.
- Themes are now ordered by the centrality of the evidence behind each section,
  so a peripheral axis with facts (unrelated history/Wikipedia material) cannot
  become a headline theme merely because it was retrieved.

### Coverage-aware follow-up (`core/investigation_planner.py`)

- The existing `dimension_coverage` channel now also targets `uncovered`
  required dimensions, ranked ABOVE merely thin ones (`GAP_UNCOVERED_DIMENSION =
  1.2` vs `GAP_UNDER_RESEARCHED_DIMENSION = 0.9`). Reuses the same candidate kind
  and the existing per-pass budget; no new detector, no new LLM call, no new
  channel.

### Forecasting questions (`core/temporal.py`, `agents/synthesizer.py`, `agents/outline.py`)

- `query_targets_future()`: clock-relative detection of a future year in the
  question, so "the most demanding jobs in 2027" is a forecast even without the
  verb "will". A historical year never triggers the forecast shape.
- The forecast writer guidance now requires four strictly separated statement
  kinds — OFFICIAL PROJECTIONS, CURRENT INDICATORS, CROSS-SOURCE INFERENCE,
  UNKNOWNS — and forbids inventing a probability, rank, percentage or date the
  evidence does not contain.

### Coverage-aware AnalystBrief (`agents/analyst.py`)

- The analyst prompt now receives coverage (required vs covered), the uncovered
  dimensions, the intended interpretation and under-specified readings — not
  just a ranked fact list — so it reasons over the whole landscape.

### Modes (unchanged by design)

- Deep/executive already raise breadth, source requirements and iterations;
  quick lowers them. No per-mode answer templates were added.

## v2.3 — LLM Analytical Synthesis (analyst, not report generator)

Measured finding: the deterministic SynthesisPlan (v2.2) organized the evidence
but did not synthesize it — it had no thesis field, restated convergent claims
as "conclusions", and on a broad query ("current trends in AI") narrowed the
central question onto the first sub-dimension. An LLM stage between plan and
writer was warranted.

### New stage: `agents/analyst.py`

- `analytical_synthesis()`: one small LLM call over the SAME verified evidence
  + deterministic plan, producing a structured `AnalyticalBrief` — central
  thesis, major insights, relationships, counter-evidence, implications,
  cross-source conclusions, uncertainties. Rendered into the writer prompt
  after the plan; no chain-of-thought ever leaves the stage.
- **No new facts**: every statement cites evidence numbers; a deterministic
  guard drops any statement carrying a number absent from the evidence pool
  (citation markers stripped first, so `[1]` is never read as a figure).
- **Verification unchanged**: the writer still cites `[n]` against verified
  facts and `verify_answer_support` still runs. This stage only shapes what the
  writer reasons from.
- **Fallback (AGENTS.md 4.7)**: LLM failure / empty output / disabled
  (`synthesis_analyst_enabled`) degrades to a brief built from the
  deterministic plan (established → dominant → conclusions), never empty when
  the plan is non-empty, never a crash.

### Deterministic plan fix

- `central_question` is now the USER'S QUERY, not the first planned dimension.
  Using the first sub-question narrowed a broad question onto one angle — the
  exact collapse the blueprint exists to prevent.

## v2.2 — Evidence Synthesis Planning

Reasoning over the evidence landscape BEFORE writing, not just better prose.

### New stage: `core/synthesis_planner.py` (deterministic, LLM-free)

- Builds a single explicit `SynthesisPlan` answering the twelve planning
  questions (what is asked, needed dimensions, dominant vs incidental
  findings, conflicts, under-researched dimensions, established/inferred,
  prioritisation, what to omit, structure) from artifacts the pipeline
  already computes — no new detection logic, no new LLM call.
- **Dominant vs incidental** finding ranking by centrality (reuses
  `evidence_completion.claim_impact` plus the grade weight). A finding clears
  the dominant bar when it is quantitative, summary-used or A-grade
  corroborated; a pool with nothing above the bar still gets its single best
  finding as the lead.
- **Under-researched dimensions**: a dimension whose most central finding is
  important but which carries fewer than two findings is named — a
  coverage-vs-importance judgement the existing channels did not make.
- Renders into the writer prompt (`_render_context_block`) alongside the
  reasoning structure; wired in `workflow.synthesizer_node`.

### Synthesis-plan-driven follow-up research

- New allocator channel `KIND_DIMENSION_COVERAGE` in
  `core/investigation_planner.py`: the under-researched dimensions the plan
  names become targeted searches using the dimension's own question text.
  Every other channel fires on an evidence *deficiency*; this is the first
  that fires on a synthesis-level judgement.

### Under-specified (non-homonymous) questions

- `IntentReport.interpretations`: a curated detector for terms with two
  materially useful readings ("most demanding jobs" = high-demand OR
  high-strain). The writer is told to answer both briefly, never to spend the
  answer explaining that the term is ambiguous. Emitted on the `intent`
  event.

### Cross-source synthesis is attributed, not rejected

- `verify_answer_support` classified a genuine synthesis sentence ("taken
  together, these findings indicate…") as *unsupported* because it matched no
  single cited source verbatim. A sentence citing two or more distinct
  verified sources is now `synthesis` — counted separately, never as
  supported fact and never as contamination. Fabricated numbers in such a
  sentence still fail as `numeric_failure`. `citation_check` counts synthesis
  sentences toward the URL's support.

## v2.1 — Intent & Answer-Quality layer


The upgrade against the "What is transformer?" failure class: the pipeline
used to treat the raw query as a search string, mix electrical-transformer
statistics into ML answers, and ship whatever the synthesizer produced.
Understand-before-searching and a pre-delivery quality gate are now
architectural stages, not prompt hopes.

### New pipeline stage: Intent Classification (`agents/intent.py`)

- START → intent → planner: one small, cache-friendly LLM call resolves the
  question BEFORE research is shaped — ambiguity into ranked senses with
  probabilities, the research domain, and the explanation level
  (basic/practical/expert).
- Deterministic fallback (same contract as every agent): curated homonym
  hints + the orchestrator's lexical classifiers, including self-resolution
  when the user already disambiguated ("python snake feeding habits").
- DeepScout never blocks on a clarifying question: `recommended_action` decides
  whether research targets the dominant sense or structures both, and the
  answer itself opens with the disambiguation.
- The grounding search on the raw query moved into the intent node and is
  shared with the planner (it was the mechanism pulling wrong-sense pages
  into the plan).
- Planner: intent overrides the model's domain classification; ambiguous
  plans tag every contract with a `sense` label; `engineering` added to the
  domain vocabulary (technical specialist).
- Summarizer: sense constraint discards other-sense claims at extraction
  time; facts carry `sense` end to end; cache keyed by sense.
- Synthesizer: ambiguous reports open with a numbered disambiguation
  (`1) **Sense** — explanation`), keep each sense's evidence in its own
  sections, and the citation audit exempts the disambiguation lines; basic
  level forces a plain-language explanation with an analogy.
- New `intent` NDJSON event (+ frontend case) and an Intent row in the
  intelligence panel.

### Provider resilience: LLM-written answers on flaky free tiers

- **Payload ladders**: Groq rejects requests over ~21-41KB (HTTP 413, measured
  live) — exactly where the summarizer prompt lands. Both writer stages now
  shrink their evidence view (22k→12k→6k excerpt budgets; 40→24→14 fact caps)
  and stay LLM-written instead of degrading to extraction. Rate-limit walls
  (429/TPM exhaustion) shrink too; timeouts never do (slowness isn't a size
  signal — shrink-retrying a slow provider multiplies 90s stalls).
- **Permanent-400 fail-fast**: a provider with no credits 400s every call;
  400s with balance/credit/key/model signatures and 404s fail fast instead of
  burning 4 retries per LLM call.
- **Patient Retry-After**: the first retry of a call honors the provider's
  own wait hint up to 20s (one patient wait clears a rolling TPM window);
  later retries cap at 3s. Body-style hints ('try again in X.XXs') parsed.
- **ACTIVE_PROVIDER_FALLBACK** (default false): a FAILING UI-selected active
  provider falls through to the env chain instead of degrading the run —
  built for flaky free proxies; strict exclusivity stays available.

### New pre-delivery stage: Answer Quality Optimizer (`agents/answer_quality.py`)

- Every synthesized answer is scored 0-100 on Accuracy / Relevance /
  Evidence / Clarity / Reasoning from measured pipeline state only (support
  verdicts, verification flags, source registry, citation health) — no LLM.
- Hard floors bind: an ambiguous-query report without the mandatory
  disambiguation fails relevance regardless of its other scores; data-dump
  bullets, missing limitations, unsurfaced conflicts and off-band length
  feed actionable failure strings.
- A failing draft gets exactly ONE re-synthesis with the failures fed back
  (never a loop; skipped when the synthesizer itself is degraded); the
  better draft ships and the scores are disclosed in the report's
  `# Answer Quality` section and the `final_report` event.
- Settings: `INTENT_ENABLED`, `QUALITY_GATE_ENABLED`, `QUALITY_THRESHOLD`.

## v2.0 — Production upgrade

The upgrade from the v1 vision implementation: every dead-ended component
wired live, four new subsystems, and a benchmark suite that found (and
fixed) six real correctness bugs before shipping.

### Wired: previously-built-but-inert components

- **Budget governor** (`agents/budget.py`) — was complete but had zero call
  sites. Now: every LLM call records provider-reported token usage into a
  per-run ledger; searches record provider units; the depth controller
  consults `can_afford_pass` before every expansion; runs persist real
  `estimated_cost` (was hardcoded `None`).
- **Dependency waves** (planner's `execution_waves`) — was computed and
  discarded. Now: the summarizer executes wave-by-wave, and dependent
  contracts receive earlier-wave findings as bounded grounding context.
- **Feature-11 stopping** (`agents/stopping.py` marginal-gain logic) —
  merged into the live depth controller: no-novel-query memory (never
  re-runs a search the run already issued), min-iterations per mode,
  mode confidence targets (quick 0.60 … audit 0.85).
- **Confidence v3 signals** — contradictions (pool-size-scaled penalty),
  citation support (10% weight carved from citation coverage) and axis
  coverage (5% from source diversity) now feed the live engine.

### New subsystems

- **Semantic engine** (`core/semantic.py`): TF-IDF hybrid with stemming,
  synonym canonicalization, negation weighting, and fully vectorized batch
  scoring (jaccard/rare-anchor/number gates via presence matmuls).
  Replaces per-pair SequenceMatcher loops: 40×200 batch scoring 865ms→3ms,
  300-fact matrix 5s→0.5s.
- **LLM response cache** (`core/llm_cache.py`): exact-prompt disk cache;
  repeated critic re-evals and re-runs served in ~1ms with the dollar cost
  refunded while token accounting stays visible.
- **Run usage ledger** (`core/usage.py`): ContextVar ledger bridging the
  app-scoped LLM client to per-run budget accounting; streams live to the
  UI on every critic event.
- **Citation health check** (`agents/citation_check.py`): live URL
  re-validation of the final answer's legend (HEAD → 2KB ranged GET,
  bounded concurrency/timeout/count, never fatal) fused with sentence
  support into ok/warn/broken/bad verdicts surfaced in the report, the
  API events and the UI.

### Upgraded components

- **Contradiction engine v2**: numeric (now unit-aware — "%" vs "GW" is no
  longer a conflict, years excluded), polarity (state-assertion vocabulary,
  detected outside the similarity band), temporal (period mismatch),
  severity ranking, additive `kind`/`severity`/`values` fields with legacy
  keys preserved.
- **Answer support**: batched cross-similarity scoring; per-sentence
  `sentence_details` (status + support score) for the UI and benchmarks.
- **Deduplication**: matrix-based greedy merge; stopword-stripped dup floor
  for function-word variants with identical quantities; polarity guard so a
  claim can never corroborate its own negation.
- **Confidence engine v2**: see above; degraded-run cap retained.
- **Provider chain**: usage parsed from responses (`CompletionResult`),
  pre-flight probe results cached 30s on success (failures always re-probe).
- **Persistence**: schema auto-initialization on first use; `file:` /
  `sqlite://` DATABASE_URL forms; parent directories created;
  `save_report` wrapped so persistence can never kill a completed run
  (both were fresh-install failure modes).
- **API events**: `plan` carries `waves`; `critic` and `final_report`
  carry `budget`; `final_report` carries `wave_report` and
  `citation_health`.

### Frontend

- Intelligence panel: **Cost & budget** meter (calls, tokens, USD,
  utilization bar, cache hit rate), **Execution waves** strip,
  **Citation health** row, answer-support health row, and the two new
  confidence signal labels.
- Run state and missions persist real cost.

### Benchmark-driven bug fixes

Found by `bench/run_offline.py` on its first run, fixed, and re-measured:

1. `claim_polarity` counted bare "up"/"down" as direction tokens — the "up"
   inside "follow-up" canceled "reduction" and a polarity-inverted claim
   verified as true (verification F1 0.94 → 1.00).
2. "approved"/"banned"-class state assertions were absent from the polarity
   vocabulary — "approved" vs "not approved" was undetectable.
3. Dedup merged "X" with "not X" and counted the negating source as
   corroboration — now blocked by a polarity guard.
4. Antonym-swap pairs ("contracted" vs "expanded") fell below the
   similarity-band floor; polarity conflicts now need only a 0.45 topical
   floor (contradiction F1 0.80 → 1.00).
5. Contradiction penalty was pool-size-blind — one severe conflict among 3
   facts now costs up to 2x its per-conflict penalty (calibration 75% →
   100% in band).
6. Function-word variants ("declined 14% in 2023" vs "declined by 14%
   during 2023") escaped the 0.86 dedup merge — the stopword-stripped
   dup floor restores them while a quantity guard keeps "grew 25%" and
   "grew 11%" unmergeable.

### Tests

343 tests (was 282), all offline: +15 semantic engine, +9 depth controller
v2, +6 LLM cache/usage, +3 wave execution, +8 contradiction v2, +9 citation
health, +10 confidence v2, +5 sqlite robustness. Autouse fixtures isolate
the LLM cache and citation URL checks so no test ever hits the network.

### Benchmarks

See `BENCHMARK_RESULTS.md`. Headlines: verification F1 1.00, hallucination
leak 0%, citation support 100%, contradiction F1 1.00 (3 kinds), confidence
calibration 100% in-band and monotonic, dedup precision 1.00, LLM cache
6.7x repeat-pass speedup.

### Housekeeping

- `requirements.txt`: numpy added explicitly (the semantic engine is the
  first direct numpy use).
- `.env.example` documents the new knobs (budget note updated from
  "enforced once ported" — it is enforced now; LLM cache; citation check).
- Docs set added: `SETUP.md`, `CONFIG.md`, `ARCHITECTURE.md`,
  `BENCHMARK_RESULTS.md`, this changelog.
