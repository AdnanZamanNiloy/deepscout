"""Evidence-based answer construction: the middle case between answer and refusal.

The pipeline had two reachable outcomes — a source answered the question, or the
convergence diagnosis forced a refusal. The synthcase in between (no source
answers it, but the evidence covers its dimensions well enough to build a
defensible answer) was absorbed by the refusal branch, because
"no source ranks these" was read as "no answer can be given".

These tests pin the classification, the guardrails that stop it degrading into a
guessing machine, and the writer contract. Every threshold asserted here is a
named constant in the module so the test states the rule, not a magic number.

Numbers 1-8 in the docstrings map to the behaviour the layer was specified to
provide.
"""
from app.agents.answer_construction import (
    ANSWER_DIRECT,
    ANSWER_INSUFFICIENT,
    ANSWER_SYNTHESIZED,
    INFERENCE_CROSS_DIMENSION,
    INFERENCE_DIRECT,
    MAX_SINGLE_DOCUMENT_SHARE,
    MIN_SYNTHESIS_DIMENSIONS,
    _plausible_candidate,
    assess_answer_construction,
    classify,
    render_construction_contract,
)

QUERY = "What can be the most demanding job in 2027?"

DIMENSION_AXES = (
    "psychological stress and burnout",
    "workload and working hours",
    "responsibility and consequence",
    "expertise and qualification",
    "physical demands",
)


def fact(claim, source, dimension, *, corroboration=1, verified=True, confidence=0.8):
    return {
        "claim": claim,
        "source": source,
        "sub_question": dimension,
        "verified": verified,
        "corroboration_count": corroboration,
        "confidence": confidence,
    }


def plan(axes=DIMENSION_AXES):
    return [{"axis": a} for a in axes]


def multi_dimensional_pool():
    """Five independent dimensions, each from a different publisher, corroborated."""
    return [
        fact("Emergency medicine physicians report the highest measured burnout rate",
             "https://pubmed.example/a", DIMENSION_AXES[0], corroboration=2),
        fact("Surgical residents average the longest weekly working hours",
             "https://ilo.example/b", DIMENSION_AXES[1], corroboration=2),
        fact("Air traffic controllers carry the highest consequence-of-error load",
             "https://ntsb.example/c", DIMENSION_AXES[2], corroboration=2),
        fact("Psychiatrists hold among the longest qualification pathways",
             "https://oecd.example/d", DIMENSION_AXES[3]),
        fact("Firefighters show the highest physical demand ratings",
             "https://eurofound.example/e", DIMENSION_AXES[4]),
    ]


SHORTLIST = {
    "verdict": "shortlist",
    "candidates": ["Emergency medicine physicians", "Surgical residents"],
}
CONVERGED = {
    "identified": True,
    "reason": "no source ranks demandingness directly",
    "signature": "no source ranks",
    "missing_evidence": "an occupational study ranking roles by demand",
}
LOCK = {
    "term": "demanding job",
    "definition": "the role with the highest combined workload, stress and responsibility",
}


# ---------------------------------------------------------------------------
# 1. direct source -> DIRECT
# ---------------------------------------------------------------------------


def test_a_real_comparison_is_direct_and_may_rank():
    built = classify(
        QUERY,
        facts=[fact("Job A has a higher demand score than Job B",
                    "https://a.example/x", "evidence", corroboration=2)],
        ranking_basis={"verdict": "ranked", "candidates": ["Job A", "Job B"]},
    )
    assert built.mode == ANSWER_DIRECT
    assert built.allowed_ranking is True, "a published comparison is exactly what permits ranking"
    assert built.inference_level == INFERENCE_DIRECT


def test_a_fully_covered_non_ranking_question_is_direct():
    built = classify(
        "How does TCP congestion control work?",
        facts=multi_dimensional_pool(),
        plan=plan(),
        ranking_basis={"verdict": "undetermined"},
    )
    assert built.mode == ANSWER_DIRECT
    assert built.allowed_ranking is False, "non-ranking questions never gain a ranking"


