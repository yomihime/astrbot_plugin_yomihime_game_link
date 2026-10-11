"""SDK-only offline module for S2-B actual receiving tests."""

from __future__ import annotations

import yomihime_game_link_sdk as ygl


class ErrorSupplementHandler:
    def __init__(self):
        self.services = None
        self.result = None
        self.calls = []
        self.bound = None

    async def invoke(self, context, parameters):
        self.bound = await self.services.scopes.bind(context)
        self.calls.append(context)
        return self.result


def error_result(facts=None):
    return ygl.CapabilityResult(
        "neutral-error",
        ygl.ResultStatus.ERROR,
        error=ygl.ErrorDetail(
            ygl.ErrorCode.NO_RECORDS, "No matching records; choose another source."
        ),
        model_facts=None if facts is None else ygl.FactDocument(facts),
    )


def envelope(**extra):
    return {
        "status": "error",
        "error": {
            "code": "no_records",
            "message": "No matching records; choose another source.",
        },
        **extra,
    }
