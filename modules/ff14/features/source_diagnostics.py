"""Fixed item/market parse-failure projections through the bound SDK port."""

from ....yomihime_game_link_sdk import DiagnosticDeclaration
from ....yomihime_game_link_sdk.errors import OperationTimeout, ServiceUnavailable

EVENT = "FF14SourceParseFailure"
STAGES = {
    "item_search": "xivapi_items",
    "item_detail": "xivapi_items",
    "item_acquisition": "garland_items",
    "market_directory": "universalis_market",
    "market_aggregated": "universalis_market",
    "market_listings": "universalis_market",
}
DECLARATION = DiagnosticDeclaration(
    EVENT,
    {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "maxLength": 32,
                "enum": list(dict.fromkeys(STAGES.values())),
            },
            "stage": {"type": "string", "maxLength": 32, "enum": list(STAGES)},
            "category": {
                "type": "string",
                "maxLength": 32,
                "enum": ["payload_parse"],
            },
        },
        "required": ["source", "stage", "category"],
        "additionalProperties": False,
    },
)


async def report_parse_failure(port, stage):
    if port is None or stage not in STAGES:
        return
    try:
        await port.emit(
            EVENT,
            {"source": STAGES[stage], "stage": stage, "category": "payload_parse"},
        )
    except (PermissionError, OperationTimeout, ServiceUnavailable):
        raise
    except Exception:
        # Optional telemetry cannot replace a source result or parse failure.
        pass
