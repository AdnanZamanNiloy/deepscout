"""Query-anchored research focus: scope, coverage, concentration, drift.

These tests are the regression net for a single failure mode: the research loop
answering a question OTHER than the one asked. The historical bug was concrete
— a hardcoded AI taxonomy was forced onto every query, so "population of Malawi
in 2024" produced six contracts about model capability and datacenter power
demand, and the critic then refused to finalize until each had primary sources.

Two properties are load-bearing and both are asserted below:
  * the planner's frontier injection applies to a QUERY's shape, never a topic;
  * focus measurement uses the plan's own dimensions, with no built-in list.
"""


from app.agents.focus import (
    ADVERSARIAL_AXES,
    DEFAULT_CONCENTRATION_LIMIT,
    FocusReport,
    ResearchScope,
    _subject_tokens,
    assess_focus,
    needs_redirect,
    should_go_deeper,
    targeted_followups,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GRID_PLAN = [
    {"axis": "definition", "question": "grid design terminology and components"},
    {"axis": "evidence", "question": "grid integration measured outcomes statistics"},
    {"axis": "cost", "question": "grid upgrade costs and financing"},
    {"axis": "regulation", "question": "grid regulation and policy frameworks"},
    {"axis": "criticism", "question": "grid integration failure cases"},
]
GRID_QUERY = "How should a country redesign its electricity grid for renewables?"


def _fact(claim, source, sub_question="", verified=True, axis=""):
    fact = {"claim": claim, "source": source, "sub_question": sub_question}
    if verified:
        fact["verified"] = True
    if axis:
        fact["axis"] = axis
    return fact


def _spread_facts(plan, per_dimension=3):
    """Verified evidence on every dimension of a plan."""
    out = []
    for i, contract in enumerate(plan):
        for j in range(per_dimension):
            out.append(_fact(
                f"{contract['question']} finding {j}",
                f"https://source{i}{j}.org/report",
                sub_question=contract["question"],
            ))
    return out


# ---------------------------------------------------------------------------
# Domain agnosticism
# ---------------------------------------------------------------------------


def test_no_builtin_topic_taxonomy_in_focus_module():
    """The bug was a topic list in the planner. focus.py must not reintroduce
    one: every term below is only permitted inside a docstring."""
    import ast
    import io
    import re
    import tokenize

    path = "app/agents/focus.py"
    with open(path) as fh:
        source = fh.read()
    tree = ast.parse(source)
    docstring_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if ast.get_docstring(node, clean=False) is not None:
                body = node.body[0]
                docstring_lines.update(range(body.lineno, (body.end_lineno or 0) + 1))

    topics = (
        "frontier", "capability", "infrastructure", "economics", "adoption",
        "regulation", "safety", "datacenter", "gpu", "benchmark",
        "cybersecurity", "malawi", "population",
    )
    # Two entries are English compound nouns, not research topics: they exist so
    # a conjunction inside one term ("health and safety") is not miscounted as
    # two subjects. Allowed explicitly rather than by weakening the guard.
    compound_noun_exception = ("health and safety", "safety and security")
    offenders = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type != tokenize.STRING:
            continue
        if tok.start[0] in docstring_lines:
            continue
        for topic in topics:
            if not re.search(rf"\b{topic}\b", tok.string, re.IGNORECASE):
                continue
            if any(exc in tok.string.lower() for exc in compound_noun_exception):
                continue
            offenders.append((tok.start[0], topic))
    assert not offenders, f"executable topic strings in focus.py: {offenders}"


def test_dimensions_come_from_the_plan_not_a_taxonomy():
    """Two unrelated subjects described by identical code."""
    plan = [{"axis": "prosody", "question": "iambic metre and stress patterns"},
            {"axis": "history", "question": "the sonnet's adoption in the 17th century"}]
    facts = [_fact("iambic metre alternates unstressed and stressed syllables",
                   "https://poetry.org/1", plan[0]["question"])]
    report = assess_focus("How does iambic metre work in English sonnets?", plan, facts)
    assert set(report.dimensions) == {"prosody", "history"}
    assert report.missing == ("history",)
    assert report.coverage == 0.5


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def test_unverified_facts_do_not_count_as_coverage():
    """A run that retrieved 40 unverified snippets has not covered anything.

    Counting unverified claims as coverage is how a run that found nothing
    reports full coverage, which is the same self-verifying-evidence trap
    AGENTS.md records for the confidence engine.
    """
    plan = [{"axis": "evidence", "question": "measured outcomes"},
            {"axis": "cost", "question": "cost data"}]
    facts = [_fact("outcome finding", "https://a.org/1", plan[0]["question"], verified=False)]
    report = assess_focus("measured outcomes and cost", plan, facts)
    assert report.coverage == 0.0
    assert set(report.missing) == {"evidence", "cost"}


def test_full_coverage_is_reported_when_every_dimension_has_verified_evidence():
    report = assess_focus(GRID_QUERY, GRID_PLAN, _spread_facts(GRID_PLAN))
    assert report.coverage == 1.0
    assert not report.missing and not report.thin


def test_thin_dimension_is_distinguished_from_missing():
    plan = [{"axis": "evidence", "question": "outcomes"}, {"axis": "cost", "question": "costs"}]
    # Two-part question, so the per-dimension bar is 2: one verified fact is
    # researched but thin, two clears it.
    facts = [_fact("one outcome", "https://a.org/1", plan[0]["question"])]
    report = assess_focus("outcomes and costs", plan, facts)
    assert report.missing == ("cost",)
    assert report.thin == ("evidence",)  # researched but below the bar


# ---------------------------------------------------------------------------
# Concentration — the drift failure
# ---------------------------------------------------------------------------


def test_concentration_is_detected_when_one_dimension_dominates():
    evidence_only = [c for c in GRID_PLAN if c["axis"] == "evidence"]
    report = assess_focus(GRID_QUERY, GRID_PLAN, _spread_facts(evidence_only, per_dimension=8))
    assert report.concentrated
    assert report.dominant_dimension == "evidence"
    assert report.concentration > DEFAULT_CONCENTRATION_LIMIT


def test_concentration_exempts_adversarial_axes():
    """A report that is mostly criticism is doing its job, not drifting."""
    plan = [{"axis": "counter_evidence", "question": "counterarguments"},
            {"axis": "evidence", "question": "outcomes"}]
    facts = [_fact(f"counterargument {i}", f"https://c{i}.org", plan[0]["question"])
             for i in range(6)]
    facts.append(_fact("one supportive outcome", "https://e.org", plan[1]["question"]))
    report = assess_focus("does this work", plan, facts)
    assert "counter_evidence" in ADVERSARIAL_AXES
    assert report.concentration > DEFAULT_CONCENTRATION_LIMIT
    assert not report.concentrated


def test_balanced_research_is_not_concentrated():
    report = assess_focus(GRID_QUERY, GRID_PLAN, _spread_facts(GRID_PLAN))
    assert not report.concentrated


def test_host_concentration_flags_a_single_publisher_empire():
    plan = [{"axis": "evidence", "question": "outcomes"}, {"axis": "cost", "question": "costs"}]
    facts = [_fact(f"finding {i}", "https://same-publisher.org/p", plan[0]["question"])
             for i in range(4)]
    facts.append(_fact("cost finding", "https://other.org/c", plan[1]["question"]))
    report = assess_focus("outcomes and costs", plan, facts)
    assert report.host_concentration > 0.7
    assert report.source_diversity == 2


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------


def test_drift_is_measured_against_the_original_question():
    """The failure this exists for: a healthy-looking pool about another subject.

    Search keeps returning genuinely interesting material about a different
    topic. Every evidence-size gate passes — facts were found, verified, and
    cited — so only a question-anchored measure can catch it.
    """
    query = "population of Malawi in 2024"
    facts = [
        _fact("Transformer attention head pruning reduces inference latency by 12%",
              f"https://arxiv{i}.org/abs/{i}", "model capability")
        for i in range(6)
    ]
    report = assess_focus(query, [{"axis": "evidence", "question": "population statistics"}], facts)
    assert report.drifted
    assert report.off_query_share > 0.9
    assert report.coverage == 0.0  # nothing answers the question either


def test_on_question_evidence_does_not_register_as_drift():
    query = "population of Malawi in 2024"
    facts = [
        _fact("Malawi's population was 21.4 million in 2024", f"https://wb{i}.org/x", "population")
        for i in range(4)
    ]
    report = assess_focus(query, [{"axis": "evidence", "question": "population statistics"}], facts)
    assert not report.drifted


def test_negation_is_not_a_stopword_in_drift_measurement():
    """AGENTS.md: negation must never be a stopword in a similarity component.

    A run that found evidence about states WITH electricity access must not read
    as answering a question about states WITHOUT it.
    """
    tokens = _subject_tokens("countries without electricity access")
    assert "without" in tokens
    assert "without" not in _subject_tokens("countries with electricity access")


# ---------------------------------------------------------------------------
# Proportionality: narrow questions must NOT expand
# ---------------------------------------------------------------------------


def test_narrow_question_is_not_flagged_incomplete_for_having_one_dimension():
    """A definition answered from one good dimension is COMPLETE.

    Treating it as incomplete is how a loop drifts: it widens a question the
    user did not ask wide, which is the behaviour being fixed.
    """
    query = "What is TCP congestion control?"
    plan = [{"axis": "definition", "question": "congestion control mechanisms and algorithms"}]
    facts = [_fact("TCP congestion control adjusts the sending rate in response to loss",
                   f"https://ietf{i}.org/rfc", plan[0]["question"]) for i in range(3)]
    report = assess_focus(query, plan, facts)
    assert report.is_narrow
    assert report.coverage == 1.0
    assert not report.concentrated
    assert not report.needs_more_research
    assert not needs_redirect(report, iteration=1, max_iterations=3)


def test_narrow_question_with_a_real_gap_still_redirects():
    query = "What is TCP congestion control?"
    plan = [
        {"axis": "definition", "question": "congestion control mechanisms"},
        {"axis": "comparison", "question": "Reno versus CUBIC versus BBR"},
    ]
    facts = [_fact("congestion control adjusts sending rate on loss", "https://ietf.org/1",
                   plan[0]["question"])]
    report = assess_focus(query, plan, facts)
    assert report.missing == ("comparison",)
    assert needs_redirect(report, iteration=1, max_iterations=3)


def test_broad_question_with_gaps_redirects_and_goes_deeper():
    report = assess_focus(GRID_QUERY, GRID_PLAN, _spread_facts(GRID_PLAN[:1]))
    assert report.is_broad
    assert needs_redirect(report, iteration=1, max_iterations=3)
    assert should_go_deeper(report, iteration=1, max_iterations=3)


def test_no_redirect_past_the_iteration_ceiling():
    report = assess_focus(GRID_QUERY, GRID_PLAN, _spread_facts(GRID_PLAN[:1]))
    assert not needs_redirect(report, iteration=3, max_iterations=3)
    assert not should_go_deeper(report, iteration=3, max_iterations=3)


# ---------------------------------------------------------------------------
# Targeted follow-ups
# ---------------------------------------------------------------------------


def test_followups_target_missing_dimensions_not_known_ones():
    report = assess_focus(GRID_QUERY, GRID_PLAN, _spread_facts(GRID_PLAN[:1]))
    queries = targeted_followups(report, GRID_PLAN)
    joined = " ".join(queries).lower()
    assert "regulat" in joined or "policy" in joined
    assert "failure" in joined or "critic" in joined


def test_followups_reuse_the_plans_own_phrasing_for_that_dimension():
    """Domain agnosticism in practice: no template can inject a foreign topic,
    because the follow-up IS the plan's contract for that dimension."""
    plan = [
        {"axis": "evidence", "question": "prognostication accuracy across seasons"},
        {"axis": "prosody", "question": "enjambment in Milton's sonnets"},
    ]
    facts = [_fact("accuracy figures", "https://met.gov/1", plan[0]["question"])]
    report = assess_focus("how accurate are seasonal forecasts and how does enjambment work?", plan, facts)
    queries = targeted_followups(report, plan)
    assert plan[1]["question"] in queries


def test_followups_are_deduped_against_already_asked_queries():
    plan = [{"axis": "evidence", "question": "outcomes"}, {"axis": "cost", "question": "costs"}]
    facts = [_fact("one outcome", "https://a.org/1", plan[0]["question"]),
             _fact("two outcomes", "https://b.org/2", plan[0]["question"])]
    report = assess_focus("outcomes and costs", plan, facts)
    assert report.missing == ("cost",) and not report.thin
    assert targeted_followups(report, plan) == ["costs"]
    assert targeted_followups(report, plan, already_asked=["costs"]) == []
    assert targeted_followups(report, plan, already_asked=["unrelated words entirely"]) == ["costs"]


def test_drift_only_contributes_a_followup_when_nothing_is_missing():
    """A drifted run is told to re-anchor on the question, not to widen its plan."""
    query = "population of Malawi in 2024"
    plan = [{"axis": "evidence", "question": "Malawi population statistics"}]
    facts = [_fact("Malawi's population was 21.4 million in 2024", "https://wb.org/1",
                   plan[0]["question"])]
    on_target = assess_focus(query, plan, facts)
    assert targeted_followups(on_target, plan) == []

    drifted = FocusReport(query=query, drift=0.9, off_query_share=0.9, coverage=1.0)
    assert targeted_followups(drifted, plan) == [query]


def test_no_followups_when_the_question_is_answered():
    report = assess_focus(GRID_QUERY, GRID_PLAN, _spread_facts(GRID_PLAN))
    assert targeted_followups(report, GRID_PLAN) == []


# ---------------------------------------------------------------------------
# Report shape and robustness
# ---------------------------------------------------------------------------


def test_report_is_serialisable_and_explains_itself():
    report = assess_focus(GRID_QUERY, GRID_PLAN, _spread_facts(GRID_PLAN[:2]))
    payload = report.to_dict()
    assert payload["coverage"] > 0
    assert "coverage=" in report.summary()
    assert set(payload["dimensions"]) >= {"definition", "evidence"}


def test_empty_inputs_are_total_not_fatal():
    """Every public entry point must tolerate an empty or malformed run."""
    for report in (
        assess_focus("", [], []),
        assess_focus("q", None, None),
        assess_focus("q", [{"axis": "", "question": ""}], [{"claim": None}]),
        assess_focus("q", ["not a mapping"], [{"nonsense": True}]),
    ):
        assert isinstance(report, FocusReport)
        assert 0.0 <= report.coverage <= 1.0
        assert targeted_followups(report) == [] or all(
            isinstance(q, str) and q for q in targeted_followups(report)
        )


def test_scope_flags_breadth_from_the_question_and_its_plan():
    broad = ResearchScope.from_plan(GRID_QUERY, GRID_PLAN)
    narrow = ResearchScope.from_plan("What is TCP congestion control?",
                                     [{"axis": "definition", "question": "mechanisms"}])
    assert broad.is_broad and not broad.is_narrow
    assert narrow.is_narrow and not narrow.is_broad


def test_must_cover_dimensions_are_required_even_if_the_plan_omits_them():
    plan = [{"axis": "evidence", "question": "outcomes"}]
    facts = [_fact("outcome finding", "https://a.org/1", plan[0]["question"])]
    report = assess_focus("outcomes and their cost", plan, facts,
                          must_cover=["evidence", "cost"])
    assert report.coverage == 0.5
    assert report.missing == ("cost",)

# ---------------------------------------------------------------------------
# Frontier injection is conditional on the QUESTION, not on a topic
# ---------------------------------------------------------------------------


def test_frontier_contracts_are_only_added_to_trends_shaped_questions():
    """The core regression. A hardcoded AI taxonomy used to be forced onto every
    query, so "population of Malawi in 2024" planned six contracts about model
    capability and datacenter power demand."""
    from app.agents.planner import fallback_plan

    frontier = {
        "capability", "infrastructure", "economics", "adoption", "regulation",
        "safety", "counter_evidence",
    }
    for query in (
        "population of Malawi in 2024",
        "What is TCP congestion control?",
        "Compare nuclear and solar levelized cost of electricity",
        "Who is the CEO of Siemens and when did he start?",
        "Is intermittent fasting safe?",
    ):
        axes = {c["axis"] for c in fallback_plan(query, target_count=15)}
        assert not (axes & frontier), f"{query} received frontier axes {axes & frontier}"

    trends_axes = {
        c["axis"]
        for c in fallback_plan("What are the current trends in global coffee prices?", target_count=15)
    }
    assert frontier - {"counter_evidence"} <= trends_axes


def test_frontier_question_text_is_derived_from_the_user_query():
    """An injected contract must not name a subject the user did not mention."""
    from app.agents.planner import fallback_plan

    plan = fallback_plan("What are the current trends in global coffee prices?", target_count=15)
    injected = [c for c in plan if c["axis"] in {"capability", "economics", "adoption"}]
    assert injected
    for contract in injected:
        question = contract["question"].lower()
        assert "coffee" in question
        assert "ai " not in question and "ai(" not in question
        assert "datacenter" not in question and "gpu" not in question


def test_fact_attribution_is_tolerant_of_a_shortened_sub_question():
    """The summarizer's sub_question is often a re-worded variant, not the
    contract text. Exact matching attributed real evidence to nothing, so a
    covered dimension still measured as uncovered — which is what made the
    reviewer report the same gaps every round while coverage stayed at zero.
    """
    query = "population of Malawi in 2024"
    plan = [{"axis": "evidence", "question": "population statistics", "minimum_sources": 1}]
    shortened = [_fact("Malawi population 21.4 million", "https://wb.example/1", "population")]
    exact = [_fact("Malawi population 21.4 million", "https://wb.example/1",
                   "population statistics")]
    for facts in (shortened, exact):
        report = assess_focus(query, plan, facts)
        assert report.coverage == 1.0, f"not attributed: {facts[0]['sub_question']!r}"
        assert not report.drifted
        assert not report.missing


def test_reangled_contract_for_the_same_axis_still_attributes():
    """A gap re-angle changes the question text but not the axis, so its facts
    must still land on the right dimension."""
    query = "current state of X"
    plan = [{"axis": "regulation",
             "question": "current state of X regulation official statistics",
             "minimum_sources": 1}]
    facts = [_fact("a regulatory finding", "https://gov.example/1",
                   "current state of X regulation")]
    report = assess_focus(query, plan, facts)
    assert report.coverage == 1.0
    assert not report.missing
