"""Criticism ledger: turn critic findings into tracked research TASKS.

Why this module exists
----------------------
The critic already computes, every pass, exactly what is wrong with the
evidence pool — `gate_failures` (uncovered planned axis, missing primary
source, unverified pool, single-domain pool, severe contradiction, drift,
concentration) plus `gaps` (planned angles that produced nothing). But those
findings had NO after-life: the critic emitted free-text `improved_queries`
and, on the next pass, only its GLOBAL `is_sufficient` verdict was consulted.
Nothing checked whether the SPECIFIC criticism raised last pass was actually
resolved. Two failure modes followed:

  1. A run could mark itself sufficient because aggregate confidence rose for
     unrelated reasons while the exact gate that fired (say, an uncovered
     axis) was still open — the criticism was silently dropped.
  2. A run could re-issue the same gap pass after pass, because "was this
     criticism addressed?" was never a question the loop could ask.

This ledger closes both. Each distinct criticism becomes a structured task
with a stable key; on the next pass the task is re-checked against the NEW
pool and marked resolved/attempted/exhausted. A task still open with budget
left is grounds for one more pass; a task whose budget is spent becomes an
explicit limitation rather than an invisible hole.

Design constraints (AGENTS.md):
  * Run-scoped state threaded through LangGraph — NO module-level store. The
    ledger is a plain dict on `state["criticism_ledger"]`, sanitized on every
    read, so concurrent runs never share memory (§4.3).
  * Deterministic and total: malformed input yields an empty/unchanged ledger,
    never an exception (§4.4).
  * Every kind is derived from the question and the measured pool, never from
    hardcoded subject strings — so it holds for any query (domain-agnostic).
  * ADDITIVE ONLY: existing behaviour is preserved when the ledger is empty or
    absent; callers guard with `.get()`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from app.core.logging import get_logger

logger = get_logger(__name__)


# Task kinds. Each is a way a report can be confidently wrong, matching the
# critic's own gate vocabulary so the two can never disagree.
KIND_UNCOVERED_AXIS = "uncovered_axis"
KIND_MISSING_PRIMARY = "missing_primary_source"
KIND_NO_VERIFIED = "no_verified_evidence"
KIND_SINGLE_DOMAIN = "single_domain"
KIND_SEVERE_CONFLICT = "severe_conflict"
KIND_DRIFT = "drift"
KIND_CONCENTRATION = "concentration"
KIND_INSUFFICIENT_FACTS = "insufficient_facts"
KIND_NO_DEFINITION = "no_definitional_claim"
KIND_UNSOURCED_ANGLE = "unsourced_angle"

STATUS_OPEN = "open"
STATUS_ATTEMPTED = "attempted"
STATUS_RESOLVED = "resolved"
STATUS_EXHAUSTED = "exhausted"

DEFAULT_MAX_ATTEMPTS = 2

# Bounded memory: never let a pathological run accumulate unbounded tasks.
MAX_TASKS = 24


def _kind_for_failure(failure: str) -> Optional[str]:
    """Map a critic gate_failure string to a task kind (pure, total).

    The gate_failure vocabulary is owned by app/agents/critic.py; this is a
    lookup, not a re-implementation, so a new gate with no mapping is simply
    untracked rather than mis-tracked.
    """
    f = (failure or "").strip()
    if not f:
        return None
    if f.startswith("planned_axis_uncovered="):
        return KIND_UNCOVERED_AXIS
    if f.startswith("missing_primary_source="):
        return KIND_MISSING_PRIMARY
    if f.startswith("verified="):
        return KIND_NO_VERIFIED
    if f.startswith("domains="):
        return KIND_SINGLE_DOMAIN
    if f.startswith("severe_conflicts="):
        return KIND_SEVERE_CONFLICT
    if f.startswith("drift="):
        return KIND_DRIFT
    if f.startswith("concentration_on="):
        return KIND_CONCENTRATION
    if f.startswith("facts="):
        return KIND_INSUFFICIENT_FACTS
    if f == "no definitional claim":
        return KIND_NO_DEFINITION
    if f.startswith("uncovered_angles="):
        return KIND_UNSOURCED_ANGLE
    return None


def _target_for_failure(failure: str) -> str:
    """The axis/dimension a failure names, else '' (pure, total)."""
    f = (failure or "").strip()
    for prefix in (
        "planned_axis_uncovered=",
        "missing_primary_source=primary_source_for:",
        "missing_primary_source=",
        "concentration_on=",
    ):
        if f.startswith(prefix):
            return f[len(prefix):].strip()
    return ""


def _key(kind: str, target: str) -> str:
    return f"{kind}::{target}".lower()


def _new_task(kind: str, target: str, detail: str, iteration: int) -> Dict[str, Any]:
    return {
        "kind": kind,
        "target": target,
        "detail": detail,
        "status": STATUS_OPEN,
        "attempts": 0,
        "max_attempts": DEFAULT_MAX_ATTEMPTS,
        "first_seen_iteration": int(iteration),
        "last_seen_iteration": int(iteration),
        "resolved_iteration": None,
    }


def _sanitize(state: Any) -> Dict[str, Dict[str, Any]]:
    """Return a clean copy of the ledger; never raises, never mutates input."""
    if not isinstance(state, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for key, entry in state.items():
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind", "") or "")
        if not kind:
            continue
        status = str(entry.get("status", "") or STATUS_OPEN)
        if status not in (
            STATUS_OPEN, STATUS_ATTEMPTED, STATUS_RESOLVED, STATUS_EXHAUSTED,
        ):
            status = STATUS_OPEN
        out[str(key)] = {
            "kind": kind,
            "target": str(entry.get("target", "") or ""),
            "detail": str(entry.get("detail", "") or ""),
            "status": status,
            "attempts": max(0, _as_int(entry.get("attempts", 0))),
            "max_attempts": max(1, _as_int(entry.get("max_attempts", DEFAULT_MAX_ATTEMPTS))),
            "first_seen_iteration": max(0, _as_int(entry.get("first_seen_iteration", 0))),
            "last_seen_iteration": max(0, _as_int(entry.get("last_seen_iteration", 0))),
            "resolved_iteration": entry.get("resolved_iteration"),
        }
    return out


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def update(
    ledger: Any,
    critique: Dict[str, Any],
    facts: Sequence[Dict[str, Any]],
    *,
    iteration: int,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Dict[str, Dict[str, Any]]:
    """Fold this pass's critic findings into the ledger and re-check outcomes.

    Order matters: existing tasks are first RE-CHECKED against the current pool
    (so a task resolved this pass is marked resolved and never re-attempted),
    then the current critic's failures are upserted (a still-failing gate bumps
    the existing task's attempts, a new gate creates a task). Returns a NEW
    sanitized ledger; the input is not mutated. Total: any malformed input
    leaves the ledger as-is rather than raising.
    """
    try:
        return _update_impl(ledger, critique, facts, iteration=iteration, max_attempts=max_attempts)
    except Exception as exc:  # a ledger bug must never break a run
        logger.warning("criticism_ledger_update_failed", error=str(exc), exc_info=exc)
        return _sanitize(ledger)


def _update_impl(
    ledger: Any,
    critique: Dict[str, Any],
    facts: Sequence[Dict[str, Any]],
    *,
    iteration: int,
    max_attempts: int,
) -> Dict[str, Dict[str, Any]]:
    out = _sanitize(ledger)
    if not isinstance(critique, dict):
        return out

    current_failures = [str(f) for f in (critique.get("gate_failures") or ()) if str(f or "").strip()]
    current_gaps = [str(g) for g in (critique.get("gaps") or ()) if str(g or "").strip()]

    # The set of task keys the critic is STILL raising this pass. A task not in
    # this set was either resolved or is no longer considered blocking.
    still_raised: set = set()
    for failure in current_failures:
        kind = _kind_for_failure(failure)
        if kind:
            still_raised.add(_key(kind, _target_for_failure(failure)))
    for gap in current_gaps:
        still_raised.add(_key(KIND_UNSOURCED_ANGLE, _gap_target(gap)))

    # 1. Re-check every tracked task against the current pool. A task whose
    #    condition no longer holds is RESOLVED; one still raised is charged an
    #    attempt (this pass tried to close it).
    for key, entry in out.items():
        if entry["status"] in (STATUS_RESOLVED,):
            continue
        if key in still_raised:
            # The criticism survived another pass: charge an attempt.
            entry["attempts"] = entry["attempts"] + 1
            entry["last_seen_iteration"] = int(iteration)
            if entry["attempts"] >= max(1, int(entry["max_attempts"])):
                entry["status"] = STATUS_EXHAUSTED
            elif entry["attempts"] > 0:
                entry["status"] = STATUS_ATTEMPTED
        else:
            # Not raised this pass: the specific condition was addressed.
            if entry["status"] in (STATUS_OPEN, STATUS_ATTEMPTED):
                entry["status"] = STATUS_RESOLVED
                entry["resolved_iteration"] = int(iteration)

    # 2. Upsert the failures raised THIS pass (new ones become open tasks).
    for failure in current_failures:
        kind = _kind_for_failure(failure)
        if not kind:
            continue
        target = _target_for_failure(failure)
        key = _key(kind, target)
        if key in out:
            out[key]["detail"] = failure
            out[key]["last_seen_iteration"] = int(iteration)
            continue
        out[key] = _new_task(kind, target, failure, iteration)

    for gap in current_gaps:
        target = _gap_target(gap)
        key = _key(KIND_UNSOURCED_ANGLE, target)
        if key in out:
            out[key]["detail"] = gap
            out[key]["last_seen_iteration"] = int(iteration)
            continue
        out[key] = _new_task(KIND_UNSOURCED_ANGLE, target, gap, iteration)

    # Bounded memory: keep the most recently updated MAX_TASKS entries.
    if len(out) > MAX_TASKS:
        ordered = sorted(
            out.items(),
            key=lambda kv: (int(kv[1].get("last_seen_iteration", 0)), kv[0]),
            reverse=True,
        )
        out = dict(ordered[:MAX_TASKS])
    return out


def _gap_target(gap: str) -> str:
    """The angle a critic gap names, as a stable-ish target token (pure)."""
    text = str(gap or "").strip()
    if not text:
        return ""
    # Critic gap strings read "no evidence for angle: X" / "angle under-sourced
    # (n/m): X" / "uncovered angle: X". Take the part after the last colon.
    if ":" in text:
        return text.rsplit(":", 1)[-1].strip().lower()
    return text.lower()


def active_tasks(ledger: Any) -> List[Dict[str, Any]]:
    """Tasks still open or attempted (budget may remain) — order-stable."""
    out = _sanitize(ledger)
    return [
        e for _, e in sorted(out.items())
        if e["status"] in (STATUS_OPEN, STATUS_ATTEMPTED)
    ]


def unresolved_targets(ledger: Any) -> List[str]:
    """Search targets for still-actionable criticisms (deduped, bounded).

    These are what the next pass should aim at — the concrete, machine-readable
    form of "convert each weakness into a targeted research task".
    """
    out = _sanitize(ledger)
    targets: List[str] = []
    for _, entry in sorted(out.items()):
        if entry["status"] not in (STATUS_OPEN, STATUS_ATTEMPTED):
            continue
        target = str(entry.get("target", "") or "").replace("_", " ").strip()
        if target and target not in targets:
            targets.append(target)
    return targets[:6]


def has_actionable(ledger: Any) -> bool:
    """True when at least one criticism still has budget to be addressed."""
    return bool(active_tasks(ledger))


def resolved_count(ledger: Any) -> int:
    return sum(1 for e in _sanitize(ledger).values() if e["status"] == STATUS_RESOLVED)


def exhausted_limitations(ledger: Any, limit: int = 5) -> List[str]:
    """Reader-facing limitations for criticisms that were raised and not closed.

    A criticism still unresolved when its attempt budget is spent is a property
    of the question/sources, not a slow run — it is disclosed, not hidden. This
    is the "explicitly disclose the limitations rather than pretending the
    research is complete" half of the contract. Deterministic, bounded,
    empty-safe.
    """
    out = _sanitize(ledger)
    lines: List[str] = []
    for _, entry in sorted(out.items(), key=lambda kv: kv[0]):
        if entry["status"] != STATUS_EXHAUSTED:
            continue
        detail = " ".join(str(entry.get("detail", "") or "").split())
        if not detail:
            detail = f"{entry['kind'].replace('_', ' ')}: {entry.get('target', '')}".strip()
        if not detail:
            continue
        if len(detail) > 160:
            detail = detail[:159].rstrip() + "…"
        lines.append(
            f"unresolved after {entry['attempts']} targeted attempt"
            + ("s" if entry["attempts"] != 1 else "")
            + f": {detail}"
        )
        if len(lines) >= max(1, int(limit)):
            break
    return lines


def summary(ledger: Any) -> Dict[str, int]:
    """Counts by status, for the audit/trace and the decision engine."""
    out = _sanitize(ledger)
    counts = {STATUS_OPEN: 0, STATUS_ATTEMPTED: 0, STATUS_RESOLVED: 0, STATUS_EXHAUSTED: 0}
    for entry in out.values():
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    counts["total"] = len(out)
    return counts
