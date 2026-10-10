"""Explicit Core receiving-boundary structural validation.

SDK constructors are descriptive. This validator owns the migrated invariants,
including nested instances manufactured without a constructor.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from importlib import import_module

_MODULES = (
    "contexts",
    "display",
    "manifests",
    "results",
    "storage",
    "subscriptions",
    "services",
    "administration",
)
_VALIDATORS = None
_TYPES = None


def _catalog():
    global _VALIDATORS, _TYPES
    if _VALIDATORS is None:
        checks, types = {}, set()
        for leaf in _MODULES:
            module = import_module(__package__ + "." + leaf)
            for name, value in vars(module).items():
                if isinstance(value, type) and is_dataclass(value):
                    types.add(value)
                    check = vars(module).get("check_" + name)
                    if check is not None:
                        checks[value] = check
                    elif value.__module__.startswith(__package__ + "."):
                        check = vars(value).get("__post_init__")
                        if check is not None:
                            checks[value] = check
        _VALIDATORS, _TYPES = checks, frozenset(types)
    return _VALIDATORS, _TYPES


def validate_contract(value):
    checks, types = _catalog()
    seen, active = set(), set()

    def visit(item, depth=0):
        if depth > 64:
            raise ValueError("contract nesting limit exceeded")
        kind = type(item)
        if kind in types:
            token = id(item)
            if token in active:
                raise ValueError("contract cycle rejected")
            if token in seen:
                return
            active.add(token)
            try:
                # ERROR JSON must be bounded and detached before recursive
                # visits inspect any caller-controlled facts mapping.
                if (
                    kind.__module__ == "yomihime_game_link_sdk.results"
                    and kind.__name__ == "CapabilityResult"
                ):
                    from yomihime_game_link_sdk.results import ResultStatus

                    status = item.status
                    if (
                        status is ResultStatus.ERROR
                        or type(status) is str
                        and status == "error"
                    ):
                        from ..public_result import preflight_error_facts

                        preflight_error_facts(item)
                check = checks.get(kind)
                # HTTP headers have a dedicated safe normalization boundary;
                # run it before visiting a caller-controlled Mapping.
                if (
                    kind.__module__ == "yomihime_game_link_sdk.services"
                    and kind.__name__ in {"HttpRequest", "HttpResponse"}
                ):
                    check(item)
                    check = None
                for field in fields(kind):
                    if field.init:
                        visit(getattr(item, field.name), depth + 1)
                if check is not None:
                    check(item)
            except AttributeError:
                raise ValueError("contract fields are missing") from None
            finally:
                active.remove(token)
            seen.add(token)
        elif any(isinstance(item, contract) for contract in types):
            raise TypeError("contract subclasses are not admitted")
        elif isinstance(item, Mapping):
            failed = False
            try:
                pairs = tuple(item.items())
            except Exception:
                failed = True
            if failed:
                raise ValueError("contract mapping is invalid")
            for key, child in pairs:
                visit(key, depth + 1)
                visit(child, depth + 1)
        elif isinstance(item, (tuple, list)):
            for child in item:
                visit(child, depth + 1)

    visit(value)
    return value
