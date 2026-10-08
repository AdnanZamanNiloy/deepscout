"""Citation numbering, evidence rendering and the post-generation audit.

Extracted verbatim from `app/agents/synthesizer.py` (refactor; no behaviour
change). Binds every fact to the number the model must cite, renders the
evidence/source/legend blocks and pre-computed conflict ranges, and measures
the finished draft's traceability (`audit_citations`).

`synthesizer.py` re-exports every name here, so the existing import surface
(tests included) is unchanged."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Dict, List, Sequence, Set, Tuple

if TYPE_CHECKING:
    from app.agents.epistemics import EpistemicReport

from app.core.logging import get_logger
from app.agents.contradiction import numeric_ranges
from app.agents.evidence_utils import (
    extract_domain,
    extract_numbers,
    semantic_similarity,
    split_into_sentences,
)
from app.agents.research_quality import is_factual_sentence
from app.agents.sources import canonical_url, classify_source

from app.agents.synthesis.primitives import (
    _ANALYSIS_LEAD_RE,
    _DISAMBIG_LINE_RE,
    _TRIVIAL_NUMBERS,
    _corroboration,
    _safe_int,
)
from app.agents.synthesis.types import CitationAudit

logger = get_logger(__name__)


MAX_LEGEND_SOURCES = 14

# Hard ceiling on the legend. The old cap of 14 was applied to the LEGEND while
# up to 40 facts were selected, so every fact from source 15 onwards was
# silently discarded — on a broad query with 30 domains that is most of the
# evidence, and in the section-wise path it emptied sections and collapsed the
# whole path. A legend line costs ~15 tokens; discarding verified evidence
# costs the answer. The legend sizes itself to the sources actually present,
# bounded generously; the ceiling is overridable via
# `synthesis_max_legend_sources` (a large-context model can cite a wide pool).
MAX_LEGEND_SOURCES_HARD = 90

# Caps tried in order when the provider rejects the request size.
_FACT_CAP_LADDER = (80, 40, 24, 14)


def _legend_ceiling() -> int:
    """Hard legend ceiling, from settings when available, else the constant."""
    try:
        from app.core.config import get_settings

        return int(
            getattr(get_settings(), "synthesis_max_legend_sources", MAX_LEGEND_SOURCES_HARD)
            or MAX_LEGEND_SOURCES_HARD
        )
    except Exception:
        return MAX_LEGEND_SOURCES_HARD


def _legend_budget(facts: Sequence[Dict[str, Any]]) -> int:
    """Legend size for this fact set: enough to cite every fact, within reason."""
    documents = {
        canonical_url(str(f.get("source", "") or "")) or str(f.get("source", "") or "")
        for f in facts
        if f.get("source")
    }
    documents.discard("")
    return max(MAX_LEGEND_SOURCES, min(_legend_ceiling(), len(documents)))


def _number_facts(
    top_facts: Sequence[Dict[str, Any]], max_sources: int | None = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Number the sources and stamp each fact with the number that cites it.

    Grouping is by CANONICAL url, so `page?utm_source=x` and `page` are one
    source with one number instead of two entries the model may cite
    inconsistently. Facts whose source cannot make the legend are not sent to
    the model at all — handing the writer evidence it has no legal way to cite
    makes it either drop a real finding or invent a marker — but the legend is
    now sized so that discarding is rare rather than routine, and any discard
    is logged.
    """
    budget = max_sources if max_sources is not None else _legend_budget(top_facts)
    numbered, pairs = _assign_numbers(top_facts, budget)
    cited: List[Dict[str, Any]] = []
    for fact, index in pairs:
        item = dict(fact)
        item["citation"] = index
        cited.append(item)
    dropped = len([f for f in top_facts if f.get("source")]) - len(cited)
    if dropped > 0:
        logger.info(
            "[Synthesizer] %d fact(s) dropped: their source exceeded the %d-entry legend",
            dropped, budget,
        )
    return numbered, cited


