"""A minimal package and renderer-neutral result using one public type system.

No factory is imported by constructing the manifest. The entry name below is
descriptive example data, not a claim that a runnable game module exists.
"""

from decimal import Decimal

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
from yomihime_game_link_sdk.display import (
    DisplayDocument,
    MetricsBlock,
    NumberValue,
    TextBlock,
)
from yomihime_game_link_sdk.results import CapabilityResult, FactDocument, ResultStatus
from yomihime_game_link_sdk.version import MODULE_ABI_VERSION

from ..core.contracts.validation_boundary import validate_contract


def example_package() -> PackageManifest:
    capability = validate_contract(
        CapabilityDescriptor(
            capability_id="record.query",
            input_schema={
                "type": "object",
                "properties": {"record_id": {"type": "string", "minLength": 1}},
                "required": ["record_id"],
                "additionalProperties": False,
            },
            invocation_policy=InvocationPolicy.NATURAL_LANGUAGE_ALLOWED,
            effect=CapabilityEffect.READ_ONLY,
        )
    )
    module = validate_contract(
        ModuleManifest(
            module_id="sample",
            route="sample",
            category=ModuleCategory.GAME,
            factory_entry="sample.module:Factory",
            module_version="0.1.0",
            capabilities=(capability,),
            commands=(
                validate_contract(
                    CommandDescriptor(
                        operation_path="查询",
                        capability_id=capability.capability_id,
                        parameter_mapping={"record_id": "record_id"},
                        help_text="查询示例记录：/ygl sample 查询 <record_id>",
                    )
                ),
            ),
            tools=(
                validate_contract(
                    ToolDescriptor(
                        name="sample_record_query",
                        capability_id=capability.capability_id,
                        parameter_mapping={"record_id": "record_id"},
                        description="Read a public sample record.",
                    )
                ),
            ),
        )
    )
    return validate_contract(
        PackageManifest(
            package_id="example",
            package_version="0.1.0",
            contract_version=MODULE_ABI_VERSION,
            modules=(module,),
            author="Yomihime Game Link",
            license="AGPL-3.0",
            source="local contract example",
        )
    )


def example_result() -> CapabilityResult:
    document = validate_contract(
        DisplayDocument(
            title="示例公开记录",
            subject="sample-1",
            ordered_blocks=(
                validate_contract(TextBlock("这是离线契约样例，不是实际游戏数据。")),
                validate_contract(
                    MetricsBlock(
                        {
                            "示例数值": validate_contract(
                                NumberValue(Decimal("12.50"), "points", 2)
                            )
                        }
                    )
                ),
            ),
            sources=("offline example",),
        )
    )
    return validate_contract(
        CapabilityResult(
            result_id="sample-result-1",
            status=ResultStatus.SUCCESS,
            document=document,
            model_facts=validate_contract(
                FactDocument(
                    {"record_id": "sample-1", "value": "12.50", "unit": "points"},
                    sources=("offline example",),
                )
            ),
        )
    )
