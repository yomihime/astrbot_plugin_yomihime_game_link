"""Shared bounded public-fact scan for Web and Tool publication."""

from collections.abc import Mapping

from yomihime_game_link_sdk.results import FactDocument

from ..core.contracts.validation_boundary import validate_contract


def public_fact_strings(facts: FactDocument):
    """Visit keys, values and sources under the existing total 512-node budget."""
    validate_contract(facts)
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


def preflight_error_facts(result):
    """Detach and bound ERROR JSON before generic contract traversal/copying.

    Counts keys and values independently, including repeated (noncyclic)
    references and sources. SDK values do not carry validation authority.
    """
    import json
    from math import isfinite
    from types import MappingProxyType

    from yomihime_game_link_sdk.display import Privacy
    from yomihime_game_link_sdk.results import ErrorCode, ErrorDetail

    document = result.model_facts
    if document is None:
        return
    if type(document) is not FactDocument or type(result.error) is not ErrorDetail:
        raise ValueError("invalid_error_facts")
    if (
        result.document is not None
        or result.schema_version != "1.8.0"
        or document.schema_version != "1.8.0"
        or not (
            result.privacy is Privacy.PUBLIC
            or type(result.privacy) is str
            and result.privacy == "public"
        )
    ):
        raise ValueError("invalid_error_facts")
    code = result.error.code
    if type(code) is ErrorCode:
        code = code.value
    elif type(code) is not str:
        raise ValueError("invalid_error_facts")
    message = result.error.message
    if type(message) is not str or not message:
        raise ValueError("invalid_error_facts")
    visited, active = 0, set()

    def snapshot(value, depth=0):
        nonlocal visited
        visited += 1
        if visited > 512 or depth > 16:
            raise ValueError("public_facts_exceed_limits")
        kind = type(value)
        if kind is str:
            if len(value) > 4096:
                raise ValueError("public_facts_exceed_limits")
            return value, value
        if value is None or kind in (bool, int):
            return value, value
        if kind is float:
            if not isfinite(value):
                raise ValueError("invalid_error_facts")
            return value, value
        if isinstance(value, Mapping) or kind in (list, tuple):
            token = id(value)
            if token in active:
                raise ValueError("invalid_error_facts")
            active.add(token)
            try:
                if isinstance(value, Mapping):
                    immutable, plain = {}, {}
                    failed = False
                    try:
                        # Incremental keys/read avoids eager items()/tuple() on
                        # unbounded caller mappings before the node budget.
                        for key in value:
                            if type(key) is not str or not key:
                                raise ValueError("invalid_error_facts")
                            snapshot(key, depth + 1)
                            child, json_child = snapshot(value[key], depth + 1)
                            immutable[key], plain[key] = child, json_child
                    except Exception:
                        failed = True
                    if failed:
                        raise ValueError("invalid_error_facts")
                    return MappingProxyType(immutable), plain
                immutable, plain = [], []
                for child in value:
                    copied, json_child = snapshot(child, depth + 1)
                    immutable.append(copied)
                    plain.append(json_child)
                return tuple(immutable), plain
            finally:
                active.remove(token)
        raise ValueError("invalid_error_facts")

    if not isinstance(document.facts, Mapping):
        raise ValueError("invalid_error_facts")
    copied, plain = snapshot(document.facts)
    sources = document.sources
    if type(sources) not in (tuple, list):
        raise ValueError("invalid_error_facts")
    copied_sources = []
    for source in sources:
        if type(source) is not str or not source:
            raise ValueError("invalid_error_facts")
        copied_sources.append(snapshot(source)[0])
    if (
        set(copied) not in ({"status", "error"}, {"status", "error", "supplement"})
        or type(copied["status"]) is not str
        or copied["status"] != "error"
        or not isinstance(copied["error"], Mapping)
        or set(copied["error"]) != {"code", "message"}
        or type(copied["error"]["code"]) is not str
        or type(copied["error"]["message"]) is not str
        or copied["error"]["code"] != code
        or copied["error"]["message"] != message
        or "supplement" in copied
        and not isinstance(copied["supplement"], Mapping)
    ):
        raise ValueError("invalid_error_facts")
    # The actual Tool publisher uses default JSON separators and this UTF-8
    # policy. Conversion below only sees the bounded detached native JSON tree.
    failed = False
    try:
        encoded = json.dumps(plain, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        failed = True
    if failed:
        raise ValueError("invalid_error_facts")
    if len(encoded) > 256 * 1024:
        raise ValueError("public_facts_exceed_limits")
    object.__setattr__(
        result,
        "model_facts",
        FactDocument(copied, tuple(copied_sources), document.schema_version),
    )
