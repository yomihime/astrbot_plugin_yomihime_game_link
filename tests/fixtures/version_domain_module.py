"""SDK/stdlib-only version-domain test module; no Host/Core authority."""
import yomihime_game_link_sdk as ygl


def result(status=ygl.ResultStatus.SUCCESS):
    if status is ygl.ResultStatus.ERROR:
        error = ygl.ErrorDetail(ygl.ErrorCode.NO_RECORDS, "No records; choose another source.")
        return ygl.CapabilityResult("version-error", status, error=error,
            model_facts=ygl.FactDocument({"status": "error", "error": {
                "code": error.code.value, "message": error.message},
                "supplement": {"archive": {"available": None}}}))
    return ygl.CapabilityResult("version-result", status,
        document=ygl.DisplayDocument("Version fixture", "public", (ygl.TextBlock("public result"),)),
        model_facts=ygl.FactDocument({"status": status.value, "fixture": "version-domain"}))


class Handler:
    def __init__(self):
        self.services = None
        self.result = result()
        self.calls = []
        self.bound = None
        self.message = None

    async def invoke(self, context, parameters):
        self.bound = await self.services.scopes.bind(context)
        if context.origin is ygl.InvocationOrigin.LLM_TOOL:
            self.message = await self.bound.message.read()
        self.calls.append(context)
        return self.result


class Instance:
    def __init__(self, handler):
        self.handler = handler
        self.starts = 0
        self.stops = 0

    def handlers(self):
        return ygl.ModuleHandlers({"read": self.handler}, {}, {})

    async def start(self):
        self.starts += 1

    async def stop(self):
        self.stops += 1

    async def check_health(self):
        return ygl.HealthReport({"read": ygl.CapabilityHealth(ygl.HealthStatus.AVAILABLE)})


class Factory:
    def __init__(self, handler=None):
        self.handler = handler or Handler()
        self.calls = []

    async def create(self, services):
        self.calls.append(services)
        self.handler.services = services
        return Instance(self.handler)
