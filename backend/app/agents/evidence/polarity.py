from __future__ import annotations

import re



_NEGATION_TOKENS = {
    "not", "no", "never", "cannot", "cant", "isnt", "arent", "wasnt",
    "werent", "doesnt", "dont", "didnt", "wont", "without", "neither",
    "nor", "none", "fails", "failed", "unable", "lacks", "lacking",
    "absent", "denies", "denied", "refutes", "refuted", "disproves",
    "contradicts", "rejects", "unsupported", "false", "incorrect",
}


_DIRECTION_UP = {
    "increase", "increases", "increased", "increasing", "rise", "rises",
    "rising", "rose", "grow", "grows", "growing", "grew", "growth",
    "higher", "surge", "surged", "expand", "expanded", "expansion",
    "improve", "improved", "gain", "gained", "accelerate",
    "accelerated", "more", "exceeds", "exceeded", "outperforms",
    # state assertions: "approved" vs "not approved" is the cleanest
    # polarity flip in regulatory/medical text (benchmark case).
    "approved", "approves", "authorized", "permitted", "allowed",
    "effective", "works", "succeeded", "succeeds", "passed", "valid",
    "safe", "confirmed", "supports", "supported", "enabled",
}


_DIRECTION_DOWN = {
    "decrease", "decreases", "decreased", "decreasing", "fall", "falls",
    "falling", "fell", "decline", "declines", "declined", "declining",
    "drop", "drops", "dropped", "lower", "shrink", "shrank", "reduce",
    "reduced", "reduction", "contract", "contracted", "worsen", "worsened",
    "loss", "lose", "lost", "decelerate", "less", "below",
    "underperforms", "rejected", "rejects", "banned", "prohibited",
    "blocked", "failed", "failing", "ineffective", "harmful", "unsafe",
    "invalid", "refuted", "disabled", "stalled", "weakened",
}


def claim_polarity(text: str) -> int:
    """-1 negated / downward, +1 affirmative-upward, 0 neutral.

    Deliberately coarse. Its only job is to catch the case lexical overlap
    cannot see: a claim asserting the OPPOSITE of its source shares nearly
    all of the source's vocabulary and therefore verified cleanly before.
    Bare "up"/"down" are intentionally excluded — they leak out of
    hyphenated words ("follow-up", "setup") and flip real verdicts (a
    benchmark-found bug: "failed to reduce" vs "led to a reduction" read as
    the same polarity because "up" from "follow-up" canceled "reduction").
    """
    tokens = re.findall(r"[a-z']+", (text or "").lower())
    flat = {t.replace("'", "") for t in tokens}
    negated = bool(flat & _NEGATION_TOKENS)
    up = len(flat & _DIRECTION_UP)
    down = len(flat & _DIRECTION_DOWN)
    direction = 0
    if up > down:
        direction = 1
    elif down > up:
        direction = -1
    if negated:
        return -1 if direction >= 0 else 1
    return direction