def _assign_numbers(
    facts: Sequence[Dict[str, Any]], max_sources: int | None = None
) -> Tuple[List[Dict[str, Any]], List[Tuple[Dict[str, Any], int]]]:
    """Legend entries plus the (fact, number) pairs, keeping fact identity.

    Identity matters for the extractive path and the section-wise path, which
    need to attach the right marker to a claim they have already selected.
    """
    budget = max_sources if max_sources is not None else _legend_budget(facts)
    numbered: List[Dict[str, Any]] = []
    number_by_document: Dict[str, int] = {}
    pairs: List[Tuple[Dict[str, Any], int]] = []

    for fact in facts or []:
        url = str(fact.get("source", "") or "")
        if not url:
            continue
        document = canonical_url(url) or url
        index = number_by_document.get(document)
        if index is None:
            if len(numbered) >= max(1, budget):
                continue
            profile = classify_source(url)
            index = len(numbered) + 1
            number_by_document[document] = index
            numbered.append(
                {
                    "n": index,
                    "domain": profile.domain or extract_domain(url) or url or "unknown source",
                    "url": url,
                    "tier": profile.tier,
                    "authority": round(profile.authority, 2),
                    "primary": bool(profile.is_primary),
                }
            )
        pairs.append((fact, index))

    return numbered, pairs


def _render_evidence_block(cited_facts: Sequence[Dict[str, Any]], limit: int = 40) -> str:
    """One line per claim, citation number first.

    Sending raw dicts spends tokens on keys the writer cannot use and buries the
    attribution the writer needs most.
    """
    lines: List[str] = []
    for fact in list(cited_facts)[:limit]:
        claim = re.sub(r"\s+", " ", str(fact.get("claim", "") or "")).strip()
        if not claim:
            continue
        marks: List[str] = []
        if fact.get("is_primary"):
            marks.append("primary")
        if fact.get("verified") is True:
            marks.append("verified")
        corroboration = _corroboration(fact)
        if corroboration > 1:
            marks.append(f"{corroboration} sources agree")
        if fact.get("direct_quote"):
            marks.append("direct quote")
        angle = str(fact.get("sub_question", "") or "").strip()
        meta = f" ({', '.join(marks)})" if marks else ""
        angle_tag = f" [angle: {angle}]" if angle else ""
        sense = str(fact.get("sense", "") or "").strip()
        sense_tag = f" [sense: {sense}]" if sense else ""
        lines.append(f"[{fact.get('citation')}] {claim}{meta}{angle_tag}{sense_tag}")
    return "\n".join(lines)


def _render_source_excerpts(
    cited_facts: Sequence[Dict[str, Any]],
    source_excerpts: Dict[str, str] | None,
    limit: int = 8,
) -> str:
    """Primary material for the writer: a bounded excerpt per cited source.

    The claim list is distilled evidence; this is what the sources actually
    say. Only the sources the cited facts point at are shown, and each excerpt
    is already bounded upstream, so context cost is controlled. Numbered by the
    SAME citation markers as the claims, so the writer can attribute a quote.
    """
    if not source_excerpts:
        return ""
    # Map source URL -> the citation number(s) that cite it.
    number_by_url: Dict[str, int] = {}
    for fact in cited_facts:
        url = str(fact.get("source", "") or "").strip()
        index = _safe_int(fact.get("citation"), 0)
        if url and index and url not in number_by_url:
            number_by_url[url] = index
    parts: List[str] = []
    seen: Set[int] = set()
    for url, excerpt in source_excerpts.items():
        index = number_by_url.get(url)
        if not index or index in seen:
            continue
        text = re.sub(r"\s+", " ", str(excerpt or "")).strip()
        if not text:
            continue
        seen.add(index)
        parts.append(f"[{index}] {text}")
        if len(parts) >= limit:
            break
    if not parts:
        return ""
    return (
        "PRIMARY SOURCE EXCERPTS — what the cited sources actually say. Use "
        "them to quote precisely, connect mechanisms and qualify claims; cite "
        "the SAME [n] number as above:\n" + "\n\n".join(parts) + "\n\n"
    )


