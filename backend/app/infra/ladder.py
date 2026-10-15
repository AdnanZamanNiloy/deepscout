"""The degradation ladder: shed optional work, in order, before the hard cap.

Why this exists
---------------
The RunUsage governor (app/core/usage.py) stops a run when a *ceiling* is
already spent — it is a brake, and it acts only after the limit is reached. The
ladder is the steering wheel in front of it: given how long the run has taken
and what still MUST happen, it decides which OPTIONAL work to skip while there
is still time to finish the mandatory path.

Without it, a run that spends its budget early arrives at the optional stages
(visuals, a second audit pass) with nothing left and either overruns the cap or
degrades abruptly at the end. With it, the run sheds in a fixed, announced
order so what is dropped is the least valuable thing remaining, and the reason
is reported rather than discovered.

Design rules
------------
* Deterministic and cheap: no model call, no I/O. It is a function of elapsed
  time, the estimated cost of what remains, and the mode's limits.
* Ordered by (time saved) / (quality lost), so the first thing shed costs the
  reader least. The order is explicit and unit-tested — an implicit order is
  how "we skipped the wrong thing" bugs happen.
* Never skips a MANDATORY stage. The mandatory path is what makes a report a
  report: pathfinder -> architect -> review -> scout -> narrative -> assemble.
  Only optional work is on the ladder.
* Every shed is recorded as a run degradation, so the delivered report says
  what it left out (AGENTS.md: an ungrounded answer must never be mistakable
  for a researched one — and neither must a thin run for a complete one).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# The ladder, in shedding order. Each entry is an optional capability that can
# be dropped. The order is the decision, not an implementation detail:
#   1. diagram            — pure presentation; costs a call, adds no evidence.
#   2. second_audit_pass  — the auditor's SECOND look at an already-checked
#                           draft. One pass still happened; this is the extra.
#   3. extra_diagram_retry— a re-ask when the diagram came back empty.
SHED_ORDER: List[str] = [
    "diagram",
    "second_audit_pass",
    "extra_diagram_retry",
]

# Names shown to the user in degraded_reasons. Kept here so the wire string and
# the ladder entry cannot drift.
SHED_REASON: Dict[str, str] = {
    "diagram": "skipped to stay within the mode's time budget",
    "second_audit_pass": "single fact-check pass used to stay within the time budget",
    "extra_diagram_retry": "no retry for an empty diagram, to stay within the time budget",
}

# Rough per-stage p90 estimates (seconds), seeded from the Step 0 baseline
# (bench/results/MODE_BASELINE.md) and used only for the projection. They are
# deliberately generous: over-estimating makes the ladder shed slightly early,
# which is the safe direction for a time budget.
STAGE_P90_SECONDS: Dict[str, float] = {
    "pathfinder": 25.0,
    "architect": 5.0,
    "review_gate": 3.0,
    "scout_swarm": 25.0,
    "narrative_writer": 20.0,
    "evidence_auditor": 12.0,
    "diagram_designer": 8.0,
    "report_assembler": 3.0,
}

# The stages that must run for the run to produce a report at all.
MANDATORY_STAGES: List[str] = [
    "pathfinder",
    "architect",
    "review_gate",
    "scout_swarm",
    "narrative_writer",
    "report_assembler",
]


@dataclass
class LadderDecision:
    """The ladder's verdict for one probe point in a run."""

    shed: List[str] = field(default_factory=list)
    projected_finish_s: float = 0.0
    soft_limit_s: float = 0.0
    note: str = ""

    def should_skip(self, capability: str) -> bool:
        return capability in self.shed


class DegradationLadder:
    """Decides what to shed, given elapsed time and a soft limit.

    One instance per run. ``soft_limit_s`` is the point past which the run
    should start shedding so it can still finish the mandatory path before the
    hard cap; ``hard_cap_s`` is the enforced stop from
    ``mode_timeout_seconds``.
    """

    def __init__(
        self,
        *,
        soft_limit_s: float,
        hard_cap_s: float,
        mode: str = "standard",
        clock: Optional[Any] = None,
    ):
        self.soft_limit_s = float(soft_limit_s)
        self.hard_cap_s = float(hard_cap_s)
        self.mode = mode
        self._clock = clock or time.monotonic
        self.started_at = self._clock()
        self.shed: List[str] = []
        self.probes: List[Dict[str, Any]] = []

    # -- timing ------------------------------------------------------------

    @property
    def elapsed_s(self) -> float:
        return self._clock() - self.started_at

    # -- the decision ------------------------------------------------------

    def _stage_p90(self, stage: str) -> float:
        return STAGE_P90_SECONDS.get(stage, 10.0)

    def project_finish_s(self, remaining_stages: List[str], extra_s: float = 0.0) -> float:
        """Projected total run time if every remaining mandatory stage runs."""
        projected = self.elapsed_s
        for stage in remaining_stages:
            projected += self._stage_p90(stage)
        return projected + max(0.0, extra_s)

    def evaluate(
        self,
        remaining_stages: List[str],
        *,
        capability: str,
        extra_s: float = 0.0,
    ) -> bool:
        """Should ``capability`` run? Records the decision; returns the verdict.

        Called at the point a capability WOULD run. ``remaining_stages`` is the
        mandatory work still ahead, INCLUDING the capability's own stage when it
        has one (the caller passes the same list in both cases, so the two
        projections differ only by the capability itself).

        A capability is shed when running it would push the projected finish
        past the soft limit — i.e. when there is not enough room for both it and
        the mandatory path that still has to happen after it.
        """
        # Projected finish if we RUN the capability: every remaining stage runs,
        # plus its own extra cost (a second pass, a retry).
        run_cost = self._stage_p90(capability) + max(0.0, extra_s)
        projected_with = self.project_finish_s(remaining_stages) + run_cost
        # Projected finish if we SKIP it: the same remaining stages, minus the
        # capability's own stage cost (which the caller includes in the list).
        projected_without = self.project_finish_s(remaining_stages)

        fits = projected_with <= self.soft_limit_s
        decision = not fits

        record = {
            "capability": capability,
            "elapsed_s": round(self.elapsed_s, 1),
            "projected_with_s": round(projected_with, 1),
            "projected_without_s": round(projected_without, 1),
            "soft_limit_s": self.soft_limit_s,
            "shed": decision,
        }
        self.probes.append(record)
        if decision and capability not in self.shed:
            self.shed.append(capability)
        return not decision

    # -- reporting ---------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "soft_limit_s": self.soft_limit_s,
            "hard_cap_s": self.hard_cap_s,
            "elapsed_s": round(self.elapsed_s, 1),
            "shed": list(self.shed),
            "probes": list(self.probes),
        }

    def shed_reasons(self) -> Dict[str, str]:
        """Reason strings for each shed capability, for degraded_reasons."""
        return {cap: SHED_REASON.get(cap, "shed to stay within the time budget")
                for cap in self.shed}


# A soft limit is where shedding STARTS, so it must leave room to finish. The
# mandatory tail after the point most optional work happens (scout + narrative)
# is roughly the narrative + assemble cost; we reserve that plus a margin, and
# never let the soft limit exceed the hard cap minus that reserve.
def soft_limit_for(hard_cap_s: float, mode: str = "standard") -> float:
    reserve = _TAIL_RESERVE_SECONDS.get(mode, 45.0)
    return max(10.0, float(hard_cap_s) - reserve)


_TAIL_RESERVE_SECONDS: Dict[str, float] = {
    "quick": 30.0,
    "standard": 60.0,
    "deep": 90.0,
}
