"""Four bounded POST operations; the independent public page remains read-only."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from time import time

from ...api.administration import AdminAuthorizationDenied, AdminOperation
from ...api.services import (
    ConfigFieldUpdate,
    ConfigPatch,
    ConfigPatchMode,
    ConfigTarget,
)
from ...core.ports import RevisionConflict
from .runtime import PLUGIN_NAME
from .web_admin import bounded_body, verified_dashboard_request

_OPERATIONS = {
    "read": AdminOperation.READ_CONFIG,
    "catalog": AdminOperation.READ_CONFIG,
    "update": AdminOperation.UPDATE_CONFIG,
    "rollback": AdminOperation.ROLLBACK_CONFIG,
    "recover": AdminOperation.RECOVER_CONFIG,
}


class AdminPages:
    def __init__(self, context, runtime):
        self.context, self.runtime = context, runtime
        self.closed = True
        self.registrations = []
        self.requests = {}
        self.generation = 0

    def register(self):
        if not self.closed:
            return
        self.closed = False
        self.generation += 1
        try:
            for endpoint in _OPERATIONS:
                path = f"/{PLUGIN_NAME}/admin/{endpoint}"
                handler = self._handler(endpoint)
                self.registrations.append((path, handler))
                self.context.register_web_api(path, handler, ["POST"], "四字段有限管理")
        except BaseException:
            self.close()
            raise

    def close(self):
        self.closed = True
        self.generation += 1
        for source, context in tuple(self.requests.values()):
            source.end(context)
        self.requests.clear()
        apis = getattr(self.context, "registered_web_apis", None)
        if isinstance(apis, list):
            apis[:] = [
                entry
                for entry in apis
                if not any(
                    entry[0] == path and entry[1] is handler
                    for path, handler in self.registrations
                )
            ]
        self.registrations.clear()

    def _handler(self, endpoint):
        generation = self.generation
        legacy = f"/api/plug/{PLUGIN_NAME}/admin/{endpoint}"
        v1 = f"/api/v1/plugins/extensions/{PLUGIN_NAME}/admin/{endpoint}"

        async def handler():
            from astrbot.api.web import json_response, request

            context, watcher, source = None, None, None
            task = asyncio.current_task()
            try:
                if self.closed or self.generation != generation:
                    raise AdminAuthorizationDenied
                if request.method == "POST" and request.path == v1:
                    return json_response(
                        {},
                        status_code=307,
                        headers={"Location": legacy, "Cache-Control": "no-store"},
                    )
                concrete = request._get_current()
                raw, subject, expiry = verified_dashboard_request(concrete, legacy)
                core, source = self.runtime.management_entry()
                alive = {"value": True}
                context = source.issue(
                    subject=subject,
                    request=raw,
                    expiry=expiry,
                    operations={_OPERATIONS[endpoint]},
                    resources=core.admin_operations.ordinary_resources(),
                    live=lambda actual: alive["value"]
                    and actual is raw
                    and not self.closed
                    and self.generation == generation
                    and self.runtime.management_current(core, source),
                )
                self.requests[id(context)] = (source, context)
                data = await asyncio.wait_for(
                    bounded_body(concrete), timeout=max(0.001, expiry - time())
                )
                source.check(
                    context,
                    _OPERATIONS[endpoint],
                    core.admin_operations.ordinary_resources(),
                )

                async def disconnect():
                    while alive["value"]:
                        if await raw.is_disconnected():
                            alive["value"] = False
                            source.end(context)
                            task.cancel()
                            return
                        await asyncio.sleep(0.02)

                watcher = asyncio.create_task(disconnect(), name="ygl:admin-disconnect")
                if await raw.is_disconnected():
                    alive["value"] = False
                    raise AdminAuthorizationDenied
                result = await self._invoke(endpoint, core, data, context)
                source.check(
                    context,
                    _OPERATIONS[endpoint],
                    core.admin_operations.ordinary_resources(),
                )
                return json_response(
                    {"status": "ok", "data": result, "message": ""},
                    headers={"Cache-Control": "no-store"},
                )
            except RevisionConflict:
                code, status = "revision_conflict", 409
            except AdminAuthorizationDenied:
                code, status = "admin_authorization_denied", 403
            except (ValueError, TypeError):
                code, status = "invalid_config", 400
            except Exception:
                code, status = "operation_unavailable", 503
            finally:
                if context is not None:
                    alive["value"] = False
                    source.end(context)
                    self.requests.pop(id(context), None)
                if watcher is not None:
                    watcher.cancel()
                    with suppress(asyncio.CancelledError):
                        await watcher
            return json_response(
                {
                    "status": "error",
                    "code": code,
                    "message": "配置版本冲突，请刷新并重新核对后修改。"
                    if code == "revision_conflict"
                    else "未取得成功确认，请刷新核对配置与版本后重试。",
                    "data": {},
                },
                status_code=status,
                headers={"Cache-Control": "no-store"},
            )

        return handler

    async def _invoke(self, endpoint, core, data, context):
        ops = core.admin_operations
        if endpoint == "catalog":
            if data:
                raise ValueError("catalog parameters are not accepted")
            return await ops.ordinary_catalog(authorization=context)
        if endpoint == "read":
            if data:
                raise ValueError("read parameters are not accepted")
            return await ops.ordinary_snapshot(authorization=context)
        if endpoint == "update":
            if (
                set(data) != {"module_id", "expected_revision", "updates"}
                or type(data["updates"]) is not list
                or not 1 <= len(data["updates"]) <= 3
            ):
                raise ValueError("invalid update")
            resources = ops.ordinary_resources()
            target = ConfigTarget(ops.config_principal_id, data["module_id"])
            if target not in resources:
                raise ValueError("invalid target")
            declarations = tuple(
                f.declaration
                for f in ops._ordinary_migration.fields
                if f.target == target
            )
            updates = []
            for update in data["updates"]:
                if (
                    type(update) is not dict
                    or set(update)
                    not in ({"field", "mode"}, {"field", "mode", "value"})
                    or update["field"] not in resources[target]
                    or update["mode"] not in ("replace", "clear")
                    or (update["mode"] == "replace") != ("value" in update)
                ):
                    raise ValueError("invalid field update")
                updates.append(
                    ConfigFieldUpdate(
                        update["field"],
                        ConfigPatchMode(update["mode"]),
                        value=update.get("value"),
                    )
                )
            result = await ops.update_config(
                None,
                target.module_id,
                ConfigPatch(data["expected_revision"], tuple(updates), declarations),
                authorization=context,
            )
            return {"module_id": result.module_id, "revision": result.revision}
        keys = (
            {"expected_revisions"}
            if endpoint == "rollback"
            else {"expected_revisions", "complete_from_current"}
        )
        if set(data) != keys or type(data["expected_revisions"]) is not dict:
            raise ValueError("invalid recovery parameters")
        revisions = {
            ConfigTarget(ops.config_principal_id, module_id): revision
            for module_id, revision in data["expected_revisions"].items()
        }
        if endpoint == "rollback":
            return await ops.ordinary_rollback(revisions, authorization=context)
        if type(data["complete_from_current"]) is not bool:
            raise ValueError("explicit recovery intent is required")
        return await self.runtime.recover_management(
            core,
            revisions,
            authorization=context,
            complete_from_current=data["complete_from_current"],
        )