def test_a_definitional_claim_is_direct():
    built = classify(
        "What is retrieval augmented generation?",
        facts=[fact("Retrieval augmented generation is a technique that grounds "
                    "generation in retrieved documents", "https://a.example/x", "definition")],
        plan=plan(["definition"]),
    )
    assert built.mode == ANSWER_DIRECT


# ---------------------------------------------------------------------------
# 2. no direct ranking + strong multi-dimensional evidence -> SYNTHESIZED
# ---------------------------------------------------------------------------


def test_multi_dimensional_evidence_with_no_source_answer_synthesises():
    built = classify(
        QUERY,
        facts=multi_dimensional_pool(),
        plan=plan(),
        ranking_basis=SHORTLIST,
        convergence=CONVERGED,
        definition_lock=LOCK,
    )
    assert built.mode == ANSWER_SYNTHESIZED
    assert built.inference_level == INFERENCE_CROSS_DIMENSION
    assert len(built.supported_dimensions) >= MIN_SYNTHESIS_DIMENSIONS
    assert built.candidate_claims, "a synthesis must be able to point at what it is about"
    # The example question's expected shape: a synthesis, never a published rank.
    assert built.allowed_ranking is False


def test_synthesis_contract_labels_itself_and_names_the_dimensions():
    built = classify(QUERY, facts=multi_dimensional_pool(), plan=plan(),
                     ranking_basis=SHORTLIST, convergence=CONVERGED, definition_lock=LOCK)
    contract = render_construction_contract(built)
    assert "ANSWER MODE: SYNTHESIZED" in contract
    assert "EVIDENCE-BASED SYNTHESIS" in contract
    assert "must be labelled" in contract
    for axis in ("workload_and_working_hours", "responsibility_and_consequence"):
        assert axis in contract, "each supported dimension must be named to the writer"


def test_synthesis_reports_what_it_cannot_cover():
    # Four of the five planned dimensions covered: enough to synthesise (the
    # fourth fact clears the critic's MIN_FACTS_REQUIRED floor), but the
    # uncovered fifth must still be named to the writer.
    built = classify(
        QUERY,
        facts=multi_dimensional_pool()[:4],
        plan=plan(),
        ranking_basis=SHORTLIST,
        convergence=CONVERGED,
    )
    assert built.mode == ANSWER_SYNTHESIZED
    assert built.missing_dimensions, "planned-but-uncovered axes must be visible"
    contract = render_construction_contract(built)
    assert "Name what is NOT covered" in contract


# ---------------------------------------------------------------------------
# 3. partial evidence with no convergence -> INSUFFICIENT
# ---------------------------------------------------------------------------


def test_fragmented_evidence_is_insufficient_and_names_the_gap():
    built = classify(
        QUERY,
        facts=[fact("A single role was described as demanding", "https://only.example/x", "stress")],
        plan=plan(),
        ranking_basis=SHORTLIST,
        convergence=CONVERGED,
    )
    assert built.mode == ANSWER_INSUFFICIENT
    assert built.blocked_by_degradation is False
    contract = render_construction_contract(built)
    assert "ANSWER MODE: INSUFFICIENT" in contract
    assert "say what cannot be determined" in contract.lower()


def test_insufficient_contract_names_the_missing_evidence():
    built = classify(
        QUERY,
        facts=[fact("One role is demanding", "https://only.example/x", "stress")],
        plan=plan(),
    )
    contract = render_construction_contract(built)
    assert "Evidence is absent for" in contract
    assert "stress" in contract


def test_empty_pool_is_insufficient():
    built = classify(QUERY, facts=[], plan=plan())
    assert built.mode == ANSWER_INSUFFICIENT


def test_unresolved_contradictions_are_carried_into_the_contract():
    built = classify(
        QUERY,
        facts=multi_dimensional_pool(),
        plan=plan(),
        ranking_basis=SHORTLIST,
        convergence=CONVERGED,
        contradictions=[
            {"claim_a": "Burnout is highest in emergency medicine",
             "claim_b": "Burnout is highest in oncology", "resolved": False},
            {"claim_a": "resolved one", "claim_b": "resolved two", "resolved": True},
        ],
    )
    assert len(built.contradictions) == 1, "resolved conflicts are not carried"
    assert "oncology" in render_construction_contract(built)


