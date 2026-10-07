"""Graph state and per-node return types.

Split out of `workflow.py`, which held these alongside nine helper clusters,
four report builders and the graph assembly. They are pure declarations with no
logic, so moving them cannot change behaviour -- but they are the vocabulary
every node is typed against, so they need a home that does not import the
graph.
"""
from __future__ import annotations

from typing import Any, Dict, List, TypedDict


class ResearchState(TypedDict, total=False):
    query: str
    sub_questions: List[Any]
    search_results: List[Dict[str, str]]
    facts: List[Dict[str, Any]]
    critique: Dict[str, Any]
    critique_feedback: str
    iteration: int
    max_iterations: int
    final_report: str
    # Audit/trace layer (evidence ledger, contradictions, decisions, quality,
    # confidence, provenance) rendered separately from the primary answer so the
    # answer stays free of pipeline mechanics. Empty for direct/conversation
    # answers, which have no researched evidence to audit.
    final_audit: str
    synthesized_answer: str
    confidence: float
    orchestration: Dict[str, Any]
    deep_research: bool
    verification_stats: Dict[str, Any]
    answer_support: Dict[str, Any]
    confidence_breakdown: Dict[str, Any]
    confidence_history: List[float]
    # True when the degraded-extraction cap (not an evidence deficit) is what
    # holds confidence below target. Read by the depth controller so a run whose
    # evidence could not be MEASURED stops expanding instead of re-extracting
    # the same sources until the iteration ceiling.
    confidence_degraded_capped: bool
    contradictions: List[Dict[str, Any]]
    mode: str
    decision_options: List[Dict[str, Any]]
    redteam: Dict[str, Any]
    # Wave execution (Feature 03): plan shape + per-pass wave results.
    execution_waves: List[List[str]]
    wave_report: List[Dict[str, Any]]
    # Citation validation v2: live URL health of the emitted answer's legend.
    citation_health: Dict[str, Any]
    # Intent classification (understand-before-searching): ambiguity, senses,
    # domain and explanation level, resolved before planning. Plus the raw
    # grounding search snippets, shared by intent and the planner.
    intent: Dict[str, Any]
    context_snippets: List[str]
    # Ambiguity policy (app/agents/ambiguity.py): decided after intent and BEFORE
    # planning. Carries the action (proceed/assume/ask/separate), the plausible
    # readings, and — for `ask` — the clarification question. An `ask` stops the
    # run instead of researching every reading; `assume`/`separate` shape the
    # answer. Process metadata: it reaches the audit and the wire, never the
    # primary answer text.
    ambiguity: Dict[str, Any]
    # Query router (R2): the direct-vs-research decision made after intent,
    # before planning. R2 only SURFACES it (route event + trace); R3 branches
    # the graph on it (route_after_intent) and the direct path records its
    # answer below.
    route: Dict[str, Any]
    # Direct-answer path (R3): the delivered answer and the agent's refusal
    # verdict. `direct_answer` is empty when the graph took the research
    # branch, or when the agent refused and fell through to research.
    direct_answer: str
    direct_answer_meta: Dict[str, Any]
    # Answer quality gate: five-axis 0-100 score of the delivered report.
    quality: Dict[str, Any]
    # Thesis fidelity (Phase 8): how faithfully the delivered prose reflects
    # the analyst's brief. Declared so LangGraph carries it to the audit layer.
    thesis_fidelity: Dict[str, Any]
    # Answer conformance (Phase 12): whether the delivered prose fits the
    # question's shape, calibrates inference, synthesises its conflicts and has
    # depth proportional to the question. Carried to the audit layer.
    answer_conformance: Dict[str, Any]
    # Answer-first outline (GPT Researcher adaptation): the section shape the
    # writer targeted (broad flag + section list), and whether section-wise
    # synthesis was used. Declared on state so LangGraph carries them to the
    # route for streaming.
    outline: Dict[str, Any]
    section_wise: bool
    # Evidence grades (Step 5): A/B/C/D counts over the verified fact pool.
    evidence_distribution: Dict[str, int]
    # Research-loop: whether a counter-evidence query has actually been issued.
    counter_evidence_attempted: bool
    # Fix A: claim-specific queries that seek a NEW publisher for uncorroborated
    # claims. Computed in critic_node, executed directly in search_node (they
    # do not depend on the planner model rephrasing them).
    corroboration_queries: List[str]
    # Corroboration ACQUISITION: per-claim attempt state (normalized claim ->
    # {claim, domains_queried, query_keys, attempts}), threaded through state so
    # no module-level mutable registry is needed. A claim stops being re-queried
    # once its attempt budget is spent (it stays in the gap set as a limitation).
    corroboration_registry: Dict[str, Dict[str, Any]]
    # Per-claim INVESTIGATION state (app/core/investigation_state.py): normalized
    # claim -> {claim, attempts, queries, status, last_outcome}. It is the
    # OUTCOME memory the corroboration registry lacks — did a targeted attempt
    # actually corroborate the claim? — so exhausted single-source gaps are
    # acknowledged as limitations instead of being silently re-chased, and
    # un-attempted gaps are funded before already-attempted ones. Run-scoped via
    # state (no module-level store); see the module for the status vocabulary.
    investigation_state: Dict[str, Dict[str, Any]]
    # Fix B: hard per-run cap on expansion search passes actually issued.
    expansion_passes: int
    # Executed-query memory: the normalized text of every search query this run
    # has actually issued (contract questions, variants, corroboration and
    # primary-source follow-ups). search_node enforces it so an identical query
    # can never be re-issued across passes — a repeat returns the same evidence
    # at full cost. Also mirrored into `coverage_searched`, the key the depth
    # controller's _searched_queries() reader already consults.
    executed_queries: List[str]
    coverage_searched: List[str]
    # Query-anchored focus assessment (app/agents/focus.py): coverage,
    # concentration and drift measured against the ORIGINAL question, plus the
    # redirect queries the next pass should issue. Process metadata — it belongs
    # to the audit, never to the primary answer.
    focus: Dict[str, Any]
    # Fundamental-gap convergence (app/agents/convergence.py): the diagnosis for
    # this pass, plus the run's history of fundamental-gap signatures. The history
    # is what makes REPETITION detectable — the same "no source ranks these"
    # conclusion twice is a property of the question, and the loop must converge
    # rather than reopen for an angle the evidence cannot supply. Run scoped on
    # state (no module-level counter).
    convergence: Dict[str, Any]
    gap_history: List[str]


