"""Thin native AstrBot entrypoint; business code lives in captured generations."""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Star, register

from .bootstrap.loader import ArtifactCache, CoreGeneration, validate_core_release


@register(
    "astrbot_plugin_yomihime_game_link",
    "yomihime",
    "如月怜的游戏连结：聚合游戏角色、战绩与资讯。",
    "0.2.0",
)
class YomihimeGameLink(Star):
    """Hold no canonical SDK, business objects or mutable resource imports."""

    def __init__(self, context, config=None):
        super().__init__(context, config)
        from astrbot.api.star import StarTools

        self._host_context = context
        self._host_config = config
        self._plugin_root = Path(__file__).resolve().parent
        self._data_root = Path(
            StarTools.get_data_dir("astrbot_plugin_yomihime_game_link")
        )
        self._runtime = None
        self._generation = None
        self._pages = None
        self._admin_pages = None
        self._start_task = None
        self._stopping = False
        self._state = "initializing"

    async def initialize(self):
        await super().initialize()
        if self._start_task is None:
            self._start_task = asyncio.create_task(
                self._start_generation(), name="ygl-core-acquire"
            )
            self._start_task.add_done_callback(self._observe_start)
        # Native Host callbacks may hold their own management lock. A parked
        # predecessor must never make initialize wait on that lock's release.
        await asyncio.sleep(0)

    def _observe_start(self, work):
        if work.cancelled():
            return
        error = work.exception()
        if error is not None:
            self._state = "failed"
            import logging

            logging.getLogger(__name__).error(
                "Core generation failed: %s", type(error).__name__
            )

    async def _start_generation(self):
        cache = ArtifactCache(self._data_root)

        async def validate(raw, signature, bundle):
            descriptor = validate_core_release(raw, signature, bundle)
            if bundle.release_doc["plugin_id"] != {
                "author": "yomihime",
                "name": "astrbot_plugin_yomihime_game_link",
            }:
                raise ValueError("core_identity_mismatch")
            return descriptor

        try:
            bundle, _, key = await cache.capture(
                self._plugin_root, kind="core", validate=validate
            )
        except BaseException:
            cache.close()
            self._state = "failed"
            raise
        # This comparison comes only from the fully captured, signed installed
        # candidate. A corrupt installation cannot supply a migration proof.
        recovery_plan = cache.core_recovery_plan(bundle)
        self._installed_version = bundle.release_doc["release"]
        self._installed_digest = key
        pins = recovery_plan.pins
        try:
            await self._run_candidate(cache, bundle, key, fallback=False)
            return
        except asyncio.CancelledError:
            raise
        except Exception as initial_error:
            generation = self._generation
            if generation is None:
                bundle.release()
                cache.close()
            else:
                generation.sync_fence()
                self._state = "cleanup_pending"
                while not self._stopping:
                    quiet = await generation.terminate(timeout=0.25)
                    if quiet["state"] == "quiet":
                        break
                if self._stopping:
                    return
            if self._stopping:
                return
            for pin in pins:
                if pin is None or pin == key:
                    continue
                recovery = ArtifactCache(self._data_root)

                async def validate_cached(raw, signature, cached):
                    return await validate(raw, signature, cached)

                try:
                    cached, _, cached_key = await recovery.load_core_fallback(
                        pin, recovery=recovery_plan, validate=validate_cached
                    )
                except Exception:
                    recovery.close()
                    continue
                self._generation = self._runtime = None
                try:
                    await self._run_candidate(
                        recovery, cached, cached_key, fallback=True
                    )
                    return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._generation.sync_fence()
                    self._state = "cleanup_pending"
                    while not self._stopping:
                        if (await self._generation.terminate(timeout=0.25))[
                            "state"
                        ] == "quiet":
                            break
                    if self._stopping:
                        return
            self._state = "failed"
            raise initial_error

    async def _run_candidate(self, cache, bundle, key, *, fallback):
        if cache.check_core_admission(bundle, fallback=fallback) != key:
            raise ValueError("core_admission_digest_mismatch")
        generation = CoreGeneration(self, self._host_context, self._data_root, bundle)
        self._generation = generation
        generation.cache = cache
        try:
            while not self._stopping:
                result = await generation.acquire_admitted(cache)
                if result["state"] == "acquired":
                    break
                self._state = result["state"]
                if result["state"] == "rejected":
                    raise RuntimeError("core_owner_rejected")
                await asyncio.sleep(0.25)
            if self._stopping:
                if generation.receipt is None:
                    bundle.release()
                    cache.close()
                return
            namespace = generation.mount_admitted_core(cache, fallback=fallback)
            runtime_module = importlib.import_module(
                namespace + ".adapters.astrbot.runtime"
            )
            self._runtime = runtime_module.AstrBotRuntime(
                self._host_context,
                plugin_root=bundle.resource_root,
                data_dir=self._data_root,
                config=self._host_config,
            )
            generation.attach(self._runtime)
            self._pages = importlib.import_module(
                namespace + ".adapters.astrbot.public_pages"
            ).PublicPages(self._host_context, self._runtime)
            self._admin_pages = importlib.import_module(
                namespace + ".adapters.astrbot.admin_pages"
            ).AdminPages(self._host_context, self._runtime)
            await self._runtime.initialize()
            if self._stopping:
                generation.sync_fence()
                return
            self._pages.register()
            self._admin_pages.register()
            if not fallback:
                cache.publish("ygl_core", key)
            self._running_version = bundle.release_doc["release"]
            self._running_digest = key
            self._state = generation.state = "fallback" if fallback else "active"
        except BaseException:
            if self._pages is not None:
                self._pages.close()
            if self._admin_pages is not None:
                self._admin_pages.close()
            if generation.receipt is not None:
                generation.sync_fence()
            else:
                bundle.release()
                cache.close()
            raise

    def get_ygl_endpoint_v1(self):
        runtime = self._runtime
        if runtime is None or not runtime.ready or self._stopping:
            return None
        return getattr(runtime, "_control_endpoint", None)

    @filter.on_plugin_loaded()
    async def plugin_loaded(self, *args, **kwargs):
        self._schedule_reconcile()

    @filter.on_plugin_unloaded()
    async def plugin_unloaded(self, *args, **kwargs):
        self._schedule_reconcile()

    def _schedule_reconcile(self):
        runtime = self._runtime
        callback = getattr(runtime, "schedule_extension_reconcile", None)
        if callable(callback):
            callback()

    async def terminate(self):
        self._stopping = True
        # All old gates close synchronously before the first await.
        if self._generation is not None:
            self._generation.sync_fence()
        if self._pages is not None:
            self._pages.close()
        if self._admin_pages is not None:
            self._admin_pages.close()
        try:
            if self._generation is not None:
                await self._generation.terminate(timeout=5.0)
        finally:
            await super().terminate()

    @filter.on_llm_request()
    async def scope_model_tools(self, event: AstrMessageEvent, req):
        if self._runtime is not None and self._runtime.ready and not self._stopping:
            await self._runtime.scope_model_tools(event, req)

    @filter.command("ygl")
    async def game_link(self, event: AstrMessageEvent):
        if self._runtime is None or not self._runtime.ready or self._stopping:
            status = {
                "failed": "游戏连结启动失败，请查看管理员日志。",
                "cleanup_pending": "游戏连结正在等待上一代清理。",
                "parked": "游戏连结正在等待运行所有权。",
            }.get(self._state, "游戏连结正在初始化。")
            yield event.plain_result(status)
            return
        if self._state == "fallback":
            yield event.plain_result(
                f"游戏连结正在使用已验证的上一版本：运行 {self._running_version}，安装 {self._installed_version}。"
            )
        event.should_call_llm(True)
        text = await self._runtime.handle_event(event)
        if text is not None:
            if type(text) is str:
                yield event.plain_result(text)
            else:
                # The response belongs to the current private runtime graph.
                await self._runtime.send_help(event, text)