# ---------------------------------------------------------------------------
# 4. a synthesized candidate cannot become an unsupported "#1"
# ---------------------------------------------------------------------------


def test_synthesis_never_permits_a_ranking():
    built = classify(QUERY, facts=multi_dimensional_pool(), plan=plan(),
                     ranking_basis=SHORTLIST, convergence=CONVERGED)
    assert built.allowed_ranking is False
    contract = render_construction_contract(built)
    assert "NEVER present the synthesis as a published ranking" in contract


def test_convergence_outranks_a_ranking_basis_so_direct_is_unavailable():
    """A loop that concluded the evidence cannot answer must not be overridden
    by a basis assessment that thinks it saw a comparison. This mirrors
    report_consistency.report_status, which gives convergence the same priority."""
    built = classify(
        QUERY,
        facts=multi_dimensional_pool(),
        plan=plan(),
        ranking_basis={"verdict": "ranked", "candidates": ["A", "B"]},
        convergence=CONVERGED,
    )
    assert built.mode != ANSWER_DIRECT, "convergence must forbid a directly-reported ranking"
    assert built.allowed_ranking is False


def test_synthesized_audit_flags_an_unlabelled_synthesis():
    audit = assess_answer_construction(
        QUERY,
        "Emergency medicine is the most demanding job, ahead of all others.",
        {"mode": ANSWER_SYNTHESIZED},
    )
    assert audit["labelled_as_synthesis"] is False
    assert audit["asserts_ranking"] is True
    assert len(audit["violations"]) == 2


def test_synthesized_audit_accepts_a_labelled_synthesis():
    audit = assess_answer_construction(
        QUERY,
        "There is no authoritative ranking. Based on the available evidence across "
        "workload, stress and responsibility, emergency medicine is the strongest "
        "evidence-based candidate, but this is a synthesis rather than a published ranking.",
        {"mode": ANSWER_SYNTHESIZED},
    )
    assert audit["labelled_as_synthesis"] is True
    assert audit["asserts_ranking"] is False
    assert audit["violations"] == []


def test_insufficient_audit_flags_an_answer_that_does_not_say_so():
    audit = assess_answer_construction(
        QUERY, "Firefighting is quite demanding.", {"mode": ANSWER_INSUFFICIENT}
    )
    assert audit["names_the_gap"] is False
    assert audit["violations"]


# ---------------------------------------------------------------------------
# 5. a supported cluster remains a cluster
# ---------------------------------------------------------------------------


def test_several_indistinguishable_candidates_stay_a_cluster():
    built = classify(QUERY, facts=multi_dimensional_pool(), plan=plan(),
                     ranking_basis=SHORTLIST, convergence=CONVERGED)
    assert built.allows_cluster is True
    contract = render_construction_contract(built)
    assert "SUPPORTED CLUSTER" in contract
    assert "never imply a winner" in contract
    assert built.allowed_ranking is False


def test_a_single_candidate_is_not_presented_as_a_cluster():
    built = classify(
        QUERY,
        facts=multi_dimensional_pool(),
        plan=plan(),
        ranking_basis={"verdict": "shortlist", "candidates": ["Emergency medicine physicians"]},
        convergence=CONVERGED,
    )
    assert built.allows_cluster is False
    contract = render_construction_contract(built)
    assert "SUPPORTED CLUSTER" not in contract
    assert "strongest the evidence supports" in contract


# ---------------------------------------------------------------------------
# 6. narrow single-source evidence cannot trigger SYNTHESIZED
# ---------------------------------------------------------------------------


def test_one_narrow_study_cannot_synthesise():
    """The single most important refusal: a long report on one case is ONE study."""
    one_study = [
        fact(f"Finding {i} about occupational demand", "https://single.example/report",
             DIMENSION_AXES[i], corroboration=3)
        for i in range(len(DIMENSION_AXES))
    ]
    built = classify(QUERY, facts=one_study, plan=plan(), ranking_basis=SHORTLIST)
    assert built.mode == ANSWER_INSUFFICIENT
    assert "one document" in built.reason
    assert MAX_SINGLE_DOCUMENT_SHARE == 0.6


