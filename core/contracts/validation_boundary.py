"""Explicit Core receiving-boundary structural validation.

SDK constructors are descriptive. This validator owns the migrated invariants,
including nested instances manufactured without a constructor.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from importlib import import_module
from types import MappingProxyType

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
_NONE_TYPE = type(None)


def _catalog():
    global _VALIDATORS, _TYPES
    if _VALIDATORS is None:
        checks, types = {}, set()
        for leaf in _MODULES:
            module = import_module(__package__ + "." + leaf)
            for name, value in vars(module).items():
                if isinstance(value, type) and is_dataclass(value):
                    # MRO membership below is equivalent to nominal subclass
                    # checks only for ordinary classes, without virtual ABCs
                    # or metaclass-defined __instancecheck__ hooks.
                    if type(value) is not type:
                        raise TypeError("contract catalog requires nominal dataclasses")
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


def _contract_subclass(item, kind, types):
    # Read the real MRO, bypassing a hostile subclass metaclass's attribute
    # override. Cost follows this value's ancestry, not the entire catalog.
    if not types.isdisjoint(type.__getattribute__(kind, "__mro__")[1:]):
        return True
    # Nominal isinstance also recognizes an apparent __class__. Keep rejecting
    # such fabricated contract instances rather than treating them as scalars.
    apparent = getattr(item, "__class__", kind)
    if apparent is kind:
        return False
    if isinstance(apparent, type):
        return not types.isdisjoint(type.__getattribute__(apparent, "__mro__"))
    # Exotic class-like __bases__ proxies retain the previous conservative
    # behavior; ordinary schema JSON never uses this path.
    return any(isinstance(item, contract) for contract in types)


def validate_contract(value):
    checks, types = _catalog()
    seen, active = set(), set()

    def visit(item, depth=0):
        if depth > 64:
            raise ValueError("contract nesting limit exceeded")
        kind = type(item)
        # Exact builtins cannot contain contracts or override __class__/MRO.
        # Subclasses and proxies still require the original nominal checks.
        if (
            kind is str
            or kind is int
            or kind is float
            or kind is bool
            or kind is _NONE_TYPE
            or kind is bytes
        ):
            return
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
                if (
                    kind.__module__ == "yomihime_game_link_sdk.subscriptions"
                    and kind.__name__ == "ScheduleDescriptor"
                ):
                    from .subscriptions import preflight_schedule_parameters

                    preflight_schedule_parameters(item)
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
        elif (
            kind is not dict
            and kind is not list
            and kind is not tuple
            and kind is not MappingProxyType
            and _contract_subclass(item, kind, types)
        ):
            raise TypeError("contract subclasses are not admitted")
        elif kind is dict or kind is MappingProxyType or isinstance(item, Mapping):
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
        elif kind is tuple or kind is list or isinstance(item, (tuple, list)):
            for child in item:
                visit(child, depth + 1)

    visit(value)
    return value
