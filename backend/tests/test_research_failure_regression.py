"""Regression for the live "MSc research topic" failure (2026-10).

The run exposed three concrete pipeline defects, none of which the code's own
tests caught because they exercised each component in isolation with stable,
well-formed inputs. This module reproduces the LIVE shape and pins the fixes:

  1. REPETITIVE SEARCHES. The dimension-candidate channel appended an
     "(attempt N)" suffix to the query, which defeated search_node's exact
     executed-query dedup — so the same search ran again every round. The live
     run issued "...2026 (attempt 2)" and "...(attempt 3)". Fix: emit the query
     text unchanged; de-duplication lives in the ledger, not a mutated string.

  2. FALSE RESOLUTION. The criticism ledger keyed drift/concentration tasks on
     their measured VALUE, so `drift=0.50` -> `drift=0.61` minted a NEW task
     each round while the old one was marked "resolved" — falsely clearing a
     criticism that was still firing. Fix: run-singleton conditions take a
     stable key; only axis-shaped failures carry a per-item target.

  3. NON-CONVERGENT DRIFT. Drift rose 0.50 -> 0.61 across rounds while the
     redirect re-forced a pass every time, ballooning to 80 sources. Fix: a
     drift that fails to improve across consecutive rounds converges (finalize
     with the drift recorded as a limitation).
"""
from __future__ import annotations

from app.core.criticism_ledger import update
from app.core.depth.checks import _focus_diverging
from app.core.depth.controller import decide_with_checks


# --------------------------------------------------------------------------
# 1. No "(attempt N)" mutation -> the query text is stable across rounds
# --------------------------------------------------------------------------


def test_dimension_queries_are_not_suffixed_with_attempt_numbers():
    """The candidate generator must never mint a fresh "(attempt N)" query text.

    This is the exact defect that defeated search_node's executed-query dedup
    (it matches exact normalized text), so the same search re-ran every round —
    the live run issued "...2026 (attempt 2)" and "...(attempt 3)". Asserted over
    the parsed AST (not raw source, so comments don't count): no executable
    string literal may contain an attempt suffix. De-duplication must live in
    the ledger, not in a mutated string that query memory cannot match.
    """
    import ast

    tree = ast.parse(open("app/core/investigation_planner.py").read())
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            ds = ast.get_docstring(node, clean=False)
            if ds:
                docstrings.add(ds)
    offenders = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value not in docstrings
        and "(attempt " in node.value.lower()
    ]
    assert not offenders, f"query text must not carry an attempt suffix: {offenders}"


# --------------------------------------------------------------------------
# 2. Stable ledger keys — a still-firing drift is never falsely "resolved"
# --------------------------------------------------------------------------


def test_worsening_drift_is_not_marked_resolved():
    r1 = {"gate_failures": ["drift=0.50"], "gaps": []}
    r2 = {"gate_failures": ["drift=0.61", "concentration_on=_unassigned=0.61"], "gaps": []}
    r3 = {"gate_failures": ["drift=0.61", "concentration_on=_unassigned=0.61"], "gaps": []}

    led = update({}, r1, [], iteration=1)
    assert "drift::" in led
    led = update(led, r2, [], iteration=2)
    led = update(led, r3, [], iteration=3)

    drift = led.get("drift::")
    assert drift is not None, "drift task must persist under a stable key"
    assert drift["status"] != "resolved", "a still-firing drift must not be cleared"


def test_axis_value_variation_does_not_fork_the_key_into_false_resolution():
    # Same axis, different surrounding count -> must be the SAME task.
    a = update({}, {"gate_failures": ["uncovered_angles=3", "planned_axis_uncovered=cost"], "gaps": []}, [], iteration=1)
    b = update(a, {"gate_failures": ["uncovered_angles=8", "planned_axis_uncovered=cost"], "gaps": []}, [], iteration=2)
    cost = b.get("uncovered_axis::cost")
    assert cost is not None
    assert cost["status"] in ("attempted", "open"), "re-raised axis must stay actionable"
    assert cost["attempts"] >= 1


# --------------------------------------------------------------------------
# 3. Non-improving drift converges (finalize), improving drift does not
# --------------------------------------------------------------------------


def test_worsening_drift_converges_to_finalize():
    state = {
        "query": "suggest demanding MSc CS research topics",
        "critique": {"is_sufficient": False, "improved_queries": ["more sources"]},
        "focus": {"report": {"drifted": True, "off_query_share": 0.61,
                             "missing": ["frontier"], "thin": []}},
        "focus_history": [
            {"off_query_share": 0.50},
            {"off_query_share": 0.61},
            {"off_query_share": 0.61},
        ],
        "iteration": 3,
        "max_iterations": 5,
        "confidence": 0.4,
        "facts": [],
        "sub_questions": [{"axis": "frontier", "question": "q"}],
    }
    decision, checks = decide_with_checks(state)
    assert decision == "finalize"
    assert "drift did not improve" in checks["decision_reason"]


def test_improving_drift_still_allows_expansion():
    state = {
        "query": "suggest demanding MSc CS research topics",
        "critique": {"is_sufficient": False, "improved_queries": ["more sources"]},
        "focus": {"report": {"drifted": True, "off_query_share": 0.30,
                             "missing": ["frontier"], "thin": []}},
        "focus_history": [
            {"off_query_share": 0.61},
            {"off_query_share": 0.45},
            {"off_query_share": 0.30},
        ],
        "iteration": 3,
        "max_iterations": 5,
        "confidence": 0.4,
        "facts": [],
        "sub_questions": [{"axis": "frontier", "question": "q"}],
    }
    decision, _ = decide_with_checks(state)
    # Drift is improving: the redirect is still given a chance, not converged.
    assert decision == "expand"


def test_drift_divergence_helper_requires_enough_rounds():
    assert _focus_diverging({"focus_history": [{"off_query_share": 0.6}]})["diverging"] is False
    assert _focus_diverging({"focus_history": []})["diverging"] is False
