# C00-registration handoff

Implemented `api/manifests.py` and `api/contexts.py`, with contract coverage in
`tests/contracts/test_manifests.py`. No commit was created.

Public manifest DTOs are frozen, slotted dataclasses:

- `CapabilityDescriptor(capability_id, input_schema, invocation_policy, effect, output_version=CONTRACT_VERSION, privacy_floor=PUBLIC, required_config=(), required_sources=(), required_capabilities=())`
- `CommandDescriptor(operation_path, capability_id, parameter_mapping, help_text)`
- `ToolDescriptor(name, capability_id, parameter_mapping, description)`
- `ModuleManifest(module_id, route, category, factory_entry, module_version, capabilities, commands=(), tools=())`
- `PackageManifest(package_id, package_version, contract_version, modules, author, license, source)`, plus `global_module_id(local_id)`.

Enums are `InvocationPolicy` (`command_only`, `natural_language_allowed`),
`CapabilityEffect` (`read_only`, `write`), `PrivacyFloor` (`public`, `private`),
and `ModuleCategory` (`game`, `platform`). Input schemas use a deliberately
closed, frozen subset: object properties/required/additionalProperties, array
items, scalar enum and compatible numeric or string bounds. Unknown or
incompatible schema constraints are rejected. Tool declarations may only expose
declared, read-only, natural-language capabilities. `help` remains reserved;
command uniqueness is per module route.

`InvocationView` is frozen and has this exact signature:

```python
InvocationView(
    invocation_id, origin, actor_id, conversation_id, module_id,
    module_epoch, registry_revision, deadline=None, parent_id=None,
    grant_id=None, grant_revision=None,
    subscription_id=None, subscription_revision=None,
)
```

`origin` is `InvocationOrigin(command, llm_tool, scheduler, admin)`. Invocation,
actor, conversation, parent, grant, and subscription IDs are opaque non-control
text. `module_id` is global `package/module`; epochs/revisions are positive
non-bool integers; deadline is a finite positive monotonic timestamp; grant and
subscription IDs must be paired with their revisions. The DTO is intentionally
not an authorization credential; `core/context_issuer.py` owns issuance and
identity checks.

Validation passed:

```text
python -m unittest tests.contracts.test_manifests tests.contracts.test_context_issuer -v
14 tests OK
ruff check api/manifests.py api/contexts.py tests/contracts/test_manifests.py
All checks passed
```

This work does not implement runtime registry binding, authorization, handler
execution, schema parsing beyond the declared structural subset, or an AstrBot
integration.

## Sol initial-review corrections

The schema vocabulary remains unchanged, but now enforces exact scalar enum
types (including rejecting `bool` as an integer), finite numeric values, and
enum/bound consistency. Capability input schemas must be closed object schemas:
`additionalProperties` defaults to `False` and an explicit `True` is rejected.
`output_version` must exactly equal `CONTRACT_VERSION`.

At module assembly, command and Tool mappings are checked as entry-input-name
to capability-field mappings: target fields must exist, are one-to-one, and
cover every required capability field. Private capabilities are command-only
and cannot be exposed as Tools, in addition to the existing write and
command-only Tool restrictions. Global context IDs now share the identifier
grammar exactly, rejecting malformed package or module segments such as
`pkg./module`, `pkg/module.`, and `pkg-/module`.

Correction validation passed:

```text
python -m unittest tests.contracts.test_manifests tests.contracts.test_context_issuer -v
18 tests OK
ruff format --check api/manifests.py api/contexts.py tests/contracts/test_manifests.py
3 files already formatted
ruff check api/manifests.py api/contexts.py tests/contracts/test_manifests.py
All checks passed
```
