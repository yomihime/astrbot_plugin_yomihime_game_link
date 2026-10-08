"""Shared bounded public-fact scan for Web and Tool publication."""

from collections.abc import Mapping

from ..api.results import FactDocument


def public_fact_strings(facts: FactDocument):
    """Visit keys, values and sources under the existing total 512-node budget."""
    pending = [(facts.facts, 0)]
    pending.extend((source, 0) for source in facts.sources)
    visited = 0
    seen = set()
    while pending:
        value, depth = pending.pop()
        visited += 1
        if visited > 512 or depth > 16:
            raise ValueError("public_facts_exceed_limits")
        if isinstance(value, str):
            if len(value) > 4096:
                raise ValueError("public_facts_exceed_limits")
            if value not in seen:
                seen.add(value)
                yield value
        elif isinstance(value, Mapping):
            pending.extend((key, depth + 1) for key in value)
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, (tuple, list)):
            pending.extend((item, depth + 1) for item in value)
