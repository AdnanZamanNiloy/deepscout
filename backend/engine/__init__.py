"""Vendored multi-agent research engine.

The two packages under this directory (`gptr` — the shared research core, and
`multi_agents` — the LangGraph agent team) use absolute imports rooted at their
own package names, e.g. ``from gptr.utils.llm import create_chat_completion``.
To keep those internal imports untouched at vendoring time, this directory is
placed on ``sys.path`` so ``gptr`` and ``multi_agents`` resolve as top-level
modules.

Importing this package is side-effecting by design: it performs the one-time
``sys.path`` insertion. Every other module in the backend imports the engine
through this package (``import engine``) or through the adapters in
``app.engine``.
"""
from __future__ import annotations

import os
import sys

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))

if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

__all__ = ["_ENGINE_DIR"]