def _source_lines(numbered: Sequence[Dict[str, Any]]) -> str:
    out: List[str] = []
    for source in numbered:
        tier = str(source.get("tier", "") or "")
        label = f"[{source['n']}] {source['domain']}"
        if tier:
            label += f" — {tier}{', primary source' if source.get('primary') else ''}"
        if source.get("url"):
            label += f" ({source['url']})"
        out.append(label)
    return "\n".join(out)


def _apply_adjudication(
    contradictions: Sequence[Dict[str, Any]],
    epistemics: "EpistemicReport",
) -> List[Dict[str, Any]]:
    """Drop non-conflicts; stamp the survivors with their verdict.

    A detected pair that turned out to be a time series, a scope mismatch or a
    unit mismatch is NOT a disagreement, and every downstream renderer treats
    the contradiction list as disagreements: the ranges block turns it into
    "report the range X to Y", the Counterarguments section lists it as a
    dispute, and the confidence engine penalises it. Removing them here fixes
    all three at once, and carrying `resolution_note` through means the ones
    that survive can be reported as adjudicated rather than as open questions.
    """
    if not contradictions:
        return []
    by_pair = {
        (r.claim_a, r.claim_b): r for r in getattr(epistemics, "resolutions", []) or []
    }
    out: List[Dict[str, Any]] = []
    for item in contradictions:
        if not isinstance(item, dict):
            continue
        verdict = by_pair.get(
            (str(item.get("claim_a", "") or ""), str(item.get("claim_b", "") or ""))
        )
        if verdict is None:
            out.append(item)
            continue
        if not verdict.is_real_conflict:
            # Not a disagreement. Reported in the evidence section as what it
            # actually is, never as a conflicting range.
            continue
        entry = dict(item)
        if verdict.resolved:
            entry["resolved"] = True
            entry["resolution"] = verdict.explanation
            entry["resolution_rule"] = verdict.rule
            entry["winner"] = verdict.winner
        out.append(entry)
    return out


def _render_ranges_block(contradictions: Sequence[Dict[str, Any]]) -> str:
    """Pre-computed ranges for numeric conflicts.

    The prompt has always forbidden averaging conflicting numbers but never
    supplied the range to use instead — so the model either picked one or hedged
    vaguely. The arithmetic is done here.
    """
    # An adjudicated conflict has an answer, so offering the writer a range
    # would invite it to hedge something the evidence actually settles.
    open_conflicts = [
        c for c in (contradictions or [])
        if isinstance(c, dict) and not c.get("resolved")
    ]
    ranges = numeric_ranges(open_conflicts)
    resolved = [
        c for c in (contradictions or [])
        if isinstance(c, dict) and c.get("resolved") and c.get("resolution")
    ]
    verdicts = ""
    if resolved:
        lines = [
            f"- Conflict resolved: prefer \"{str(c.get('claim_a' if c.get('winner') == 'a' else 'claim_b', ''))[:120]}\" "
            f"because {c.get('resolution')}. State the resolved figure and note that "
            "a weaker source disagrees; do NOT present it as an open question."
            for c in resolved[:4]
        ]
        verdicts = "Adjudicated source conflicts:\n" + "\n".join(lines) + "\n\n"
    if not ranges:
        return verdicts
    lines: List[str] = []
    for item in ranges[:4]:
        unit = "" if item["unit"] in ("", "dimensionless") else f" {item['unit']}"
        domains = ", ".join(sorted({extract_domain(s) for s in item["sources"] if s})[:4])
        lines.append(
            f"- Sources disagree: report the range {item['low']:g}{unit} to "
            f"{item['high']:g}{unit} (spread {item['spread']:g}{unit}); "
            f"disagreeing sources: {domains or 'multiple'}. Never average these."
        )
    return verdicts + "Conflicting quantities, pre-computed as ranges:\n" + "\n".join(lines) + "\n\n"


