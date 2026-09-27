"""Policy checks used by the public invocation gateway.

The policy module deliberately contains no registry or execution logic.  It
only answers whether a trusted invocation source and a static capability
declaration fit the small public/read-only surface implemented in B02.
"""

from __future__ import annotations

from ..api.contexts import InvocationOrigin
from ..api.manifests import (
    CapabilityDescriptor,
    CapabilityEffect,
    InvocationPolicy,
    PrivacyFloor,
)


def origin_allowed(origin: object, expected: InvocationOrigin) -> bool:
    """Return whether *origin* is the source accepted by an entry point."""

    return isinstance(origin, InvocationOrigin) and origin is expected


def supports_public_read_only(capability: object) -> bool:
    """Return whether this capability belongs to the public read-only class.

    Readiness is decided separately by Admission/HealthResolver; static
    configuration, source and dependency declarations do not make a public
    read-only capability ineligible on their own.
    """

    return isinstance(capability, CapabilityDescriptor) and (
        capability.privacy_floor is PrivacyFloor.PUBLIC
        and capability.effect is CapabilityEffect.READ_ONLY
    )


def tool_allowed(capability: object) -> bool:
    """Return whether a capability may be reached through an LLM Tool."""

    return (
        supports_public_read_only(capability)
        and isinstance(capability, CapabilityDescriptor)
        and capability.invocation_policy is InvocationPolicy.NATURAL_LANGUAGE_ALLOWED
    )


__all__ = ["origin_allowed", "supports_public_read_only", "tool_allowed"]
