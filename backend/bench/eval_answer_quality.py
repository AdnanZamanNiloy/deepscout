#!/usr/bin/env python3
"""Answer-quality benchmark for adaptive synthesis (deterministic, LLM-free).

This is the benchmark the synthesis rework is measured against. It does NOT
reward a particular heading list — a structurally different answer can score
highly if it answers the question well. It scores the observable qualities the
product promises:

    relevance, evidence grounding, synthesis, coherence, signal density,
    citation quality, uncertainty handling, process-noise leakage, and
    structural adaptability.

Two complementary halves, both deterministic and network-free:

  1. SYNTHETIC cases: a labeled (query, candidate answer) pair per category.
     Each case carries a `shape` label describing the structure a good answer
     should take (concise, criterion-comparison, mechanism-chain, steps,
     options-tradeoffs, thematic, …). The structural-adaptability score checks
     that the answer's detected shape matches the question's needs, not that it
     contains named headings.
  2. PIPELINE cases (optional, `--pipeline`): runs a small representative query
     set through the production graph with the offline mocks and scores the
     delivered answer with the same metrics (structure + process-noise).

Usage (from backend/):

    python bench/eval_answer_quality.py
    python bench/eval_answer_quality.py --pipeline
    python bench/eval_answer_quality.py --json

Exit code 0 when every aggregate floor passes, 1 on regression.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.answer_quality import score_answer_relevance

# ---------------------------------------------------------------------------
# Process-noise detector — must match the product's own scrubber cues.
# ---------------------------------------------------------------------------

_PROCESS_NOISE_RE = re.compile(
    r"(pipeline stage|deterministic fallback|corroboration attempt|"
    r"evidence grade\s*[A-D]\b|search budget|internal confidence|"
    r"uncovered (?:research )?dimension|agent state|token budget|"
    r"below the threshold|relevance \d+/100|of \d+ facts verified|"
    r"single-source after \d+|this report was (?:assembled|generated))",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Structural shape detection — what structure did the answer actually use?
# ---------------------------------------------------------------------------

_COMPARISON_RE = re.compile(r"\b(vs\.?|versus|compared with|while .+ (?:is|are) more|"
                            r"on the other hand|whereas)\b", re.I)
_STEPS_RE = re.compile(r"^\s*\d+[.)]\s+", re.M)
_MECHANISM_RE = re.compile(r"\b(because|caused by|driven by|as a result of|leads to|"
                           r"causes?|the reason)\b", re.I)
_OPTIONS_RE = re.compile(r"\b(option|trade-off|tradeoff|recommend|if you value|"
                         r"depends on)\b", re.I)
_HISTORY_RE = re.compile(r"\b(19|20)\d{2}\b")
# The numbered 'n) **Sense** — gloss' block an ambiguous query must open with.
_DISAMBIG_RE = re.compile(r"^\s*\d+\)\s*\*\*[^*]{2,120}\*\*", re.M)


def detect_shape(answer: str) -> str:
    """Best-effort structure label for an answer (deterministic)."""
    text = answer or ""
    if _DISAMBIG_RE.search(text):
        return "disambiguation"
    numbered = len(_STEPS_RE.findall(text))
    if numbered >= 2:
        return "steps"
    if _OPTIONS_RE.search(text):
        return "options"
    if _COMPARISON_RE.search(text):
        return "criterion_comparison"
    if _MECHANISM_RE.search(text):
        return "mechanism_chain"
    if len(_HISTORY_RE.findall(text)) >= 3:
        return "chronological"
    headings = re.findall(r"^\s{0,3}##\s+(.+)$", text, re.M)
    if len(headings) >= 2:
        return "thematic"
    return "concise"


# Category → the shapes that legitimately answer it. A match is adaptive.
_CATEGORY_SHAPES: Dict[str, set] = {
    "factual": {"concise", "thematic"},
    "definition": {"concise"},
    "explanation": {"mechanism_chain", "thematic", "concise"},
    "how-to": {"steps"},
    "troubleshooting": {"steps", "mechanism_chain"},
    "comparison": {"criterion_comparison"},
    "causal": {"mechanism_chain"},
    "current-events": {"thematic", "concise"},
    "broad-research": {"thematic"},
    "literature-review": {"thematic"},
    "decision-support": {"options"},
    "technical-investigation": {"mechanism_chain", "thematic", "steps"},
    "forecasting": {"thematic", "concise"},
    "data-analysis": {"thematic", "concise", "mechanism_chain"},
    "ambiguous": {"disambiguation", "thematic", "concise"},
}


@dataclass
class Case:
    id: str
    category: str
    query: str
    answer: str
    # Whether the answer should be judged as process-clean.
    expect_clean: bool = True
    facts: List[Dict[str, Any]] = field(default_factory=list)


def _fact(claim: str, url: str) -> Dict[str, Any]:
    return {"claim": claim, "source": url, "verified": True, "confidence": 0.8}


def cases() -> List[Case]:
    """~20 representative prompts across the required categories."""
    crisp = "AI capex reached 300 billion dollars in 2025 [1], up sharply from 2024 [2]."
    return [
        Case("factual-1", "factual", "What is the capital of France?",
             "Paris is the capital and largest city of France. [1]",
             facts=[_fact("Paris is the capital of France.", "https://britannica.com/france")]),
        Case("definition-1", "definition", "What is CRISPR?",
             "CRISPR is a gene-editing technique that lets scientists cut and "
             "modify DNA at a chosen sequence [1]. It works by guiding a Cas "
             "protein to a matching stretch of DNA, where it makes a cut [1].",
             facts=[_fact("CRISPR is a gene-editing technique.", "https://nature.com/crispr")]),
        Case("explanation-1", "explanation", "How does retrieval augmented generation work?",
             "RAG works by retrieving relevant documents before generation [1]. "
             "Because the model conditions on retrieved text, outputs are grounded "
             "in sources rather than parametric memory alone [2].",
             facts=[_fact("RAG retrieves documents before generation.", "https://arxiv.org/rag"),
                    _fact("RAG grounds outputs in sources.", "https://acm.org/rag")]),
        Case("howto-1", "how-to", "How do I configure a Python virtual environment?",
             "1. Create it with python3 -m venv .venv [1].\n"
             "2. Activate it with source .venv/bin/activate [1].\n"
             "3. Install dependencies with pip install -r requirements.txt [2].\n"
             "If activation fails, check your shell's execution policy [2].",
             facts=[_fact("Python venv creates isolated environments.", "https://docs.python.org/venv"),
                    _fact("pip installs from a requirements file.", "https://pip.pypa.io/req")]),
        Case("troubleshooting-1", "troubleshooting", "Why is my build failing with a memory error?",
             "1. Check the failing step's heap limit [1].\n"
             "2. Raise NODE_OPTIONS to increase the heap [1].\n"
             "The error usually occurs because the bundler exceeds the default ceiling [2].",
             facts=[_fact("Node builds fail when the heap is exceeded.", "https://nodejs.org/mem"),
                    _fact("NODE_OPTIONS raises the Node heap.", "https://nodejs.org/options")]),
        Case("comparison-1", "comparison", "Compare React and Vue.",
             "The two differ mainly in structure. React uses JSX and leaves more "
             "decisions to the developer, whereas Vue provides template syntax and "
             "built-in state primitives [1]. React has the larger ecosystem [2].",
             facts=[_fact("React uses JSX.", "https://react.dev"), _fact("Vue uses templates.", "https://vuejs.org")]),
        Case("causal-1", "causal", "Why did AI capital expenditure rise in 2025?",
             "The rise was driven by compute demand: model scaling pushed training "
             "costs up [1], and hyperscalers responded by expanding data-centre "
             "capacity [2]. A competing explanation points to cheap capital [3].",
             facts=[_fact("Model scaling raised training costs.", "https://arxiv.org/scale"),
                    _fact("Hyperscalers expanded capacity.", "https://reuters.com/dc"),
                    _fact("Cheap capital funded expansion.", "https://ft.com/ai")]),
        Case("current-1", "current-events", "What is the latest in AI regulation?",
             "Regulators advanced several frameworks in 2025 [1]. The EU moved "
             "first with model-transparency rules [2], while the US relied more on "
             "agency guidance [3].",
             facts=[_fact("The EU advanced model-transparency rules.", "https://europa.eu/ai"),
                    _fact("The US relied on agency guidance.", "https://whitehouse.gov/ai")]),
        Case("broad-1", "broad-research", "What are the current trends in AI as of 2026?",
             "AI's centre of gravity shifted to infrastructure and governance [1].\n\n"
             "## Compute\nThe build-out is the defining change [1][2].\n\n"
             "## Governance\nRule-making caught up with deployment [3].",
             facts=[_fact(crisp, "https://a.example"), _fact("Governance advanced.", "https://b.example")]),
        Case("lit-1", "literature-review", "Review the literature on sleep and memory.",
             "The literature converges on a consolidation account [1].\n\n"
             "## Behavioural evidence\nRecall improves after sleep [1].\n\n"
             "## Neural evidence\nHippocampal replay accompanies consolidation [2].",
             facts=[_fact("Sleep improves recall.", "https://pubmed.ncbi.nlm.nih.gov/1"),
                    _fact("Hippocampal replay accompanies consolidation.", "https://nature.com/neuro")]),
        Case("decision-1", "decision-support", "Should a startup adopt Kubernetes?",
             "Option A — adopt now: it scales cleanly, but the operational cost is "
             "real [1]. Option B — defer until you have a platform team: if you "
             "value speed over scale today, this is the better trade-off [2]. "
             "Recommendation: defer below ~20 services [2].",
             facts=[_fact("Kubernetes scales cleanly.", "https://kubernetes.io"),
                    _fact("Kubernetes needs operational investment.", "https://cncf.io/report")]),
        Case("tech-1", "technical-investigation", "Investigate rising p99 latency in a web service.",
             "The spike is caused by lock contention in the connection pool [1]. "
             "Under load, threads queue on the pool, which raises tail latency [2].",
             facts=[_fact("Connection-pool lock contention raises latency.", "https://sre.google/latency"),
                    _fact("Thread queues raise tail latency.", "https://acm.org/queue")]),
        Case("forecast-1", "forecasting", "What will AI spending be by 2027?",
             "Spending stood at 300 billion dollars in 2025 [1]. Projections for "
             "2027 range from 500 to 700 billion, depending on the assumed adoption "
             "rate [2]; this is a projection, not an observation.",
             facts=[_fact("AI spending was 300bn in 2025.", "https://idc.com/ai"),
                    _fact("2027 AI spending projections vary.", "https://gartner.com/ai")]),
        Case("data-1", "data-analysis", "Analyze the trend in solar capacity.",
             "Solar capacity additions grew quickly through the period [1]. "
             "The growth is driven by falling module costs [2].",
             facts=[_fact("Solar capacity additions grew.", "https://iea.org/solar"),
                    _fact("Falling module costs drove growth.", "https://irena.org")]),
        Case("ambiguous-1", "ambiguous", "What is a transformer?",
             "1) **Transformer neural network architecture** — an attention-based "
             "deep learning model [1].\n"
             "2) **Electrical transformer** — a device that changes AC voltage [2].\n"
             "This answer addresses meaning 1 [1].",
             facts=[_fact("Transformers use attention.", "https://arxiv.org/attention"),
                    _fact("Electrical transformers change voltage.", "https://ieee.org/x")]),
    ]


def score_case(case: Case) -> Dict[str, Any]:
    shape = detect_shape(case.answer)
    expected_shapes = _CATEGORY_SHAPES.get(case.category, set())
    adaptive = shape in expected_shapes if expected_shapes else True
    process_hits = len(_PROCESS_NOISE_RE.findall(case.answer))
    relevance = score_answer_relevance(case.query, case.answer)
    markers = [int(m) for m in re.findall(r"\[(\d+)\]", case.answer)]
    return {
        "id": case.id,
        "category": case.category,
        "shape": shape,
        "expected_shapes": sorted(expected_shapes),
        "adaptive": adaptive,
        "process_noise": process_hits,
        "process_clean": process_hits == 0,
        "answer_relevance": round(relevance, 3),
        "citation_count": len(markers),
    }


def aggregate(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(results) or 1
    return {
        "cases": len(results),
        "adaptability_rate": round(sum(1 for r in results if r["adaptive"]) / n, 3),
        "process_clean_rate": round(sum(1 for r in results if r["process_clean"]) / n, 3),
        "citation_rate": round(sum(1 for r in results if r["citation_count"] > 0) / n, 3),
        "mean_relevance": round(sum(r["answer_relevance"] for r in results) / n, 3),
    }


# Floors: calibrated to the intended behaviour, not to a baseline of the old
# rigid system. These are the properties the rework must hold.
_FLOORS = {
    "adaptability_rate": 0.90,
    "process_clean_rate": 1.0,
    "citation_rate": 0.85,
    "mean_relevance": 0.25,
}


def run(json_out: bool = False) -> int:
    results = [score_case(c) for c in cases()]
    agg = aggregate(results)
    failures = [
        {"metric": k, "actual": agg[k], "floor": v}
        for k, v in _FLOORS.items() if agg[k] < v
    ]
    if json_out:
        print(json.dumps({"aggregate": agg, "results": results, "failures": failures}, indent=2))
    else:
        print("Answer-quality benchmark (adaptive synthesis)")
        print("=" * 52)
        for r in results:
            flag = "ok " if r["adaptive"] else "SHAPE"
            clean = "" if r["process_clean"] else f"  NOISE={r['process_noise']}"
            print(f"  {r['id']:<22} {r['category']:<22} {flag} {r['shape']} "
                  f"rel={r['answer_relevance']:.2f}{clean}")
        print("-" * 52)
        for k, v in agg.items():
            print(f"  {k:<20} {v}")
        if failures:
            print("\nFAILED FLOORS:")
            for f in failures:
                print(f"  {f['metric']}: {f['actual']} < {f['floor']}")
        else:
            print("\nAll answer-quality floors passed.")
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# PIPELINE HALF (--pipeline): run representative queries through the REAL
# production graph with the deterministic offline mocks and score the DELIVERED
# answer with the same metrics. This is the half that can catch a pipeline
# regression (a wrong plan, a lost subject, a degraded fallback) that scoring
# hand-written candidate answers never can — the synthetic half always passes
# because its candidates were written to pass.
# ---------------------------------------------------------------------------

# Representative queries across the materially different categories the
# scripted offline fixtures cover. Each entry: (golden_id, category, query,
# expected_shapes). These run the FULL production graph.
_PIPELINE_QUERIES: List[tuple] = [
    ("factual-rag", "factual",
     "What is retrieval augmented generation and how does it work?",
     {"concise", "thematic", "mechanism_chain"}),
    ("comparison-nuclear-solar", "comparison",
     "Compare nuclear and solar energy for grid reliability and cost.",
     {"criterion_comparison"}),
    ("decision-nuclear-investment", "decision-support",
     "Should Bangladesh expand nuclear energy investment over the next twenty years?",
     {"options", "thematic"}),
]

# Planning-shape queries checked DIRECTLY against the real intent→plan path (no
# mock), on the defect this benchmark guards: a modifier term must not replace
# the query's actual subject in the plan. These do not need the graph or the
# scripted fixtures — the defect lives entirely in intent classification, the
# ambiguity policy and the plan, all of which are deterministic.
_PLAN_SUBJECT_QUERIES: List[tuple] = [
    ("plan-rec-1", "recommendation",
     "Suggest me highly demanding research topics in computer science.",
     ["computer", "science"]),
    ("plan-rec-2", "recommendation",
     "Recommend demanding MSc research topics in computer science.",
     ["computer", "science"]),
    ("plan-rec-3", "recommendation",
     "Recommend the best programming language to learn.",
     ["programming", "language"]),
    ("plan-decision", "decision",
     "Should we invest in nuclear energy for our grid?",
     ["nuclear", "energy"]),
]


def run_plan_subject_checks() -> List[Dict[str, Any]]:
    """Deterministic intent→plan check: does the plan keep the query's subject?

    Runs the REAL heuristic intent + ambiguity policy + fallback plan (the
    degraded path, where the live defect was observed). A modifier term such as
    "demanding" must never become the plan's subject. No mock, no network.
    """
    from app.agents.ambiguity import decide_ambiguity
    from app.agents.intent import heuristic_intent
    from app.agents.planning.plan import fallback_plan

    out: List[Dict[str, Any]] = []
    for qid, category, query, subject_terms in _PLAN_SUBJECT_QUERIES:
        intent = heuristic_intent(query).to_dict()
        policy = decide_ambiguity(query, intent)
        intent["ambiguity_policy"] = policy.to_dict()
        plan = fallback_plan(query, target_count=4, intent=intent)
        subjects_blob = " ".join(
            str(c.get("question", "") or "").lower() for c in plan
        )
        kept = all(t in subjects_blob for t in subject_terms)
        # A reading label (parenthetical stripped, as the planner renders it)
        # must NEVER lead a planned question: that is the subject being replaced
        # by a modifier-term interpretation. This is the exact pre-fix shape
        # ("stressful or difficult …" instead of "computer science …").
        readings = [
            re.sub(r"\s*\([^)]*\)", "", str(label)).strip().lower()
            for label in (policy.interpretations or [])
        ]
        leaked = [
            r for r in readings
            if r and any(
                str(c.get("question", "") or "").lower().startswith(r)
                for c in plan
            )
        ]
        out.append({
            "id": qid,
            "category": category,
            "query": query,
            "subject_terms": subject_terms,
            "subject_kept": kept,
            "interpretation_leaked": leaked,
            "planned_questions": [str(c.get("question", ""))[:80] for c in plan][:4],
            "ambiguity_action": policy.action,
        })
    return out


async def run_pipeline(json_out: bool = False) -> int:
    """Run the representative set through the production graph (offline mocks).

    Exit 0 when every aggregate pipeline floor passes, 1 on regression.
    """
    import asyncio  # noqa: F401  (kept for the async runner below)
    import tempfile

    from app.agents import citation_check
    from app.core import llm_cache
    from app.core.config import Settings
    from app.core.degradation import reset_fallbacks, take_fallbacks
    from app.core.usage import clear_run_usage, start_run_usage
    from app.graph.workflow import (
        build_initial_state,
        create_workflow,
        graph_recursion_limit,
    )
    from bench.mock_pipeline import GoldenFakeLLM, GoldenFakeSearch

    results: List[Dict[str, Any]] = []

    async def _one(qid: str, category: str, query: str, shapes: set) -> Dict[str, Any]:
        tmp = tempfile.mkdtemp(prefix="deepscout-aq-pipe-")
        settings = Settings(
            groq_api_key="pipeline-offline",
            database_url=f"{tmp}/aq.db",
            _env_file=None,
        )
        llm_cache._force_disabled = True

        async def _offline_citations(answer, answer_support, **kwargs):
            return {"checked": 0, "sources": [], "summary": {}, "enabled": False}

        original = citation_check.check_citations
        citation_check.check_citations = _offline_citations
        reset_fallbacks()
        llm = GoldenFakeLLM(settings, query_id=qid, query=query,
                            critic_pass_on_iteration=1)
        search = GoldenFakeSearch(settings, query=query)
        workflow = create_workflow(llm, search_client=search)
        state = build_initial_state(query, settings.max_iterations, mode="standard")
        usage = start_run_usage(f"aq-{qid}", settings, mode="standard")
        final: Dict[str, Any] = dict(state)
        try:
            async for snap in workflow.astream(
                state, stream_mode="values",
                config={"recursion_limit": graph_recursion_limit(state)},
            ):
                final = {**final, **{k: v for k, v in snap.items() if v}}
        finally:
            clear_run_usage()
            citation_check.check_citations = original
        degraded = take_fallbacks()

        answer = str(final.get("synthesized_answer", "") or "")
        report = str(final.get("final_report", "") or "") or answer
        delivered = report
        plan = [q for q in (final.get("sub_questions") or []) if isinstance(q, dict)]
        sub_questions = [q for q in (final.get("sub_questions") or []) if isinstance(q, dict)]
        facts = [f for f in (final.get("facts") or []) if isinstance(f, dict)]

        # SUBJECT PRESERVATION — the exact regression this benchmark exists to
        # catch: a modifier term (e.g. "demanding") must not replace the query's
        # actual subject in the plan. Measured as overlap between the query's own
        # content words and the planned questions.
        q_tokens = {
            w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2
            and w not in {"the", "and", "for", "what", "how", "does", "suggest",
                          "recommend", "compare", "should", "me", "some", "highly",
                          "demanding", "topics", "research"}
        }
        plan_blob = " ".join(
            str(q.get("question", "") or "").lower() for q in plan
        )
        subject_terms = [t for t in q_tokens if t in ("computer", "science", "msc")]
        subject_kept = all(t in plan_blob for t in subject_terms) if subject_terms else True

        shape = detect_shape(delivered)
        adaptive = shape in shapes
        noise = len(_PROCESS_NOISE_RE.findall(delivered))
        relevance = score_answer_relevance(query, answer or delivered)
        return {
            "id": qid,
            "category": category,
            "shape": shape,
            "adaptive": adaptive,
            "process_noise": noise,
            "process_clean": noise == 0,
            "answer_relevance": round(relevance, 3),
            "subject_kept": subject_kept,
            "subject_terms": subject_terms,
            "planned_questions": [str(q.get("question", ""))[:80] for q in plan][:6],
            "degraded": list(degraded),
            "answer_chars": len(delivered),
            "facts": len(facts),
        }

    for qid, category, query, shapes in _PIPELINE_QUERIES:
        results.append(await _one(qid, category, query, shapes))

    plan_checks = run_plan_subject_checks()

    n = len(results) or 1
    pn = len(plan_checks) or 1
    agg = {
        "cases": len(results),
        "subject_kept_rate": round(sum(1 for r in results if r["subject_kept"]) / n, 3),
        "adaptability_rate": round(sum(1 for r in results if r["adaptive"]) / n, 3),
        "process_clean_rate": round(sum(1 for r in results if r["process_clean"]) / n, 3),
        "mean_relevance": round(sum(r["answer_relevance"] for r in results) / n, 3),
        "plan_cases": len(plan_checks),
        "plan_subject_kept_rate": round(
            sum(1 for r in plan_checks if r["subject_kept"]) / pn, 3),
        "plan_no_interpretation_leak_rate": round(
            sum(1 for r in plan_checks if not r["interpretation_leaked"]) / pn, 3),
    }
    floors = {
        # Every representative query must keep its subject in the plan: a plan
        # that lost the subject cannot retrieve on-topic evidence.
        "subject_kept_rate": 1.0,
        "adaptability_rate": 0.60,
        "process_clean_rate": 1.0,
        "mean_relevance": 0.10,
        # The regression this benchmark was extended to catch: the plan must
        # keep the query's subject and must never adopt a modifier-term
        # interpretation as its subject.
        "plan_subject_kept_rate": 1.0,
        "plan_no_interpretation_leak_rate": 1.0,
    }
    failures = [
        {"metric": k, "actual": agg[k], "floor": v}
        for k, v in floors.items() if agg[k] < v
    ]
    if json_out:
        print(json.dumps({"aggregate": agg, "results": results,
                          "plan_checks": plan_checks,
                          "failures": failures}, indent=2))
    else:
        print("Answer-quality benchmark (PIPELINE, production graph + mocks)")
        print("=" * 60)
        for r in results:
            flag = "ok " if r["adaptive"] else "SHAPE"
            kept = "" if r["subject_kept"] else "  SUBJECT-LOST"
            clean = "" if r["process_clean"] else f"  NOISE={r['process_noise']}"
            print(f"  {r['id']:<24} {r['category']:<16} {flag} {r['shape']} "
                  f"rel={r['answer_relevance']:.2f}{kept}{clean}")
        print("-" * 60)
        print("  Planning subject-preservation (real intent + plan, no mock):")
        for r in plan_checks:
            kept = "ok " if r["subject_kept"] else "SUBJECT-LOST"
            leak = ("  LEAK=" + ",".join(r["interpretation_leaked"])
                    if r["interpretation_leaked"] else "")
            print(f"  {r['id']:<24} {r['category']:<16} {kept} "
                  f"action={r['ambiguity_action']}{leak}")
        print("-" * 60)
        for k, v in agg.items():
            print(f"  {k:<32} {v}")
        if failures:
            print("\nFAILED FLOORS:")
            for f in failures:
                print(f"  {f['metric']}: {f['actual']} < {f['floor']}")
        else:
            print("\nAll pipeline answer-quality floors passed.")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--pipeline", action="store_true",
                        help="also run representative queries through the production graph")
    args = parser.parse_args()
    if args.pipeline:
        import asyncio

        return asyncio.run(run_pipeline(json_out=args.json))
    return run(json_out=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
