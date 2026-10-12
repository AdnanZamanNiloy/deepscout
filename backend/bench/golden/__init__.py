"""Versioned golden fixtures for the deterministic offline evaluators.

Data, not a harness: each evaluator loads its own versioned fixture and its own
threshold file, so a future version (v2, v3...) is a new JSON file beside the
old one, not a new evaluator.

    contradictions_v1.json    labeled contradiction cases, loaded by
                              ``bench.golden.contradictions_v1``
    thresholds_*_v1.json      per-evaluator regression floors

Only deterministic, offline-checkable expectations live here — never
model-specific prose.
"""