class PlannerUpdate(TypedDict):
    sub_questions: List[str]
    execution_waves: List[List[str]]


class IntentUpdate(TypedDict):
    intent: Dict[str, Any]
    context_snippets: List[str]
    route: Dict[str, Any]
    # Ambiguity policy decided in the intent node (see ResearchState.ambiguity).
    ambiguity: Dict[str, Any]


class SearchUpdate(TypedDict, total=False):
    search_results: List[Dict[str, str]]
    counter_evidence_attempted: bool
    expansion_passes: int
    facts: List[Dict[str, Any]]
    executed_queries: List[str]
    coverage_searched: List[str]


class SummarizerUpdate(TypedDict):
    facts: List[Dict[str, Any]]
    wave_report: List[Dict[str, Any]]


class VerifierUpdate(TypedDict):
    facts: List[Dict[str, Any]]
    verification_stats: Dict[str, Any]


class CriticUpdate(TypedDict):
    critique: Dict[str, Any]
    iteration: int
    confidence: float
    critique_feedback: str
    confidence_breakdown: Dict[str, Any]
    contradictions: List[Dict[str, Any]]
    redteam: Dict[str, Any]
    facts: List[Dict[str, Any]]
    counter_evidence_attempted: bool
    corroboration_registry: Dict[str, Dict[str, Any]]
    investigation_state: Dict[str, Dict[str, Any]]
    # Query-anchored focus assessment for this pass (app/agents/focus.py).
    focus: Dict[str, Any]
    # Convergence diagnosis for this pass, plus the signature history that makes
    # repetition detectable (see ResearchState.gap_history).
    convergence: Dict[str, Any]
    gap_history: List[str]


class SynthesizerUpdate(TypedDict):
    synthesized_answer: str
    answer_support: Dict[str, Any]
    citation_health: Dict[str, Any]
    quality: Dict[str, Any]
    evidence_distribution: Dict[str, int]
    # Answer-first outline: the section shape the writer targeted (broad flag
    # + section list) and whether the section-wise path was used. Rides in
    # state so the existing final_report event can surface it.
    outline: Dict[str, Any]
    section_wise: bool
    # Thesis fidelity (Phase 8): how faithfully the prose reflects the
    # analyst's brief. Audit-only; never rendered into the primary answer.
    thesis_fidelity: Dict[str, Any]
    # Answer conformance (Phase 12): whether the prose fits the question's
    # shape, calibrates inference, synthesises conflicts and has depth
    # proportional to the question. Audit-only.
    answer_conformance: Dict[str, Any]
    # Machine-owned synthesis provenance (measured evidence accounting,
    # integrity notes) the synthesizer deliberately kept OUT of the answer.
    # Rendered by build_answer_audit, never by the primary answer.
    synthesis_machine_notes: List[str]


class FinalizeUpdate(TypedDict, total=False):
    final_report: str
    # Audit/trace layer, kept structurally separate from the primary answer:
    # evidence ledger, contradictions, decision options, quality, confidence and
    # research provenance. The primary answer must never carry process noise;
    # this is where it lives instead. See build_answer_audit.
    final_audit: str
    decision_options: List[Dict[str, Any]]
