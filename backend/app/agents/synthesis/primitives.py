"""Low-level primitives shared across the synthesis package.

Extracted verbatim from `app/agents/synthesizer.py` (refactor; no behaviour
change). Safe coercion helpers for untrusted fact dicts, the independent-source
corroboration shim, and the regexes the citation audit and disambiguation
helpers share.

`synthesizer.py` re-exports every name here, so the existing import surface
(tests included) is unchanged."""

from __future__ import annotations

import re
from typing import Any, Dict, Set

from app.agents.research_quality import independent_corroboration
from app.core.primitives import safe_float as _safe_float, safe_int as _safe_int  # noqa: F401


def _corroboration(fact: Dict[str, Any]) -> int:
    """Independent-source count for a fact, floored at 1.

    Reads `independent_corroboration` when the independence pass has stamped
    it, so three outlets reprinting one wire story count as ONE corroborating
    voice rather than three. Falls back to the raw count when the pass did not
    run, which keeps every existing caller working unchanged.
    """
    return independent_corroboration(fact)


# A sentence stating a fact but carrying no [n] is a traceability hole. These
# openers mark analysis/transition sentences, which legitimately carry none.
# The second block covers the report's own scaffolding — the honesty notes the
# extractive path emits are meta-statements about the report, not factual
# claims about the world. Counting them as untraceable facts deflated citation
# density exactly when the pipeline was degraded.
# NOTE: no trailing \b — alternatives ending in ":" can never satisfy one.
_ANALYSIS_LEAD_RE = re.compile(
    r"^\s*(?:taken together|in short|overall|therefore|this means|the picture|"
    r"in practice|by contrast|as a result|on balance|the implication|"
    r"what follows|in other words|put differently|the net effect|"
    r"evidence is thin|confidence:|well-supported:|uncertain:|short answer:|"
    r"conflicting evidence:|could not verify:|pipeline stages on deterministic|"
    r"the evidence spans|based on your question|this report focuses)",
    re.IGNORECASE,
)
_FACTUAL_HINT_RE = re.compile(r"\d|\b(19|20)\d{2}\b|%|\bper cent\b|\bpercent\b")

# Numbers this small are ordinary prose ("three angles", "two sources") and are
# not worth grounding; anything with a unit, currency, percent or year is.
_TRIVIAL_NUMBERS: Set[float] = {0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0}

# The disambiguation block an ambiguous-query report MUST open with:
# "1) **Transformer neural network architecture** — attention-based ...".
# Numbered with a closing paren (not "1.") so sentence splitters keep the line
# intact. These lines are definitional common knowledge (the non-researched
# sense has no evidence by design), so the citation audit exempts them rather
# than flagging the pipeline's own disambiguation as untraceable.
_DISAMBIG_LINE_RE = re.compile(
    r"^\s*\d+\)\s*\*\*[^*]{2,120}\*\*\s*[—-]"
    r"|^\s*based on your question,\s*this report focuses on meaning",
    re.IGNORECASE,
)

_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*\S)\s*$")
