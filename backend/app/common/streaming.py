"""Progress streaming seam for the pipeline.

Skills call :func:`stream_output` to report what they are doing. The payload is
forwarded to whatever *sink* is bound for the run (see
:class:`app.research.runner.ResearchRunner`), which shapes it into a wire frame.
When no sink is bound the message is only logged, so the pipeline is fully
usable in tests and benchmarks without a transport.
"""
from __future__ import annotations

from typing import Any, Optional

from app.common.logging import get_logger

logger = get_logger(__name__)


class EventSink:
    """Minimal transport the pipeline writes progress events to.

    A sink receives ``(type, content, output, metadata)`` tuples and is free to
    buffer, forward or drop them. :mod:`app.research.runner` provides one that
    pushes onto an asyncio queue.
    """

    async def emit(
        self,
        kind: str,
        content: str,
        output: str,
        metadata: Any = None,
    ) -> None:  # pragma: no cover - interface
        raise NotImplementedError


async def stream_output(
    type: str,
    content: str,
    output: str,
    sink: Optional[EventSink] = None,
    output_log: bool = True,
    metadata: Any = None,
) -> None:
    """Report a progress event, logging it and forwarding to ``sink``.

    ``type`` is the coarse category, ``content`` a short machine key, and
    ``output`` the human-readable message. ``output_log`` controls whether the
    message is written to the log (image payloads are suppressed).
    """
    if output_log and type != "images":
        try:
            logger.info("%s", output)
        except UnicodeEncodeError:
            logger.info("%s", output.encode("cp1252", errors="replace").decode("cp1252"))

    if sink is not None:
        try:
            await sink.emit(type, content, output, metadata)
        except Exception as exc:  # a transport failure must not stop research
            logger.warning("[stream] sink emit failed: %s", exc, exc_info=exc)
