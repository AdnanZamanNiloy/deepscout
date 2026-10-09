"""The quality scorer must actually catch the failures the product shipped.

A scorer that cannot fail is decoration. Each case below is a real shape from
this session, including the exact text of the degraded run, and the assertion is
that the score lands where a human reading the report would put it.
"""
from bench.eval_report_quality import QUALITY_TARGET, score_report

EVIDENCE = [
    "The World Economic Forum's Future of Jobs 2023 report projected that "
    "44% of workers' skills would be disrupted by 2027 and that analytical "
    "thinking is a top in-demand skill.",
    "The Bureau of Labor Statistics employment projections published January 8, "
    "2026 expect AI adoption and productivity gains to reduce labour demand.",
    "A 2025 academic review found that multi-agent AI systems shift software "
    "engineering work toward orchestration and evaluation.",
]

GOOD = (
    "The most demanding skill in 2027 is analytical thinking [1]. The World "
    "Economic Forum projected that 44% of workers' skills would be disrupted by "
    "2027, and ranked analytical thinking among the top in-demand skills [1]. "
    "That expectation is a forecast rather than a measurement of difficulty. "
    "There is a countervailing signal: the Bureau of Labor Statistics expects "
    "AI adoption to reduce labour demand rather than simply reshape it [2]. "
    "The evidence does not settle which effect dominates."
)

# Verbatim from the degraded run: raw source fragments, no sentence structure.
DEGRADED_RAW_TEXT = (
    "most demanding skill in 2027 Skills scales the gap, not the safety, "
    "especially when a wrong invocation can unlock a door or move money "
    "In this work, we collect and release a first-of-its-kind dataset for "
    "multimodal skill assessment focusing on assessing piano player's skill "
    "level, answer the asked questions"
)

# Plausible prose, but the figures are invented — the failure a lexical score
# cannot see.
FABRICATED_NUMBERS = (
    "The most demanding skill in 2027 is analytical thinking [1]. Employer "
    "demand for it rose 78% between 2023 and 2026, and 92% of organisations "
    "now rank it first [2]. A 2025 review confirmed the trend [3]."
)

# Answers a neighbouring question: well written, well cited, wrong topic.
OFF_TOPIC = (
    "Piano skill assessment has advanced considerably. Multimodal datasets now "
    "grade performance automatically [1], and prior work established baselines "
    "for evaluation [1]. Observers expect further progress in automated grading."
)


def test_a_good_report_scores_at_or_above_target():
    q = score_report(
        "what can be the most demanding skill in 2027?", GOOD, EVIDENCE, "factual"
    )
    assert q.score >= QUALITY_TARGET, q.to_dict()


def test_raw_source_dump_scores_far_below_target():
    """THE live degraded output. Must be caught, and mostly on readability."""
    q = score_report(
        "what can be the most demanding skill in 2027?", DEGRADED_RAW_TEXT,
        EVIDENCE, "factual",
    )
    assert q.score < 6.0, q.to_dict()
    assert q.readability < 0.5, q.to_dict()


def test_fabricated_numbers_are_penalised():
    """The number check is the only defence against confident invention."""
    q = score_report(
        "what can be the most demanding skill in 2027?", FABRICATED_NUMBERS,
        EVIDENCE, "data-analysis",
    )
    assert q.accuracy < 0.5, q.to_dict()
    # A data-analysis answer is judged heavily on numeric accuracy.
    assert q.score < QUALITY_TARGET, q.to_dict()


def test_off_topic_answer_fails_coverage_and_grounding():
    q = score_report(
        "what can be the most demanding skill in 2027?", OFF_TOPIC, EVIDENCE, "factual"
    )
    assert q.coverage < 0.5, q.to_dict()
    assert q.score < 7.0, q.to_dict()


def test_the_weights_differ_by_query_type():
    """A comparison answer and a data answer cannot be judged by one rubric."""
    comp = score_report("compare A and B", GOOD, EVIDENCE, "comparison")
    data = score_report("what are the figures", GOOD, EVIDENCE, "data-analysis")
    assert comp.weights != data.weights
    assert comp.weights["coverage"] > data.weights["coverage"]
    assert data.weights["accuracy"] > comp.weights["accuracy"]


def test_empty_answer_scores_zero():
    q = score_report("anything", "", EVIDENCE, "factual")
    assert q.score == 0.0