def test_single_dimension_cannot_synthesise():
    built = classify(
        QUERY,
        facts=[fact(f"Claim {i}", f"https://s{i}.example/x", DIMENSION_AXES[0],
                    corroboration=2) for i in range(6)],
        plan=plan(),
        ranking_basis=SHORTLIST,
    )
    assert built.mode == ANSWER_INSUFFICIENT
    assert "dimension" in built.reason


def test_uncorroborated_chain_cannot_synthesise():
    built = classify(
        QUERY,
        facts=[fact(f"Claim {i}", f"https://s{i}.example/x", DIMENSION_AXES[i], corroboration=1)
               for i in range(len(DIMENSION_AXES))],
        plan=plan(),
        ranking_basis=SHORTLIST,
    )
    assert built.mode == ANSWER_INSUFFICIENT
    assert "corroborated" in built.reason


def test_a_ranking_question_with_no_named_candidates_cannot_synthesise():
    built = classify(
        QUERY,
        facts=[fact(f"Claim {i}", f"https://s{i}.example/x", DIMENSION_AXES[i], corroboration=2)
               for i in range(len(DIMENSION_AXES))],
        plan=plan(),
        ranking_basis={"verdict": "shortlist", "candidates": []},
    )
    assert built.mode == ANSWER_INSUFFICIENT
    assert "no candidates" in built.reason


def test_junk_inferred_candidates_are_rejected():
    """Recurrence alone admits a repeated sentence-initial noun, which would let
    the contract name a non-option as the strongest candidate."""
    for junk in ("Claim", "Study", "Report", "A", "Xy", ""):
        assert _plausible_candidate(junk) is False, junk
    for real in ("Emergency medicine physicians", "PostgreSQL", "Air traffic controllers"):
        assert _plausible_candidate(real) is True, real

    built = classify(
        QUERY,
        facts=[fact(f"Claim {i} about a role", f"https://s{i % 3}.example/x",
                    DIMENSION_AXES[i % 5], corroboration=2) for i in range(5)],
        plan=plan(),
        ranking_basis={"verdict": "shortlist", "candidates": []},
    )
    assert built.candidate_claims == []
    assert built.mode == ANSWER_INSUFFICIENT


# ---------------------------------------------------------------------------
# 7. a degraded run cannot bypass safeguards
# ---------------------------------------------------------------------------


def test_degraded_run_cannot_enter_synthesized():
    """The extraction was degenerate, so the pool cannot attest that the evidence
    was READ correctly — which is exactly what a synthesis claims."""
    built = classify(QUERY, facts=multi_dimensional_pool(), plan=plan(),
                     ranking_basis=SHORTLIST, convergence=CONVERGED, degraded=True)
    assert built.mode == ANSWER_INSUFFICIENT
    assert built.blocked_by_degradation is True
    assert "degraded" in built.reason.lower()


def test_degraded_contract_states_why_no_synthesis_is_available():
    built = classify(QUERY, facts=multi_dimensional_pool(), plan=plan(),
                     ranking_basis=SHORTLIST, degraded=True)
    contract = render_construction_contract(built)
    assert "this run was degraded" in contract.lower()
    assert "ANSWER MODE: SYNTHESIZED" not in contract


def test_degraded_run_can_still_report_a_direct_source():
    """DIRECT is a source stating the answer; reading it does not require the
    synthesis layer's attestation."""
    built = classify(QUERY, facts=[fact("A ranks higher than B", "https://a.example/x",
                                        "evidence", corroboration=2)],
                     ranking_basis={"verdict": "ranked"}, degraded=True)
    assert built.mode == ANSWER_DIRECT


def test_the_module_never_raises_on_hostile_input():
    for bad in (None, "", 0, [], {}, "not a query"):
        built = classify(bad if isinstance(bad, str) else "", facts=[None, 1, "x", {}][:1])
        assert built.mode in (ANSWER_DIRECT, ANSWER_SYNTHESIZED, ANSWER_INSUFFICIENT)
        render_construction_contract(built)


