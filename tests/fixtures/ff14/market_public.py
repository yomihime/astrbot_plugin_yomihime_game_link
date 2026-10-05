"""Public SDK samples from isolated handlers and synthetic upstream, no network."""

import asyncio
import json

from ygl_test_subject.api.services import HttpResponse

from tests.host.test_market_integration import MarketHostIntegrationTests


async def market_public_samples() -> dict:
    fixture = MarketHostIntegrationTests()
    fixture.setUp()
    try:
        await fixture.start()
        samples = {}
        parameters = dict(
            query="44091", server="90001", quality="hq", intent="listings"
        )
        samples["success"] = await fixture.public(parameters)
        samples["cache"] = await fixture.public(parameters)
        samples["selection"] = await fixture.public(
            dict(query="Synthetic Item", quality="hq", intent="min")
        )
        pending = samples["selection"]["model_facts"]["selection"]
        samples["selection_resolved"] = await fixture.public(
            {
                "selection": {
                    "batch_id": pending["batch_id"],
                    "generation": pending["generation"],
                    "item_id": 44091,
                }
            }
        )
        samples["error"] = await fixture.public(dict(query="44091", quality=None))

        async def partial(request):
            if "/Japan/" in request.path:
                return HttpResponse(429, {}, b"{}")

        fixture.transport.callback = partial
        samples["partial"] = await fixture.public(dict(query="44091", region="global"))

        async def empty(request):
            if "/aggregated/" in request.path:
                return {"results": [], "failedItems": []}

        fixture.transport.callback = empty
        samples["empty"] = await fixture.public(
            dict(query="44091", quality="nq", intent="min")
        )
        return samples
    finally:
        if hasattr(fixture, "runtime"):
            await fixture.runtime.terminate()
        fixture.tearDown()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(market_public_samples()), ensure_ascii=True))
