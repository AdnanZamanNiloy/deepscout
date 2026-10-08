from __future__ import annotations

from typing import Literal



DECISION = Literal["expand", "finalize"]


DEFAULT_MINIMUM_SOURCES = 2


AXIS_DOMINANCE_THRESHOLD = 0.60


MIN_AXES_COVERED = 2


MODE_MIN_ITERATIONS = {"quick": 1, "audit": 2, "redteam": 1}


SEVERE_SEVERITY = 0.60
