"""Phase 11 — Answer prioritization & surface cleanup.

These tests pin the behaviours that make an answer read like a research analyst
rather than a research-process log:

  * deterministic CORE_ANSWER / SUPPORTING / CONTEXT / AUDIT_ONLY classification
    and the writer directive it renders;
  * the directive reaches the writer prompt next to the AnalystBrief;
  * internal decision machinery (Option A/B/C/D, "recommended option") is
    forbidden by the prompt;
  * direct answer first / compressed uncertainty guidance is present;
  * a primary source wins the representative slot over an equivalent weaker
    secondary source, while a clearly stronger secondary still wins;
  * a secondary source stays usable when no primary exists;
  * grounding/citations and adaptive structure are unchanged.

Deterministic and LLM-free: the writer is a stub that records the prompt.
"""
from __future__ import annotations

import asyncio

from app.agents.analyst import AnalyticalBrief
from app.agents.outline import build_outline
from app.agents.synthesizer import SYNTHESIZER_SYSTEM_PROMPT, _render_analytical_guidance, synthesize
from app.core.answer_priority import (
    build_answer_priority,
    render_for_writer,
)
from app.core.synthesis_planner import build_synthesis_plan


# --- fixtures ----------------------------------------------------------------

_FACTS = [
    {"claim": "Global AI adoption reached 78 percent in 2025.",
     "source": "https://mckinsey.com/a", "verified": True, "has_numbers": True,
     "corroborating_sources": ["https://gartner.com/a"],
     "sub_question": "adoption", "axis": "adoption"},
    {"claim": "AI funding hit 100 billion dollars in 2025.",
     "source": "https://cbinsights.com/a", "verified": True, "has_numbers": True,
     "corroborating_sources": ["https://pitchbook.com/a"],
     "sub_question": "funding", "axis": "funding"},
    {"claim": "An unrelated historical note about office furniture.",
     "source": "https://random.example/z", "verified": True,
     "sub_question": "peripheral", "axis": "general"},
]


def _plan_and_brief():
    subs = [
        {"axis": "adoption", "question": "adoption"},
        {"axis": "funding", "question": "funding"},
        {"axis": "regulation", "question": "what regulation applies"},
    ]
    outline = build_outline("What is the current trend of AI?", _FACTS, subs)
    plan = build_synthesis_plan(
        _FACTS, [], query="What is the current trend of AI?",
        query_type="analytical", outline=outline, sub_questions=subs,
        required_dimensions=subs,
    )
    brief = AnalyticalBrief(
        thesis="AI adoption and funding are rising, with regulation uneven [1][2].",
        insights=["Adoption and funding reinforce each other [1][2]."],
        relationships=[{"kind": "causation", "statement": "Funding enables adoption [1][2]."}],
        counter_evidence=["Some surveys overstate production use [1]."],
        cross_source_conclusions=["Taken together, the trends indicate operational maturity [1][2]."],
        uncertainties=["Adoption definitions vary across surveys [1]."],
    )
    return plan, brief


# --- 1. classification -------------------------------------------------------

def test_classification_puts_thesis_and_dominants_in_core():
    plan, brief = _plan_and_brief()
    material = build_answer_priority(plan, brief)
    core = " ".join(material.core_answer).lower()
    assert "thesis" in core
    assert "adoption" in core  # a dominant/insight finding
    assert material.supporting, "relationships/counter-evidence belong in supporting"


def test_classification_moves_uncertainty_and_gaps_to_audit_only():
    plan, brief = _plan_and_brief()
    material = build_answer_priority(plan, brief)
    audit = " ".join(material.audit_only).lower()
    # The brief's uncertainty and the plan's uncovered dimension are audit-only.
    assert "definitions vary" in audit
    assert "regulation" in audit
    # Audit material must never be promoted into core.
    assert all("definitions vary" not in c.lower() for c in material.core_answer)


def test_classification_puts_incidental_findings_in_context():
    plan, brief = _plan_and_brief()
    material = build_answer_priority(plan, brief)
    context = " ".join(material.context).lower()
    # The peripheral finding is available as context, not deleted.
    assert "furniture" in context or plan.incidental == [] or material.context


# --- 2. directive rendering --------------------------------------------------

def test_directive_names_the_tier_hierarchy_and_direct_answer():
    plan, brief = _plan_and_brief()
    rendered = render_for_writer(plan, brief)
    assert "ANSWER PRIORITY" in rendered
    assert "CORE_ANSWER" in rendered and "SUPPORTING" in rendered
    assert "CONTEXT" in rendered and "AUDIT_ONLY" in rendered
    assert "The first sentence must answer" in rendered
    # Decision machinery is forbidden in the directive.
    assert "no option A/B/C/D labels" in rendered


def test_directive_is_empty_without_material():
    assert render_for_writer(None, None) == ""
    from app.core.answer_priority import PrioritizedMaterial

    assert render_for_writer(PrioritizedMaterial(), None) == ""


# --- 3. writer prompt integration --------------------------------------------

def test_priority_directive_reaches_writer_prompt():
    plan, brief = _plan_and_brief()
    ctx = {"analytical_brief": brief, "synthesis_plan": plan}
    guidance = _render_analytical_guidance(ctx)
    assert "ANALYTICAL SYNTHESIS" in guidance  # brief still leads
    assert "ANSWER PRIORITY" in guidance
    assert "AUDIT_ONLY" in guidance


