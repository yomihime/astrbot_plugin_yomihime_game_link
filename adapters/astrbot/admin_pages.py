"""Four bounded POST operations; the independent public page remains read-only."""

from __future__ import annotations

import asyncio
import hmac
import inspect
import secrets
from math import isfinite
from time import time

from yomihime_game_link_sdk.declarations import ConfigUpdateMode
from yomihime_game_link_sdk.services import ConfigTarget

from ...core.contracts.administration import (
    AdminAuthorizationDenied,
    AdminOperation,
    ModuleUnloadReceipt,
)
from ...core.contracts.services import ConfigFieldUpdate, ConfigPatch
from ...core.contracts.validation_boundary import validate_contract
from ...core.ports import RevisionConflict
from ...infrastructure.managed_key_manager import ManagedKeyConflict
from ...infrastructure.private_storage import PrivateStorageError
from ...infrastructure.secret_store import SecretStoreUnavailable
from .runtime import PLUGIN_NAME
from .web_admin import bounded_body, bounded_font_upload, verified_dashboard_request

_OPERATIONS = {
    "font-status": AdminOperation.READ_CONFIG,
    "font-retry": AdminOperation.UPDATE_CONFIG,
    "font-upload": AdminOperation.UPDATE_CONFIG,
    "read": AdminOperation.READ_CONFIG,
    "sync-diagnostics": AdminOperation.READ_CONFIG,
    "catalog": AdminOperation.READ_CONFIG,
    "update": AdminOperation.UPDATE_CONFIG,
    "recover": AdminOperation.RECOVER_CONFIG,
    "credential-catalog": AdminOperation.READ_CONFIG,
    "credential-status": AdminOperation.READ_CONFIG,
    "credential-update": AdminOperation.UPDATE_CONFIG,
    "credential-readiness": AdminOperation.READ_CONFIG,
    "master-key-status": AdminOperation.READ_CONFIG,
    "master-key-retry": AdminOperation.UPDATE_CONFIG,
    "master-key-restore": AdminOperation.UPDATE_CONFIG,
    "master-key-reset": AdminOperation.UPDATE_CONFIG,
    "master-key-adopt-environment": AdminOperation.UPDATE_CONFIG,
    "master-key-migration-plan": AdminOperation.READ_CONFIG,
    "master-key-migration-execute": AdminOperation.UPDATE_CONFIG,
    "legacy-config-plan": AdminOperation.READ_CONFIG,
    "legacy-config-execute": AdminOperation.UPDATE_CONFIG,
    "modules": AdminOperation.LIST_MODULES,
    "module-enabled": AdminOperation.SET_ENABLED,
    "module-unload": AdminOperation.UNLOAD_MODULE,
    "module-config-current": AdminOperation.READ_CONFIG,
    "module-config-save": AdminOperation.UPDATE_CONFIG,
    "module-config-clear": AdminOperation.UPDATE_CONFIG,
    "module-credential-status": AdminOperation.READ_CONFIG,
    "module-credential-replace": AdminOperation.UPDATE_CONFIG,
    "module-credential-clear": AdminOperation.UPDATE_CONFIG,
    "module-credential-authorize": AdminOperation.UPDATE_CONFIG,
    "module-page-session": AdminOperation.READ_CONFIG,
    "module-page-session-close": AdminOperation.READ_CONFIG,
}

_MODULE_PAGE_OPERATIONS = frozenset(
    {
        "module-config-current",
        "module-config-save",
        "module-config-clear",
        "module-credential-status",
        "module-credential-replace",
        "module-credential-clear",
        "module-credential-authorize",
        "module-page-session",
        "module-page-session-close",
    }
)


