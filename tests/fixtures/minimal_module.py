"""Fixed offline module used by the Core-B02 integration tests and example."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from ygl_test_subject.core.contracts.validation_boundary import validate_contract

from yomihime_game_link_sdk.contexts import InvocationView
from yomihime_game_link_sdk.declarations import (
    CapabilityDescriptor,
    CapabilityEffect,
    CommandDescriptor,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
    ToolDescriptor,
)
from yomihime_game_link_sdk.display import DisplayDocument, FieldsBlock, TextBlock
from yomihime_game_link_sdk.results import CapabilityResult, FactDocument, ResultStatus
from yomihime_game_link_sdk.services import ModuleHandlers
from yomihime_game_link_sdk.version import MODULE_ABI_VERSION

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
                document = validate_contract(
                    DisplayDocument(
                        "离线样例查询",
                        "demo",
                        (validate_contract(TextBlock(f"未找到离线样例：{item}")),),
                        sources=("offline test fixture",),
                    )
                )
            else:
                facts = {
                    "item": item,
                    "found": True,
                    "label": record["label"],
                    "value": record["value"],
                }
                document = validate_contract(
                    DisplayDocument(
                        "离线样例查询",
                        "demo",
                        (
                            validate_contract(TextBlock("这是固定的离线测试数据。")),
                            validate_contract(FieldsBlock(record)),
                        ),
                        sources=("offline test fixture",),
                    )
                )
        elif self.capability_id == "status":
            facts = {"status": "ready"}
            document = validate_contract(
                DisplayDocument(
                    "离线样例状态",
                    "demo",
                    (validate_contract(TextBlock("离线样例模块已就绪。")),),
                    sources=("offline test fixture",),
                )
            )
        else:  # pragma: no cover - registration only supplies the two IDs above.
            raise AssertionError("unknown offline test capability")
        return validate_contract(
            CapabilityResult(
                f"offline-{self.capability_id}",
                ResultStatus.SUCCESS,
                document=document,
                model_facts=validate_contract(
                    FactDocument(facts, sources=("offline test fixture",))
                ),
            )
        )


def build_package(
    *,
    lookup_handler: OfflineCapabilityHandler | None = None,
    status_handler: OfflineCapabilityHandler | None = None,
) -> tuple[PackageManifest, ModuleHandlers]:
    """Build the fixed sample package and its actual capability handlers."""

    lookup = validate_contract(
        CapabilityDescriptor(
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
    )
    status = validate_contract(
        CapabilityDescriptor(
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
    )
    module = validate_contract(
        ModuleManifest(
            "demo",
            "demo",
            ModuleCategory.GAME,
            "tests.fixtures.minimal_module:build_package",
            "1.0.0",
            (lookup, status),
            commands=(
                validate_contract(
                    CommandDescriptor(
                        "查询", "lookup", {"item": "item"}, "查询固定离线样例"
                    )
                ),
                validate_contract(
                    CommandDescriptor("状态", "status", {}, "查看离线样例状态")
                ),
            ),
            tools=(
                validate_contract(
                    ToolDescriptor(
                        "lookup", "lookup", {"item": "item"}, "查询固定离线样例"
                    )
                ),
            ),
        )
    )
    package = validate_contract(
        PackageManifest(
            "sample",
            "1.0.0",
            MODULE_ABI_VERSION,
            (module,),
            "Core-B02 offline tests",
            "MIT",
            "offline test fixture",
        )
    )
    handlers = validate_contract(
        ModuleHandlers(
            capabilities={
                "lookup": lookup_handler or OfflineCapabilityHandler("lookup"),
                "status": status_handler or OfflineCapabilityHandler("status"),
            },
            collectors={},
            evaluators={},
        )
    )
    return package, handlers


__all__ = ["OFFLINE_DATA", "OfflineCapabilityHandler", "build_package"]