# ---------------------------------------------------------------------------
# 8. the locked interpretation is preserved throughout construction
# ---------------------------------------------------------------------------


def test_locked_interpretation_is_carried_and_ordered_to_be_preserved():
    built = classify(QUERY, facts=multi_dimensional_pool(), plan=plan(),
                     ranking_basis=SHORTLIST, convergence=CONVERGED, definition_lock=LOCK)
    assert "demanding job" in built.locked_interpretation
    assert "highest combined workload" in built.locked_interpretation
    contract = render_construction_contract(built)
    assert "PRESERVE THE LOCKED INTERPRETATION" in contract
    assert "workload, stress and responsibility" in contract
    assert "redefine the question" in contract


def test_no_lock_means_no_lock_paragraph():
    built = classify(QUERY, facts=multi_dimensional_pool(), plan=plan(),
                     ranking_basis=SHORTLIST, convergence=CONVERGED)
    assert "PRESERVE THE LOCKED INTERPRETATION" not in render_construction_contract(built)


def test_lock_is_preserved_even_on_the_insufficient_path():
    """The reading is fixed before research; a refusal must honour it too."""
    built = classify(QUERY, facts=[], plan=plan(), definition_lock=LOCK)
    assert built.mode == ANSWER_INSUFFICIENT
    assert built.locked_interpretation


# ---------------------------------------------------------------------------
# Contract shape + serialisation
# ---------------------------------------------------------------------------


def test_dict_carries_every_field_the_writer_contract_requires():
    built = classify(QUERY, facts=multi_dimensional_pool(), plan=plan(),
                     ranking_basis=SHORTLIST, convergence=CONVERGED, definition_lock=LOCK)
    payload = built.to_dict()
    for key in (
        "mode", "supported_dimensions", "missing_dimensions", "candidate_claims",
        "supporting_evidence", "contradictions", "inference_level", "allowed_ranking",
        "allows_cluster", "locked_interpretation", "dimensions_covered",
    ):
        assert key in payload, key
    assert payload["mode"] in (ANSWER_DIRECT, ANSWER_SYNTHESIZED, ANSWER_INSUFFICIENT)


def test_supporting_evidence_is_bounded_and_deduped_by_document():
    facts = multi_dimensional_pool() + [
        fact("A second claim from the same page", "https://pubmed.example/a#section",
             DIMENSION_AXES[0], corroboration=2)
    ]
    built = classify(QUERY, facts=facts, plan=plan(), ranking_basis=SHORTLIST)
    assert len(built.supporting_evidence) <= 6
    sources = [e["source"] for e in built.supporting_evidence]
    docs = {s.split("#")[0] for s in sources}
    assert len(docs) == len(sources), "one document must not supply two 'independent' findings"


def test_contract_is_empty_for_direct_answers():
    """DIRECT adds no construction instruction: the existing contracts already
    govern it, and a second voice in the prompt is how they drift apart."""
    built = classify(QUERY, facts=[fact("A beats B", "https://a.example/x", "evidence",
                                        corroboration=2)],
                     ranking_basis={"verdict": "ranked"})
    assert built.mode == ANSWER_DIRECT
    assert render_construction_contract(built) == ""


# ---------------------------------------------------------------------------
# Graph-level wiring: the decision reaches state and the audit
# ---------------------------------------------------------------------------
#
# Everything above tests the classifier in isolation. These two run the REAL
# synthesizer inside the production graph (stubbed planner/search/critic/LLM) so
# the parts a unit test cannot reach are covered: the workflow promoting the
# decision onto state, and build_answer_audit rendering it.

import app.graph.workflow as wf  # noqa: E402
from app.core.config import Settings  # noqa: E402


class _RecordingSynthWriter:
    """Minimal LLM stub for the real synthesizer: returns writer prose."""

    def __init__(self):
        self.prompts = []
        self.settings = Settings(groq_api_key="k", _env_file=None)

    async def generate_json(self, system_prompt, user_prompt, **kwargs):
        self.prompts.append(f"{system_prompt}\n{user_prompt}")
        return {"answer": "The evidence supports a constructed answer [1]."}

    async def generate_text(self, system_prompt, user_prompt, **kwargs):
        return await self.generate_json(system_prompt, user_prompt, **kwargs)


