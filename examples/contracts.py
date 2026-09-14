"""A minimal package and renderer-neutral result using one public type system.

No factory is imported by constructing the manifest. The entry name below is
descriptive example data, not a claim that a runnable game module exists.
"""

from decimal import Decimal

from ..api.display import DisplayDocument, MetricsBlock, NumberValue, TextBlock
from ..api.manifests import (
    CapabilityDescriptor,
    CapabilityEffect,
    CommandDescriptor,
    InvocationPolicy,
    ModuleCategory,
    ModuleManifest,
    PackageManifest,
    ToolDescriptor,
)
from ..api.results import CapabilityResult, FactDocument, ResultStatus
from ..api.version import CONTRACT_VERSION


def example_package() -> PackageManifest:
    capability = CapabilityDescriptor(
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
    module = ModuleManifest(
        module_id="sample",
        route="sample",
        category=ModuleCategory.GAME,
        factory_entry="sample.module:Factory",
        module_version="0.1.0",
        capabilities=(capability,),
        commands=(
            CommandDescriptor(
                operation_path="查询",
                capability_id=capability.capability_id,
                parameter_mapping={"record_id": "record_id"},
                help_text="查询示例记录：/ygl sample 查询 <record_id>",
            ),
        ),
        tools=(
            ToolDescriptor(
                name="sample_record_query",
                capability_id=capability.capability_id,
                parameter_mapping={"record_id": "record_id"},
                description="Read a public sample record.",
            ),
        ),
    )
    return PackageManifest(
        package_id="example",
        package_version="0.1.0",
        contract_version=CONTRACT_VERSION,
        modules=(module,),
        author="Yomihime Game Link",
        license="AGPL-3.0",
        source="local contract example",
    )


def example_result() -> CapabilityResult:
    document = DisplayDocument(
        title="示例公开记录",
        subject="sample-1",
        ordered_blocks=(
            TextBlock("这是离线契约样例，不是实际游戏数据。"),
            MetricsBlock({"示例数值": NumberValue(Decimal("12.50"), "points", 2)}),
        ),
        sources=("offline example",),
    )
    return CapabilityResult(
        result_id="sample-result-1",
        status=ResultStatus.SUCCESS,
        document=document,
        model_facts=FactDocument(
            {"record_id": "sample-1", "value": "12.50", "unit": "points"},
            sources=("offline example",),
        ),
    )
