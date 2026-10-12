"""Shared leaves: configuration, logging, pure primitives, the semantic engine.

These modules form the bottom of the dependency graph — they import nothing
from the higher-level packages (core, evidence, retrieval, research, ...), so
every layer may depend on them without creating a cycle.
"""
