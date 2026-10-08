"""Synthesis internals, split out of ``app/agents/synthesizer.py``.

Pure, dependency-light building blocks behind the synthesis agent. The public
entry points stay in ``app.agents.synthesizer`` (``synthesizer_agent`` /
``synthesize``), which imports from here and re-exports every name, so the
module's existing import surface is unchanged.

Modules, bottom to top (each imports only from the layers below it):

    primitives         safe coercion, the corroboration shim, shared regexes
    types              CitationAudit, SynthesisResult
    profiles           ReportProfile, the four profiles, query-type inference
    prompts            the system prompt, reasoning-depth and writing contracts
    sections           the _Section model, canonical alias/heading/rank tables
    citations          numbering, evidence/legend/ranges rendering, audit
    ranking            fact stratification and near-duplicate compression
    findings           per-claim finding lines and composition warnings
    required_sections  deterministic missing-section rendering
    context_blocks     writer brief blocks and measured evidence accounting
    postprocess        telemetry scrub, markdown normalisation, disambiguation
    deterministic      the extractive fallback report
    finalize           the single assembly/audit path
    section_writer     the section-wise synthesis path
    orchestrator       the public entry points (synthesize / synthesizer_agent)

Nothing in this package imports ``app.agents.synthesizer``, to keep the
dependency graph acyclic.
"""
