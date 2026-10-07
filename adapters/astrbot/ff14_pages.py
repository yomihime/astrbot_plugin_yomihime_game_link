"""Exact public read-only routes over the Runtime's bounded projections."""

from __future__ import annotations

import asyncio

from .runtime import PLUGIN_NAME
from .web_public import QUERY_ENDPOINTS, WebPublicRejected

_READ_FAILED = "状态暂时无法读取，请稍后重试。"
_CLOSED = "页面状态读取已停止，请重新打开插件页面。"


class FF14Pages:
    """Register exact GET handlers; host authentication adds no Core authority."""

    def __init__(self, context: object, runtime: object) -> None:
        self._context = context
        self._runtime = runtime
        self._closed = True
        self._generation = 0
        self._registrations: list[tuple[str, object]] = []
        self._query_states: dict[int, object] = {}
        self._body_tasks: set[asyncio.Task] = set()

    def register(self) -> None:
        if not self._closed:
            return
        self._closed = False
        self._generation += 1
        try:
            for endpoint, include_values in (
                ("overview", False),
                ("settings", True),
                ("catalog", False),
            ):
                handler = self._handler(include_values, catalog=endpoint == "catalog")
                route = f"/{PLUGIN_NAME}/{endpoint}"
                # Track before registration so a host exception after insertion
                # cannot leave a half-registered route behind.
                self._registrations.append((route, handler))
                self._context.register_web_api(
                    route, handler, ["GET"], "游戏连结公共只读状态"
                )
            route = f"/{PLUGIN_NAME}/web-status"
            handler = self._web_status_handler()
            self._registrations.append((route, handler))
            self._context.register_web_api(route, handler, ["GET"], "网页公开查询状态")
            for endpoint in QUERY_ENDPOINTS:
                route = f"/{PLUGIN_NAME}/queries/{endpoint}"
                handler = self._query_handler(endpoint)
                self._registrations.append((route, handler))
                self._context.register_web_api(route, handler, ["POST"], "公开只读查询")
        except BaseException:
            self.close()
            raise

    def _handler(self, include_values: bool, *, catalog: bool = False):
        generation = self._generation

        async def read():
            if self._closed or generation != self._generation:
                return {"status": "error", "message": _CLOSED, "data": {}}
            try:
                if catalog:
                    data = self._runtime.public_module_catalog()
                else:
                    data = await self._runtime.public_ff14_page_state(
                        include_values=include_values
                    )
            except Exception:
                if self._closed or generation != self._generation:
                    return {"status": "error", "message": _CLOSED, "data": {}}
                return {"status": "error", "message": _READ_FAILED, "data": {}}
            if self._closed or generation != self._generation:
                return {"status": "error", "message": _CLOSED, "data": {}}
            return {"status": "ok", "message": "", "data": data}

        return read

    def _web_status_handler(self):
        generation = self._generation

        async def read():
            if self._closed or generation != self._generation:
                return {"status": "error", "message": _CLOSED, "data": {}}
            try:
                return {
                    "status": "ok",
                    "message": "",
                    "data": self._runtime.public_web_status(),
                }
            except Exception:
                return {"status": "error", "message": _READ_FAILED, "data": {}}

        return read

    def _body_done(self, task):
        self._body_tasks.discard(task)
        if not task.cancelled():
            task.exception()

    def _query_handler(self, endpoint: str):
        generation = self._generation
        suffix = f"{PLUGIN_NAME}/queries/{endpoint}"
        v1_path = f"/api/v1/plugins/extensions/{suffix}"
        legacy_path = f"/api/plug/{suffix}"

        async def query():
            from astrbot.api.web import json_response, request

            state, body_task = None, None
            try:
                if self._closed or generation != self._generation:
                    raise WebPublicRejected("entry_unavailable")
                if request.path == v1_path and request.method == "POST":
                    # The v1 identity can include API keys; it grants no query
                    # authority. Legacy's ordinary JWT gate must run afresh.
                    return json_response(
                        {},
                        status_code=307,
                        headers={"Location": legacy_path, "Cache-Control": "no-store"},
                    )
                state = self._runtime.begin_public_web(request, endpoint, legacy_path)
                self._query_states[id(state)] = state
                content_types = request.headers.getlist("content-type")
                if len(content_types) != 1 or content_types[0].lower() not in (
                    "application/json",
                    "application/json; charset=utf-8",
                ):
                    raise WebPublicRejected("request_rejected")
                remaining = self._runtime.public_web_remaining(state)
                body_task = asyncio.create_task(
                    request.body(), name="ygl:public_web_body"
                )
                self._body_tasks.add(body_task)
                body_task.add_done_callback(self._body_done)
                done, _ = await asyncio.wait({body_task}, timeout=remaining)
                if not done:
                    raise WebPublicRejected("session_expired")
                if (
                    self._closed
                    or generation != self._generation
                    or body_task.cancelled()
                ):
                    raise WebPublicRejected("entry_unavailable")
                body = body_task.result()
                self._runtime.public_web_remaining(state)
                parameters = self._runtime.public_query_parameters(endpoint, body)
                data = await self._runtime.invoke_public_web(
                    state, endpoint, parameters
                )
                if self._closed or generation != self._generation:
                    raise WebPublicRejected("entry_unavailable")
                return json_response(
                    {"status": "ok", "message": "", "data": data},
                    headers={"Cache-Control": "no-store"},
                )
            except WebPublicRejected as exc:
                code = exc.args[0]
                codes = {
                    "entry_unavailable": 503,
                    "request_rejected": 400,
                    "origin_invalid": 403,
                    "origin_mismatch": 403,
                    "session_expired": 401,
                    "result_rejected": 502,
                }
                return json_response(
                    {
                        "status": "error",
                        "message": "网页查询不可用，请检查配置、会话与输入后重试。",
                        "code": code if code in codes else "request_rejected",
                        "data": {},
                    },
                    status_code=codes.get(code, 400),
                    headers={"Cache-Control": "no-store"},
                )
            except Exception:
                return json_response(
                    {
                        "status": "error",
                        "message": _READ_FAILED,
                        "code": "query_failed",
                        "data": {},
                    },
                    status_code=503,
                    headers={"Cache-Control": "no-store"},
                )
            finally:
                if body_task is not None and not body_task.done():
                    body_task.cancel()
                if state is not None:
                    self._query_states.pop(id(state), None)
                    self._runtime.finish_public_web(state)

        return query

    def close(self) -> None:
        """Remove only our handlers from 4.28.2's public registration list."""
        self._closed = True
        self._generation += 1
        for state in tuple(self._query_states.values()):
            self._runtime.finish_public_web(state)
        for task in tuple(self._body_tasks):
            if not task.done():
                task.cancel()
        registrations, self._registrations = self._registrations, []
        apis = getattr(self._context, "registered_web_apis", None)
        if isinstance(apis, list):
            apis[:] = [
                entry
                for entry in apis
                if not any(
                    entry[0] == route and entry[1] is handler
                    for route, handler in registrations
                )
            ]


__all__ = ["FF14Pages"]