async def _run_graph_with_real_synthesizer(monkeypatch, facts):
    """Production graph, real synthesizer, everything else stubbed."""
    settings = Settings(groq_api_key="k", _env_file=None)
    writer = _RecordingSynthWriter()

    async def fake_planner(llm, query, critique_feedback="", today="", **kwargs):
        return [
            {"id": i + 1, "question": axis, "axis": axis,
             "search_type": "academic", "priority": 1, "depends_on": [],
             "coverage_goal": "", "domain": "general", "minimum_sources": 1,
             "stop_condition": "enough", "variants": []}
            for i, axis in enumerate(
                ["psychological stress and burnout", "workload and working hours",
                 "responsibility and consequence", "expertise and qualification",
                 "physical demands"]
            )
        ]

    async def fake_summarizer(llm, query, search_results=None,
                              specialist_role="general"):
        return [dict(f) for f in facts]

    async def fake_critic(llm, query, facts=None, iteration=1,
                          max_iterations=3, contradictions=None, **kwargs):
        return {"is_sufficient": False, "reason": "no source ranks these",
                "improved_queries": [], "confidence": 0.7,
                "gaps": ["no source ranks these candidates"], "gate_failures": []}

    class StubSearch:
        async def run_search(self, questions):
            first = questions[0]
            text = first[0] if isinstance(first, (tuple, list)) else first
            return [{"url": "https://a.example/x", "sub_question": text,
                     "snippet": "snip", "content": "content"}]

    monkeypatch.setattr(wf, "planner_agent", fake_planner)
    monkeypatch.setattr(wf, "summarizer_agent", fake_summarizer)
    monkeypatch.setattr(wf, "critic_agent", fake_critic)
    monkeypatch.setattr(
        wf, "verify_facts",
        lambda facts, search_results: [{**f, "verified": True} for f in facts],
    )
    # `writer` IS the LLM client here: create_workflow takes it directly and the
    # real synthesizer runs against it, which is the point of this test.
    state = wf.build_initial_state(QUERY, 3, mode="quick")
    workflow = wf.create_workflow(writer, StubSearch())
    final = {}
    async for snap in workflow.astream(state, stream_mode="values"):
        final = snap
    return final, writer


async def test_construction_decision_reaches_final_state_and_the_audit(monkeypatch):
    facts = [
        {"claim": "Emergency medicine physicians report the highest burnout",
         "source": "https://pubmed.example/a", "sub_question": "psychological stress and burnout",
         "verified": True, "corroboration_count": 2, "confidence": 0.8},
        {"claim": "Surgical residents average the longest weekly hours",
         "source": "https://ilo.example/b", "sub_question": "workload and working hours",
         "verified": True, "corroboration_count": 2, "confidence": 0.8},
        {"claim": "Air traffic controllers carry the highest error consequence",
         "source": "https://ntsb.example/c", "sub_question": "responsibility and consequence",
         "verified": True, "corroboration_count": 2, "confidence": 0.8},
        {"claim": "Psychiatrists hold the longest qualification pathways",
         "source": "https://oecd.example/d", "sub_question": "expertise and qualification",
         "verified": True, "corroboration_count": 2, "confidence": 0.8},
        {"claim": "Firefighters show the highest physical demand ratings",
         "source": "https://eurofound.example/e", "sub_question": "physical demands",
         "verified": True, "corroboration_count": 2, "confidence": 0.8},
    ]
    final, writer = await _run_graph_with_real_synthesizer(monkeypatch, facts)

    construction = final.get("answer_construction")
    assert isinstance(construction, dict) and construction.get("mode"), \
        "the decision must be promoted onto graph state"
    # The audit is the only place the run records which answer state it reached.
    audit = final.get("final_audit", "")
    assert "Answer construction" in audit
    assert str(construction["mode"]).upper() in audit, audit[:400]
