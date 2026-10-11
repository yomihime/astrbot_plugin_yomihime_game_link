"""Tiny neutral disk module; a test fixture, never a release input."""

import hashlib
import json

from yomihime_game_link_sdk import MODULE_ABI_VERSION


def write_settings_module(root, package_id="example", route="demo"):
    directory = root / package_id
    directory.mkdir(parents=True)
    page = b"export function mount(){return {update(){},dispose(){}};}\n"
    manifest = {
        "schema_version": 1,
        "package_id": package_id,
        "package_version": "1.0.0",
        "contract_version": MODULE_ABI_VERSION,
        "author": "tests",
        "license": "MIT",
        "source": "isolated fixture",
        "modules": [
            {
                "module_id": "demo",
                "route": route,
                "category": "platform",
                "module_version": "1.0.0",
                "factory_entry": "module:Factory",
                "capabilities": [
                    {
                        "capability_id": "inspect",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "nodes": {
                                    "type": "integer",
                                    "minimum": 0,
                                    "maximum": 600,
                                },
                                "blocks": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 33,
                                },
                                "reference": {"type": "string", "maxLength": 4096},
                                "privacy": {
                                    "type": "string",
                                    "enum": ["public", "private"],
                                },
                            },
                            "additionalProperties": False,
                        },
                        "invocation_policy": "command_and_public_web",
                        "effect": "read_only",
                        "privacy_floor": "public",
                    },
                ],
                "commands": [],
                "config_fields": [
                    {
                        "name": "label",
                        "default": "demo",
                        "group": "general",
                        "value_schema": {"type": "string", "maxLength": 32},
                    }
                ],
                "pages": [
                    {
                        "route_id": "items",
                        "title": "Demo",
                        "entry": "page.js",
                        "access": "public_web",
                        "capability_id": "inspect",
                    }
                ],
                "resources": [
                    {"path": "page.js", "sha256": hashlib.sha256(page).hexdigest()}
                ],
            }
        ],
    }
    for capability_id, privacy_floor, effect in (
        ("private.inspect", "private", "read_only"),
        ("write.change", "public", "write"),
    ):
        manifest["modules"][0]["capabilities"].append(
            {
                "capability_id": capability_id,
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "invocation_policy": "command_only",
                "privacy_floor": privacy_floor,
                "effect": effect,
            }
        )
    (directory / "yomihime.manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (directory / "page.js").write_bytes(page)
    (directory / "module.py").write_text(
        """from yomihime_game_link_sdk import (HealthReport, CapabilityHealth, HealthStatus, ModuleHandlers, CapabilityResult,
    ResultStatus, DisplayDocument, TextBlock, FactDocument, Privacy)
class Handler:
    async def invoke(self, context, parameters):
        privacy = Privacy(parameters.get("privacy", "public"))
        facts = {"owner": context.module_id}
        if "nodes" in parameters:
            facts = {"values": [0] * parameters["nodes"]}
        if "reference" in parameters:
            facts = {"reference": parameters["reference"]}
        return CapabilityResult("neutral", ResultStatus.SUCCESS,
            DisplayDocument("Neutral", "manual", tuple(TextBlock("neutral")
                for _ in range(parameters.get("blocks", 1))), privacy=privacy),
            model_facts=FactDocument(facts), privacy=privacy)
class Factory:
    async def create(self, services):
        return Demo()
class Demo:
    def handlers(self):
        return ModuleHandlers(capabilities={"inspect": Handler(), "private.inspect": Handler(), "write.change": Handler()}, collectors={}, evaluators={})
    async def start(self):
        pass
    async def stop(self):
        pass
    async def check_health(self):
        return HealthReport(capabilities={name: CapabilityHealth(HealthStatus.AVAILABLE) for name in ("inspect", "private.inspect", "write.change")})
""",
        encoding="utf-8",
    )
    return f"{package_id}/demo"
