"""Planner internals, split out of ``app/agents/planner.py``.

The public entry point stays in ``app.agents.planner`` (``planner_agent``), which
imports from here and re-exports every name, so the module's existing import
surface is unchanged.

Modules, bottom to top (each imports only from the layers below it):

    types       TypedDicts and the constant tables (valid sets, frontier axes)
    normalize   normalize_text/domain, dimension_to_axis, semantic dedup
    prompts     PLANNER_SYSTEM_PROMPT, PLANNING_DIRECTIVE_PROMPT
    dimensions  plan_dimensions + deterministic dimension validation
    contracts   _contract, gap_contracts, axis/frontier coverage enforcement
    plan        fallback_plan, select_plan, sanitize_dependencies, waves
    agent       planner_agent (the public entry point)

Nothing in this package imports ``app.agents.planner``, to keep the dependency
graph acyclic.
"""
