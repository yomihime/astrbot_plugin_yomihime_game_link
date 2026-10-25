"""Join a verified current management request to private authorization flows."""

from __future__ import annotations

import base64
from contextvars import ContextVar
from dataclasses import asdict

from .source_authorizations import (
    AuthorizationError,
    AuthorizationFlowPolicy,
    SourceAuthorizations,
)


class AuthorizationManagement:
    def __init__(self, policy):
        self._policy = policy
        self._current = ContextVar(
            "source_authorization_management_request", default=None
        )
        self._managers = {}

    def _context(self, binding=None):
        context = self._current.get()
        if context is None or (binding is not None and context["binding"] != binding):
            raise AuthorizationError("session_unavailable")
        return context

    async def _guard(self, binding):
        await self._context(binding)["guard"]()

    async def _request(self, request):
        context = self._context()
        return await context["http"].fetch(
            request, _check_authority=context["guard"], _authorization_request=True
        )

    async def _commit(self, binding, material):
        context = self._context(binding)
        await context["guard"]()
        receipt = await context["commit"](dict(material))
        context["receipt"] = receipt

    async def execute(self, *, binding, flow_id, step, session_id, guard, commit, http):
        definitions = self._policy.authorizations.get(binding.owner_id, {})
        if flow_id not in definitions:
            raise AuthorizationError("flow_unavailable")
        manager = self._managers.get(binding.owner_id)
        if manager is None:
            policies = {
                name: AuthorizationFlowPolicy.from_descriptor(row["descriptor"])
                for name, row in definitions.items()
            }
            manager = SourceAuthorizations(
                policies=policies,
                request=self._request,
                guard=self._guard,
                credential_update=self._commit,
            )
            self._managers[binding.owner_id] = manager
        context = {
            "binding": binding,
            "guard": guard,
            "commit": commit,
            "http": http,
            "receipt": None,
        }
        token = self._current.set(context)
        try:
            await guard()
            if (
                step in {"poll", "cancel", "qr"}
                and manager._session(session_id, binding).policy.flow_id != flow_id
            ):
                raise AuthorizationError("session_unavailable")
            if step == "begin":
                view = await manager.begin(flow_id, binding)
            elif step == "poll":
                view = await manager.poll(session_id, binding)
            elif step == "cancel":
                view = await manager.cancel(session_id, binding)
            elif step == "qr":
                png = await manager.qr_png(session_id, binding)
                return {"qr_png": base64.b64encode(png).decode("ascii")}
            elif step == "revoke":
                await manager.revoke(flow_id, binding)
                return {"revoked": True, "receipt": context["receipt"]}
            else:
                raise AuthorizationError("invalid_action")
            return {"session": asdict(view), "receipt": context["receipt"]}
        finally:
            self._current.reset(token)

    def invalidate(self, *, owner_id=None, page_session_id=None):
        for manager in self._managers.values():
            manager.invalidate(owner_id=owner_id, page_session_id=page_session_id)

    async def close(self):
        for manager in self._managers.values():
            await manager.close()
        self._managers.clear()