class _DashboardRequestFence:
    """Reverify one real Host session at synchronous source/commit boundaries.

    Only an ephemeral keyed digest of Authorization is retained. The initial
    proof deadline is fixed; successful rechecks never renew request authority.
    """

    def __init__(self, request, path, raw, subject, expiry):
        self.request, self.path, self.raw = request, path, raw
        self.subject, self.expiry = subject, expiry
        self.key = secrets.token_bytes(32)
        try:
            if (
                raw is None
                or type(subject) is not str
                or not subject.strip()
                or type(expiry) not in (int, float)
                or not isfinite(expiry)
                or expiry <= time()
            ):
                raise ValueError
            self.authorization, self.origin, self.server, self.verifier_function = (
                self._identity()
            )
        except Exception:
            raise AdminAuthorizationDenied from None

    def _identity(self):
        authorizations = self.request.headers.getlist("authorization")
        origins = self.request.headers.getlist("origin")
        if (
            len(authorizations) != 1
            or type(authorizations[0]) is not str
            or len(origins) != 1
            or type(origins[0]) is not str
        ):
            raise AdminAuthorizationDenied
        server = self.request._request.app.state.dashboard_app_adapter._dashboard_server
        verifier = getattr(server, "_validate_dashboard_token", None)
        if not callable(verifier) or getattr(verifier, "__self__", None) is not server:
            raise AdminAuthorizationDenied
        digest = hmac.digest(self.key, authorizations[0].encode("utf-8"), "sha256")
        return digest, origins[0], server, getattr(verifier, "__func__", None)

    def current(self, actual):
        if actual is not self.raw or time() >= self.expiry:
            return False
        try:
            checked = verified_dashboard_request(self.request, self.path)
            if inspect.isawaitable(checked):
                if inspect.iscoroutine(checked):
                    checked.close()
                return False
            if type(checked) is not tuple or len(checked) != 3:
                return False
            raw, subject, expiry = checked
            digest, origin, server, function = self._identity()
            return (
                raw is self.raw
                and subject == self.subject
                and type(expiry) in (int, float)
                and isfinite(expiry)
                and time() < min(expiry, self.expiry)
                and hmac.compare_digest(digest, self.authorization)
                and origin == self.origin
                and server is self.server
                and function is self.verifier_function
            )
        except Exception:
            return False


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
            watcher_stop = {"value": False}
            result, code, status = None, None, None
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
                dashboard_fence = _DashboardRequestFence(
                    concrete, legacy, raw, subject, expiry
                )
                if endpoint in _MODULE_PAGE_OPERATIONS:
                    runtime_generation = self.runtime._generation
                    data = await asyncio.wait_for(
                        bounded_body(concrete), timeout=max(0.001, expiry - time())
                    )
                    # The runtime establishes the actual page/session grant.
                    # Client owner/epoch fields never issue a management proof.
                    result = await self.runtime.module_page_operation(
                        endpoint, data, request=concrete
                    )
                    if (
                        self.closed
                        or self.generation != generation
                        or self.runtime._generation != runtime_generation
                        or not self.runtime._ready
                        or self.runtime._closing
                        or not dashboard_fence.current(raw)
                    ):
                        raise AdminAuthorizationDenied
                    return json_response(
                        {"status": "ok", "data": result, "message": ""},
                        headers={"Cache-Control": "no-store"},
                    )
                credentials = endpoint.startswith("credential-")
                master_key = endpoint.startswith("master-key-")
                legacy_copy = endpoint.startswith("legacy-config-")
                modules = endpoint.startswith("module")
                core, source = self.runtime.management_entry(
                    credentials=credentials, modules=modules, master_key=master_key
                )
                resources = (
                    core.admin_operations.legacy_config_resources()
                    if legacy_copy
                    else core.admin_operations.master_key_resources()
                    if master_key
                    else core.admin_operations.credential_resources()
                    if credentials
                    else core.admin_operations.module_resources()
                    if modules
                    else core.admin_operations.ordinary_resources()
                )
                alive = {"value": True}
                context = source.issue(
                    subject=subject,
                    request=raw,
                    expiry=expiry,
                    operations={
                        AdminOperation.READ_CONFIG,
                        AdminOperation.UPDATE_CONFIG,
                    }
                    if legacy_copy
                    else {_OPERATIONS[endpoint], AdminOperation.SET_ENABLED}
                    if endpoint == "module-unload"
                    else {_OPERATIONS[endpoint]},
                    resources=resources,
                    live=lambda actual: alive["value"]
                    and actual is raw
                    and not self.closed
                    and self.generation == generation
                    and self.runtime.management_current(core, source)
                    and dashboard_fence.current(actual),
                )
                self.requests[id(context)] = (source, context)
                data = await asyncio.wait_for(
                    bounded_font_upload(concrete)
                    if endpoint == "font-upload"
                    else bounded_body(concrete),
                    timeout=max(0.001, expiry - time()),
                )
                source.check(
                    context,
                    _OPERATIONS[endpoint],
                    resources,
                )

                async def disconnect():
                    while alive["value"] and not watcher_stop["value"]:
                        disconnected = await raw.is_disconnected()
                        if watcher_stop["value"]:
                            return
                        if disconnected:
                            alive["value"] = False
                            source.end(context)
                            task.cancel()
                            return
                        await asyncio.sleep(0.02)

                watcher = asyncio.create_task(disconnect(), name="ygl:admin-disconnect")
                if await raw.is_disconnected():
                    alive["value"] = False
                    raise AdminAuthorizationDenied
                source.check(context, _OPERATIONS[endpoint], resources)
                result = await self._invoke(endpoint, core, data, context)
                source.check(
                    context,
                    _OPERATIONS[endpoint],
                    resources,
                )
            except RevisionConflict:
                code, status = "revision_conflict", 409
            except AdminAuthorizationDenied:
                code, status = "admin_authorization_denied", 403
            except SecretStoreUnavailable:
                code, status = "secret_encryption_unavailable", 503
            except (ManagedKeyConflict, PrivateStorageError) as error:
                code = error.code
                status = (
                    409
                    if code
                    in {
                        "stale_inventory",
                        "operation_changed",
                        "operation_incomplete",
                        "environment_key_conflict",
                        "key_conflict",
                        "requires_stopped_host",
                        "source_exclusivity_unproven",
                    }
                    else 422
                    if code == "key_does_not_decrypt_inventory"
                    else 400
                    if code in {"invalid_key", "confirmation_required"}
                    else 503
                )
            except (ValueError, TypeError):
                code, status = "invalid_config", 400
            except Exception:
                code, status = "operation_unavailable", 503
            finally:
                # Cancellation may be consumed inside Starlette/AnyIO receive.
                # Stop the monitor independently of the still-live authority.
                watcher_stop["value"] = True
                cancellation_pending = False
                try:
                    if watcher is not None:
                        watcher.cancel()
                        try:
                            await watcher
                        except asyncio.CancelledError:
                            cancellation_pending = (
                                task is not None and task.cancelling() > 0
                            )
                        except Exception:
                            if code is None:
                                code, status = "operation_unavailable", 503
                    cancellation_pending = cancellation_pending or (
                        task is not None and task.cancelling() > 0
                    )
                    if context is not None:
                        # Recheck after the final await, including wrapped error
                        # paths. The same callback also guards the SQLite COMMIT.
                        try:
                            source.check(context, _OPERATIONS[endpoint], resources)
                        except Exception:
                            code, status = "admin_authorization_denied", 403
                finally:
                    # Exact request ownership is released without another await,
                    # even when joining the monitor or checking authority fails.
                    if context is not None:
                        alive["value"] = False
                        try:
                            source.end(context)
                        finally:
                            entry = self.requests.get(id(context))
                            if (
                                entry is not None
                                and entry[0] is source
                                and entry[1] is context
                            ):
                                self.requests.pop(id(context), None)
                if cancellation_pending:
                    raise asyncio.CancelledError
            if code is None:
                return json_response(
                    {"status": "ok", "data": result, "message": ""},
                    headers={"Cache-Control": "no-store"},
                )
            return json_response(
                {
                    "status": "error",
                    "code": code,
                    # The official Host bridge forwards only message, then
                    # rejects with Error(message). Preserve this exact marker.
                    "message": "admin_authorization_denied"
                    if code == "admin_authorization_denied"
                    else "配置版本冲突，请刷新并重新核对后修改。"
                    if code == "revision_conflict"
                    else "原密钥格式无效，请输入标准 Base64 编码的 32 字节密钥；旧凭据未更改。"
                    if code == "invalid_key"
                    else "所填密钥与当前密钥不一致；旧凭据未更改，请核对后重试。"
                    if code == "key_conflict"
                    else "所填密钥未能解开现有凭据；旧凭据未被替换，请核对原密钥后重试。"
                    if code == "key_does_not_decrypt_inventory"
                    else "未取得成功确认，请刷新核对配置与版本后重试。",
                    "data": {},
                },
                status_code=status,
                headers={"Cache-Control": "no-store"},
            )

        return handler

    async def _invoke(self, endpoint, core, data, context):
        ops = core.admin_operations
        if endpoint == "legacy-config-plan":
            if set(data) != {"policy_id"}:
                raise ValueError("invalid legacy plan parameters")
            return await ops.legacy_config_plan(
                data["policy_id"], authorization=context
            )
        if endpoint == "legacy-config-execute":
            if set(data) != {
                "policy_id",
                "source_revision",
                "target_revision",
                "confirmed",
            }:
                raise ValueError("invalid legacy execute parameters")
            return await ops.legacy_config_execute(authorization=context, **data)
        if endpoint == "sync-diagnostics":
            if data:
                raise ValueError("diagnostic parameters must be empty")
            await core.admin_authorization.authorize(
                AdminOperation.READ_CONFIG,
                invocation=None,
                context=context,
                resources=ops.ordinary_resources(),
            )
            return core.sync_diagnostics.snapshot()
        if endpoint.startswith("font-"):
            current, source = self.runtime.management_entry()
            if current is not core:
                raise AdminAuthorizationDenied

            def check():
                source.check(context, _OPERATIONS[endpoint], ops.ordinary_resources())

            check()
            manager = self.runtime._managed_font
            if endpoint == "font-status":
                if data:
                    raise ValueError("invalid font status parameters")
                return manager.status()
            if endpoint == "font-retry":
                if data:
                    raise ValueError("invalid font retry parameters")
                return manager.retry()
            if set(data) != {"content"}:
                raise ValueError("invalid font upload parameters")
            return await manager.upload(data["content"], check)
        if endpoint in ("modules", "module-enabled", "module-unload"):
            from dataclasses import asdict

            if endpoint == "modules":
                if data:
                    raise ValueError("module list parameters must be empty")
                snapshots = await ops.list_modules(None, authorization=context)
                return {
                    "registry_revision": core.registry.snapshot().revision,
                    "modules": [asdict(item.status) for item in snapshots],
                }
            keys = {"module_id", "expected_registry_revision"}
            if endpoint == "module-enabled":
                keys.add("enabled")
            if set(data) != keys:
                raise ValueError("invalid module operation")
            if endpoint == "module-unload":
                receipt = await ops.unload_module(
                    None,
                    data["module_id"],
                    expected_registry_revision=data["expected_registry_revision"],
                    authorization=context,
                )
                if type(receipt) is not ModuleUnloadReceipt:
                    raise TypeError("invalid unload receipt")
                receipt.__post_init__()
                return {
                    "module_id": receipt.module_id,
                    "state": receipt.state,
                    "registry_revision": receipt.registry_revision,
                    "data_retained": receipt.data_retained,
                    "reopen_required": receipt.reopen_required,
                }
            return asdict(
                await ops.set_enabled(
                    None,
                    data["module_id"],
                    data["enabled"],
                    expected_registry_revision=data["expected_registry_revision"],
                    authorization=context,
                )
            )
        if endpoint == "credential-readiness":
            if data and (
                set(data) != {"diagnostics"} or data["diagnostics"] is not True
            ):
                raise ValueError("invalid readiness parameters")
            return await ops.credential_readiness(
                authorization=context, diagnostics=bool(data)
            )
        if endpoint == "master-key-migration-plan":
            if data:
                raise ValueError("invalid migration plan parameters")
            return await ops.storage_migration_plan(authorization=context)
        if endpoint == "master-key-migration-execute":
            return await ops.storage_migration_execute(data, authorization=context)
        if endpoint == "master-key-status":
            if data:
                raise ValueError("invalid master key status parameters")
            return await ops.master_key_status(authorization=context)
        if endpoint in {
            "master-key-retry",
            "master-key-restore",
            "master-key-reset",
            "master-key-adopt-environment",
        }:
            common = {"expected_revision", "expected_generation", "inventory_digest"}
            extra = (
                {"encoded_key"}
                if endpoint == "master-key-restore"
                else {"confirmation"}
                if endpoint == "master-key-reset"
                else set()
            )
            if set(data) != common | extra:
                raise ValueError("invalid master key parameters")
            if (
                type(data["expected_revision"]) is not int
                or data["expected_revision"] < 1
                or type(data["expected_generation"]) is not int
                or data["expected_generation"] < 0
                or type(data["inventory_digest"]) is not str
                or len(data["inventory_digest"]) != 64
                or any(c not in "0123456789abcdef" for c in data["inventory_digest"])
            ):
                raise ValueError("invalid master key fence")
            return await ops.master_key_change(
                authorization=context,
                action=endpoint.removeprefix("master-key-"),
                expected_revision=data["expected_revision"],
                expected_generation=data["expected_generation"],
                inventory_digest=data["inventory_digest"],
                encoded_key=data.get("encoded_key"),
                confirmation=data.get("confirmation"),
            )
        if endpoint in ("credential-catalog", "credential-status"):
            if data:
                raise ValueError("credential read parameters must be empty")
            return await (
                ops.credential_catalog(authorization=context)
                if endpoint == "credential-catalog"
                else ops.credential_status(authorization=context)
            )
        if endpoint == "credential-update":
            return await ops.credential_update(data, authorization=context)
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
                or not 1 <= len(data["updates"]) <= 128
            ):
                raise ValueError("invalid update")
            resources = ops.ordinary_resources()
            target = validate_contract(
                ConfigTarget(ops.config_principal_id, data["module_id"])
            )
            if target not in resources:
                raise ValueError("invalid target")
            declarations = ops.ordinary_declarations(target)
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
                        validate_contract(ConfigUpdateMode(update["mode"])),
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
        if endpoint != "recover":
            raise AdminAuthorizationDenied
        keys = {"expected_revisions"}
        if set(data) != keys or type(data["expected_revisions"]) is not dict:
            raise ValueError("invalid recovery parameters")
        revisions = {
            validate_contract(
                ConfigTarget(ops.config_principal_id, module_id)
            ): revision
            for module_id, revision in data["expected_revisions"].items()
        }
        return await self.runtime.recover_management(
            core,
            revisions,
            authorization=context,
        )
