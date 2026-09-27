"""Fixed offline module used by the Core-B02 integration tests and example."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from ygl_test_subject.api.contexts import InvocationView
from ygl_test_subject.api.display import DisplayDocument, FieldsBlock, TextBlock
from ygl_test_subject.api.manifests import (
    CapabilityDescriptor,
    CapabilityEffect,
    CommandDescriptor,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
    ToolDescriptor,
)
from ygl_test_subject.api.results import CapabilityResult, FactDocument, ResultStatus
from ygl_test_subject.api.services import ModuleHandlers
from ygl_test_subject.api.version import CONTRACT_VERSION

OFFLINE_DATA = {
    "demo": {"label": "离线样例 demo", "value": "固定测试数据"},
    "alpha": {"label": "离线样例 alpha", "value": "固定测试数据"},
}


class OfflineCapabilityHandler:
    """A real ``CapabilityHandler`` backed only by fixed offline test data."""

    def __init__(
        self,
        capability_id: str,
        *,
        started: asyncio.Event | None = None,
        release: asyncio.Event | None = None,
    ) -> None:
        self.capability_id = capability_id
        self.started = started
        self.release = release
        self.calls: list[tuple[InvocationView, Mapping[str, object]]] = []

    async def invoke(
        self, context: InvocationView, parameters: Mapping[str, object]
    ) -> CapabilityResult:
        self.calls.append((context, parameters))
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()

        if self.capability_id == "lookup":
            item = str(parameters["item"])
            record = OFFLINE_DATA.get(item)
            if record is None:
                facts = {"item": item, "found": False}
                document = DisplayDocument(
                    "离线样例查询",
                    "demo",
                    (TextBlock(f"未找到离线样例：{item}"),),
                    sources=("offline test fixture",),
                )
            else:
                facts = {
                    "item": item,
                    "found": True,
                    "label": record["label"],
                    "value": record["value"],
                }
                document = DisplayDocument(
                    "离线样例查询",
                    "demo",
                    (
                        TextBlock("这是固定的离线测试数据。"),
                        FieldsBlock(record),
                    ),
                    sources=("offline test fixture",),
                )
        elif self.capability_id == "status":
            facts = {"status": "ready"}
            document = DisplayDocument(
                "离线样例状态",
                "demo",
                (TextBlock("离线样例模块已就绪。"),),
                sources=("offline test fixture",),
            )
        else:  # pragma: no cover - registration only supplies the two IDs above.
            raise AssertionError("unknown offline test capability")
        return CapabilityResult(
            f"offline-{self.capability_id}",
            ResultStatus.SUCCESS,
            document=document,
            model_facts=FactDocument(facts, sources=("offline test fixture",)),
        )


def build_package(
    *,
    lookup_handler: OfflineCapabilityHandler | None = None,
    status_handler: OfflineCapabilityHandler | None = None,
) -> tuple[PackageManifest, ModuleHandlers]:
    """Build the fixed sample package and its actual capability handlers."""

    lookup = CapabilityDescriptor(
        "lookup",
        {
            "type": "object",
            "properties": {"item": {"type": "string", "minLength": 1}},
            "required": ["item"],
            "additionalProperties": False,
        },
        InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
        CapabilityEffect.READ_ONLY,
    )
    status = CapabilityDescriptor(
        "status",
        {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        InvocationPolicy.COMMAND_ONLY,
        CapabilityEffect.READ_ONLY,
    )
    module = ModuleManifest(
        "demo",
        "demo",
        ModuleCategory.GAME,
        "tests.fixtures.minimal_module:build_package",
        "1.0.0",
        (lookup, status),
        commands=(
            CommandDescriptor("查询", "lookup", {"item": "item"}, "查询固定离线样例"),
            CommandDescriptor("状态", "status", {}, "查看离线样例状态"),
        ),
        tools=(
            ToolDescriptor("lookup", "lookup", {"item": "item"}, "查询固定离线样例"),
        ),
    )
    package = PackageManifest(
        "sample",
        "1.0.0",
        CONTRACT_VERSION,
        (module,),
        "Core-B02 offline tests",
        "MIT",
        "offline test fixture",
    )
    handlers = ModuleHandlers(
        capabilities={
            "lookup": lookup_handler or OfflineCapabilityHandler("lookup"),
            "status": status_handler or OfflineCapabilityHandler("status"),
        },
        collectors={},
        evaluators={},
    )
    return package, handlers


__all__ = ["OFFLINE_DATA", "OfflineCapabilityHandler", "build_package"]
