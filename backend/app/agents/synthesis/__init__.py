"""Synthesis internals, split out of ``app/agents/synthesizer.py``.

Pure, dependency-light building blocks behind the synthesis agent. The public
entry points stay in ``app.agents.synthesizer`` (``synthesizer_agent`` /
``synthesize``), which imports from here and re-exports every name, so the
module's existing import surface is unchanged.

Modules are deliberately one-directional (``prompts`` -> ``profiles``); nothing
in this package imports ``app.agents.synthesizer``, to keep the dependency
graph acyclic.
"""
