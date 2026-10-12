"""Review-loop routing, accept sentinels, and agent logging.

The editing, fact-check and draft loops are bounded: after a configurable number
of revision rounds the graph force-accepts so a run proceeds instead of dying at
LangGraph's recursion limit. Sentinels are matched exactly (see
:func:`is_none_accept_response`) so a critical note that merely contains "none"
or "no" is never mistaken for approval.
"""
from __future__ import annotations

import logging

from app.common.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MAX_PLAN_REVISIONS = 3
DEFAULT_MAX_FACT_CHECK_REVISIONS = 3
DEFAULT_MAX_DRAFT_REVISIONS = 3


class MaxPlanRevisionsExceededError(RuntimeError):
    """Raised when human feedback requests exceed the planning limit."""


class MaxFactCheckRevisionsExceededError(RuntimeError):
    """Raised when writer/fact-checker rounds exceed the configured limit."""


class MaxDraftRevisionsExceededError(RuntimeError):
    """Raised when reviewer/reviser rounds exceed the draft limit."""


def is_none_accept_response(response) -> bool:
    """True only when ``response`` is exactly the accept sentinel ``None``.

    Accepts surrounding whitespace and one matching pair of quotes (models often
    quote the token). Empty/None inputs are not acceptance.
    """
    if response is None:
        return False
    if not isinstance(response, str):
        response = str(response)
    text = response.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text.lower() == "none"


def is_plan_approval(feedback) -> bool:
    """True only when ``feedback`` is exactly ``no`` (approve/no-change)."""
    if feedback is None:
        return False
    if not isinstance(feedback, str):
        feedback = str(feedback)
    return feedback.strip().lower() == "no"


def route_review_gate(review, max_plan_revisions=DEFAULT_MAX_PLAN_REVISIONS):
    """Route after the plan-review node: ``accept`` or ``revise``."""
    if review.get("human_feedback") is None:
        return "accept"
    if max_plan_revisions is None:
        return "revise"
    count = review.get("plan_revision_count", 0)
    if count > max_plan_revisions:
        raise MaxPlanRevisionsExceededError(
            f"Plan revision limit exceeded: {count} > {max_plan_revisions}."
        )
    return "revise"


def route_evidence_audit(state, max_fact_check_revisions=DEFAULT_MAX_FACT_CHECK_REVISIONS):
    """Route after the fact-check node: ``accept`` or ``revise``."""
    if state.get("fact_check_notes") is None:
        return "accept"
    if max_fact_check_revisions is None:
        return "revise"
    count = state.get("fact_check_revision_count", 0)
    if count > max_fact_check_revisions:
        raise MaxFactCheckRevisionsExceededError(
            f"Fact-check revision limit exceeded: {count} > {max_fact_check_revisions}."
        )
    return "revise"


def route_draft_review(draft, max_draft_revisions=DEFAULT_MAX_DRAFT_REVISIONS):
    """Route after the draft-review node: ``accept`` or ``revise``."""
    if draft.get("review") is None:
        return "accept"
    if max_draft_revisions is None:
        return "revise"
    count = draft.get("draft_revision_count", 0)
    if count > max_draft_revisions:
        raise MaxDraftRevisionsExceededError(
            f"Draft revision limit exceeded: {count} > {max_draft_revisions}."
        )
    return "revise"


def log_agent(agent: str, message: str) -> None:
    """Log an agent's progress line under a stable per-agent logger name."""
    logging.getLogger(f"research.{agent.lower()}").info("%s: %s", agent, message)