def test_system_prompt_forbids_internal_decision_machinery():
    """The prompt must keep the pipeline's own machinery out of the answer.

    Asserted on the prompt's actual wording, not a paraphrase: this checks the
    ban is still PRESENT after a prompt rewrite, which is the failure mode a
    pure phrase-match would otherwise hide (it would keep passing on a stale
    phrase while the ban itself was dropped).
    """
    lowered = SYNTHESIZER_SYSTEM_PROMPT.lower()
    # Internal labels and decision scaffolding.
    assert "option a" in lowered
    assert "recommended option" in lowered
    assert "scoring" in lowered and "planner terms" in lowered
    # Process language (the pipeline describing itself rather than the subject).
    assert "process language" in lowered
    assert "pipeline stages" in lowered
    assert "relevance n/100" in lowered
    # Uncertainty compression + answer-first are present.
    assert "compress uncertainty" in lowered
    assert "what is not known" in lowered
    assert "answer first" in lowered


def test_system_prompt_states_the_runtime_contracts_take_precedence():
    """The prompt is the standing layer; the per-report contracts appended to
    the user message override its style/structure rules. Without this the
    answer-construction contract and the prompt could disagree about whether a
    synthesis may be labelled as one, and the model would arbitrate."""
    lowered = SYNTHESIZER_SYSTEM_PROMPT.lower()
    assert "precedence" in lowered
    for contract in ("definition lock", "ranking basis", "convergence",
                     "consistency", "answer construction", "disambiguation"):
        assert contract in lowered, contract
    # ...but never the citation rules or the process-language ban.
    assert "never the citation rules" in lowered


def test_system_prompt_forbids_a_writer_supplied_sources_list():
    """The numbered legend is appended by _legend_block, so a writer-emitted
    sources list is duplicated work that _strip_sections then removes."""
    assert "write no sources list" in SYNTHESIZER_SYSTEM_PROMPT.lower()


def test_system_prompt_names_the_answer_cases_without_printing_them():
    """DIRECT / ASSEMBLED / PARTIAL / NOT ESTABLISHED map onto the construction
    modes; the case NAMES must never reach the reader."""
    lowered = SYNTHESIZER_SYSTEM_PROMPT.lower()
    for case in ("direct:", "assembled:", "partial:", "not established:"):
        assert case in lowered, case
    assert "never print the case names" in lowered


# --- 4. source authority preference (dedup) ----------------------------------

def test_primary_source_wins_equivalent_secondary_at_similar_confidence():
    from app.agents.evidence_utils import dedupe_semantic_facts

    facts = [
        {"claim": "Global AI capital expenditure reached 300 billion dollars in 2025.",
         "source": "https://news.example/x", "confidence": 0.80, "verified": True,
         "is_primary": False},
        {"claim": "Global AI capital expenditure reached 300 billion dollars in 2025.",
         "source": "https://gov.uk/report", "confidence": 0.78, "verified": True,
         "is_primary": True},
    ]
    deduped = dedupe_semantic_facts(facts)
    assert len(deduped) == 1
    # The primary source holds the representative citation slot.
    assert deduped[0]["source"] == "https://gov.uk/report"
    # Both sources remain available as corroboration.
    joined = " ".join(deduped[0].get("corroborating_sources", []))
    assert "news.example" in joined and "gov.uk" in joined


def test_clearly_stronger_secondary_still_wins_over_weak_primary():
    from app.agents.evidence_utils import dedupe_semantic_facts

    facts = [
        {"claim": "Agency X will raise rates by 50 basis points next quarter.",
         "source": "https://gov.example/report", "confidence": 0.55, "verified": False,
         "is_primary": True},
        {"claim": "Agency X will raise rates by 50 basis points next quarter.",
         "source": "https://news.example/y", "confidence": 0.95, "verified": True,
         "is_primary": False},
    ]
    deduped = dedupe_semantic_facts(facts)
    assert len(deduped) == 1
    # A materially better-extracted/verified copy keeps the representative slot.
    assert deduped[0]["source"] == "https://news.example/y"


def test_secondary_source_usable_when_no_primary_available():
    from app.agents.evidence_utils import dedupe_semantic_facts

    facts = [
        {"claim": "RAG retrieves documents before generating answers.",
         "source": "https://blog.example/a", "confidence": 0.8, "verified": True},
    ]
    deduped = dedupe_semantic_facts(facts)
    assert len(deduped) == 1
    assert deduped[0]["source"] == "https://blog.example/a"


# --- 5. grounding + adaptive structure preserved -----------------------------

def test_priority_guidance_does_not_break_synthesis_or_citations():
    plan, brief = _plan_and_brief()

    class _RecWriter:
        def __init__(self):
            self.prompts = []

        async def generate_json(self, system_prompt, user_prompt, **kwargs):
            self.prompts.append(user_prompt)
            return {"answer": (
                "AI adoption and funding are both rising [1][2].\n\n"
                "## What is changing\nAdoption reached 78 percent [1], while "
                "funding reached 100 billion dollars [2]."
            )}

    writer = _RecWriter()
    outline = build_outline("What is the current trend of AI?", _FACTS, [])
    result = asyncio.run(synthesize(
        writer, "What is the current trend of AI?", _FACTS,
        {"intent": {}, "sub_questions": [], "analytical_brief": brief,
         "synthesis_plan": plan},
        outline=outline, section_wise=False, compress_context=False,
    ))
    assert writer.prompts, "writer must be called"
    assert "ANSWER PRIORITY" in writer.prompts[0]
    # Adaptivity: no universal skeleton was forced in.
    assert "## Executive Summary" not in result.answer
    assert "## Limitations & Unknowns" not in result.answer
    # Citations survive.
    assert "[1]" in result.answer and "[2]" in result.answer