def audit_citations(
    answer: str,
    numbered: Sequence[Dict[str, Any]],
    cited_facts: Sequence[Dict[str, Any]],
) -> CitationAudit:
    """Measure the draft's traceability instead of assuming it.

    Checks, in order of consequence:
      1. numbers in the prose that appear in no evidence fact (fabrication),
      2. citation markers pointing outside the legend (unresolvable),
      3. factual sentences with no marker at all (untraceable),
      4. sentences whose cited source has little textual overlap with them
         (probable misattribution).

    The caller strips machine-written sections first: grading the pipeline's own
    deterministic appendix reported holes the writer never made.
    """
    audit = CitationAudit()
    body = _strip_sections(answer, ("## Sources", "## Evidence Integrity", "## Evidence integrity"))
    valid = {_safe_int(s.get("n"), 0) for s in numbered}
    valid.discard(0)
    claims_by_number: Dict[int, List[str]] = {}
    for fact in cited_facts:
        index = _safe_int(fact.get("citation"), 0)
        if not index:
            continue
        claims_by_number.setdefault(index, []).append(str(fact.get("claim", "") or ""))
    evidence_text = " ".join(
        str(f.get("claim", "") or "") + " " + str(f.get("direct_quote", "") or "")
        for f in cited_facts
    )
    evidence_values = {round(q.value, 4) for q in extract_numbers(evidence_text, limit=400)}

    for sentence in _audit_units(body):
        stripped = sentence.lstrip("-* ").strip()
        if not stripped:
            continue
        markers = [int(m) for m in re.findall(r"\[(\d+)\]", stripped)]
        # Named entities, quotations, worded dates and attribution verbs all
        # make a sentence checkable. The old digits-only test let every
        # non-numeric assertion ("Acme acquired Beta") pass uncited.
        carries_fact = is_factual_sentence(stripped)
        analysis_lead = bool(_ANALYSIS_LEAD_RE.match(stripped))
        if not markers and (
            (analysis_lead and not carries_fact) or _DISAMBIG_LINE_RE.match(stripped)
        ):
            # Analysis/transition prose, the report's own scaffolding, and the
            # disambiguation lines are not evidence claims; they belong in
            # neither the numerator nor the denominator of citation density. An
            # analysis opener that nevertheless states a number or a date IS a
            # factual claim — "Overall," must not be an exemption from citing.
            continue
        audit.total_sentences += 1
        if markers:
            audit.cited_sentences += 1
        for marker in markers:
            if marker not in valid and marker not in audit.invalid_markers:
                audit.invalid_markers.append(marker)

        for quantity in extract_numbers(stripped, limit=12):
            value = round(quantity.value, 4)
            if value in _TRIVIAL_NUMBERS and quantity.unit == "":
                continue
            if not _value_grounded(value, evidence_values):
                raw = quantity.raw.strip()
                if raw and raw not in audit.ungrounded_numbers:
                    audit.ungrounded_numbers.append(raw)

        if not markers:
            if carries_fact:
                audit.uncited_factual.append(stripped[:200])
            continue

        # Misattribution check: the cited source should have something to do
        # with the sentence. Uses the max over that source's claims because one
        # sentence may compress several claims from the same page.
        supporting = [c for m in markers for c in claims_by_number.get(m, [])]
        if supporting:
            best = max(semantic_similarity(stripped, claim) for claim in supporting)
            if best < 0.12:
                audit.weakly_supported.append(
                    {"sentence": stripped[:200], "cited": markers, "support": round(best, 3)}
                )

    return audit


