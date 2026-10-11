"""Neutral consumers: import only the public SDK and standard library."""

import yomihime_game_link_sdk as ygl


class CacheConsumer:
    def __init__(self, services=None):
        self.services = services
        self.lookup = None

    async def invoke(self, context, parameters):
        bound = await self.services.scopes.bind(context)
        self.lookup = await bound.cache.lookup(ygl.CacheQuery("consumer"))
        return ygl.CapabilityResult(
            "sdk-cache-consumer",
            ygl.ResultStatus.SUCCESS,
            ygl.DisplayDocument("SDK consumer", "local", (ygl.TextBlock("checked"),)),
        )


class SubscriptionConsumer:
    def __init__(self, services):
        self.services = services

    async def create(self, context, request):
        return await self.services.subscriptions.create_request(context, request)

    async def revise(self, context, request):
        return await self.services.subscriptions.revise_request(context, request)

    async def cancel(self, context, subscription_id, revision):
        await self.services.subscriptions.cancel(
            context, subscription_id, expected_revision=revision
        )
