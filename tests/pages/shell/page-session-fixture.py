"""JSON-lines adapter to the real bounded registry; synthetic identities only."""

import json
import sys

from ygl_test_subject.adapters.astrbot.page_sessions import HostModulePageSessions
from ygl_test_subject.services.module_pages import ModulePageGrant

import tests  # noqa: F401

now = [1000.0]
registry = HostModulePageSessions(wall_clock=lambda: now[0])
binding = ModulePageGrant("example/demo", "config", "synthetic", 1, {"sample"}, set())
digest = registry.bearer_digest("synthetic-not-a-real-jwt")
for line in sys.stdin:
    request = json.loads(line)
    now[0] = request["now"]
    try:
        if not request.get("authorized", True):
            raise PermissionError("Host session revoked")
        common = dict(
            subject="synthetic", bearer_digest=digest, binding=binding, generation=1
        )
        if request["action"] == "issue":
            result = registry.issue(
                **common,
                expiry=now[0] + 30,
                previous_session=request.get("previous_session"),
            )
        elif request["action"] == "require":
            registry.require(request["page_session"], **common)
            result = {"current": True}
        else:
            registry.revoke(
                request["page_session"], subject="synthetic", bearer_digest=digest
            )
            result = {"closed": True}
        print(json.dumps({"id": request["id"], "result": result}), flush=True)
    except (PermissionError, RuntimeError) as error:
        print(
            json.dumps({"id": request["id"], "error": type(error).__name__}), flush=True
        )
registry.close()