def _audit_units(body: str) -> List[str]:
    """Split a markdown report into auditable units.

    Line-aware on purpose. `split_into_sentences` alone splits on terminal
    punctuation, and bullets frequently have none — so a whole bullet list
    collapses into one "sentence" that counts as cited if any single bullet
    carries a marker. Uncited bullets are exactly what this audit exists to
    catch, so each line is split first, then each line into sentences.
    """
    units: List[str] = []
    for raw_line in (body or "").replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        for sentence in split_into_sentences(line, max_sentences=20):
            text = sentence.strip()
            if text:
                units.append(text)
        if len(units) >= 400:
            break
    return units[:400]


# Four-digit integers in the year range are compared exactly: a 2% relative
# tolerance made 2024 and 2025 "the same number", so a wrong year passed the
# grounding check silently — the single most plausible fabrication in a
# research report.
from app.core.primitives import is_year as _is_year  # noqa: F401


def _value_grounded(value: float, evidence_values: Set[float], tolerance: float = 0.02) -> bool:
    """A drafted number is grounded when some evidence number matches it.

    Tolerance is relative, so 2.5 million vs 2,500,000 and rounding differences
    between a source and a paraphrase both pass, while a fabricated figure does
    not. Years and small integers require an exact match, because a relative
    tolerance is meaningless for them.
    """
    if value in evidence_values:
        return True
    if _is_year(value) or abs(value) < 20:
        return False
    for known in evidence_values:
        if _is_year(known):
            continue
        scale = max(abs(known), abs(value), 1e-9)
        if abs(known - value) / scale <= tolerance:
            return True
    return False


def _invalid_markers(answer: str, valid: Set[int]) -> List[int]:
    """Markers in the text that resolve to nothing in the legend."""
    found = {int(m) for m in re.findall(r"\[(\d+)\]", answer or "")}
    return sorted(found - valid)


def _drop_invalid_markers(answer: str, count: int) -> str:
    """Remove [n] markers that resolve to nothing in the legend."""

    def _keep(match: "re.Match[str]") -> str:
        index = _safe_int(match.group(1), 0)
        return match.group(0) if 1 <= index <= count else ""

    return re.sub(r"\[(\d+)\]", _keep, answer or "")


def _strip_sections(answer: str, headings: Sequence[str]) -> str:
    """Drop appended machine-written sections before auditing the prose."""
    text = answer or ""
    for heading in headings:
        index = text.find(f"\n{heading}")
        if index != -1:
            text = text[:index]
    return text


def _cite_token(fact: Dict[str, Any]) -> str:
    """Placeholder standing in for a citation number not yet assigned."""
    return f"[[c{id(fact)}]]"


def _with_citation(fact: Dict[str, Any]) -> str:
    """A claim carrying its own citation placeholder.

    The marker goes INSIDE the sentence, before the terminal punctuation:
    "claim [1]." not "claim. [1]". Sentence splitters break after [.!?], so a
    marker placed after the period became its own orphan unit — the audit then
    scored the extractive fallback report as largely uncited even though every
    claim carried a citation.
    """
    claim = str(fact.get("claim", "") or "").strip()
    if not claim:
        return ""
    terminal = claim[-1] if claim[-1] in ".!?" else "."
    if claim[-1] in ".!?":
        claim = claim[:-1].rstrip()
    return f"{claim} {_cite_token(fact)}{terminal}"


def _legend_block(numbered: Sequence[Dict[str, Any]]) -> str:
    """`## Sources` section: one entry per line, blank-line separated, so the
    frontend renders a clean vertical numbered list under its own heading.

    Each entry carries its tier ("official publisher", "peer-reviewed",
    "media", ...) and a primary-source marker. A reader deciding how much to
    trust finding [4] should not have to recognise the domain to know whether it
    is a regulator's filing or a blog aggregating one.
    """
    lines: List[str] = []
    for source in numbered:
        entry = f"[{source['n']}] {source.get('domain', '')}"
        tier = str(source.get("tier", "") or "")
        if tier:
            entry += f" ({tier}{', primary' if source.get('primary') else ''})"
        if source.get("url"):
            entry += f" — {source['url']}"
        lines.append(entry)
    return "## Sources\n\n" + "\n\n".join(lines)
