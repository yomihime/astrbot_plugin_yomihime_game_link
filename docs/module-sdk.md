# Game Link module SDK

Import public declarations with `import yomihime_game_link_sdk as ygl`. SDK `0.1.0a23` combines the a16 service contracts and the a19 catalog metadata contract. The a19 branch alone was not a complete superset of a16. Core and extensions pin the exact canonical wheel; version ranges are not a substitute for content identity. The SDK requires Python 3.11 or newer and has no third-party runtime dependencies. Module ABI remains 2.0 and the outer registration bridge remains 1: new declarations are optional trailing fields and existing registration signatures are unchanged. New consumers require a23 and its matching Core implementation.

The SDK (including its packaged examples) is licensed under MIT; see `SDK-LICENSE`. The AstrBot plugin implementation remains AGPL-3.0 under `LICENSE`. Third-party notices retain their own terms. The a12 source contract adds the outer-plugin extension bridge without changing module ABI 2.0. Built wheel identity and installation evidence are recorded separately; editing these declarations does not prove a published or installed wheel.

## Independent AstrBot extension plugins

The outer Star may locate the active Core through the public AstrBot
`Context.get_registered_star` API and call its public
`get_game_link_extension_host()`. A ready host implements SDK `ExtensionHost`
bridge version 1. Check its SDK release and module ABI before registration.
The outer plugin must delay importing the canonical SDK until Core bootstrap
has completed; it must not vendor a second copy.

`register_package(plugin_name=..., plugin_instance=..., package_root=...)`
accepts a complete package contained in the active plugin's installation.
The host verifies the exact Star instance from the public registry, safely
captures all package Python sources and returns an opaque `PackageLease`.
Use `unregister_package(lease)` on termination. Same-instance registrations
are idempotent; a stale lease cannot withdraw a replacement. Public plugin
loaded/unloaded hooks support reconnecting after either plugin reloads.
There is no polling requirement. Changed source or SDK bytes remain subject
to `restart_required`. User records and stored enable intents survive detach.
Sensitive configuration and credential-bearing sources require an approved installation policy bound to the actual Host plugin, package root and reviewed sensitive permissions. Current content hashes prove integrity; they are separate from the durable permission identity. A trusted management plan and revision-checked approval can grant the proposed policy. A package's own policy file never approves itself. Compatible version and page updates retain permissions without rebuilding Core; expanded source, credential injection, authorization, job or migration permissions require a new trusted approval and normal restart. Exact build-selected policies from earlier paired releases may migrate equivalent permissions with an audit record. Core keeps secret storage and injects credentials only into approved host/path/method/field slots; modules do not receive token bytes. Discovery and declarations confer no management authority, lease, storage or page permission. Pages publish only immutable, hash-verified resources captured from a current installation lease and share the shell's runtime. See [trusted source policies](source-policies.md).

## Read-only current-actor dependencies

A capability may explicitly declare a module-qualified dependency on an OWNER
read-only capability. Core admits this edge only from an exact active root
COMMAND with a real adapter-scoped actor. Tool, Web, scheduler, writable OWNER
targets and nested promotion remain denied. The child inherits the actor and
deadline, receives its own provider epoch/admission, and receives no copied
module grant. Provider USER records are readable but record writes are denied.
Core revalidates both parent and child across awaits and discards late results.

Private dependency data uses a bounded PRIVATE `DisplayDocument`, for example
one `FieldsBlock` with the provider's minimal DTO. It is consumed through the
SDK result only. PRIVATE results still cannot contain model facts, and this
read proof does not authorize root private output or account management.

## Install and import

Install the reviewed wheel into your module development environment:

Install the reviewed wheel recorded for the paired Core plugin build, then verify its version and public ABI against that release record. The Core bootstrap pins exact wheel contents and rejects changed contents, foreign package origins, partial preload graphs, and any preloaded retired `yomihime_sdk` graph. Do not infer the combined release version or digest from a branch label.

The plugin release contains the exact content-pinned SDK from that wheel. Its bootstrap rejects changed contents, foreign package origins, partial preload graphs, and any preloaded retired `yomihime_sdk` graph. The retired root `api` and `yomihime_sdk.api` modules have no compatibility redirects. Historical stored-data readers are separate from module import compatibility.

## Versions and receiving validation

Package manifests use `contract_version: "2.0"` as the current module ABI selector. Disk manifest schema remains 1 and factory ABI remains 1. Old or unknown module ABI versions are rejected before factory resolution or registration. SDK release, module ABI and output formats are distinct: current display, facts and persisted B04 documents remain at output version 1.8.0. An output version of 2.0 is not admitted.

SDK constructors describe data and freeze supported containers; they do not establish permissions or perform Core receiving validation. Core checks the current canonical types and nested invariants at registration, invocation, service, renderer and storage boundaries. Constructing or copying an InvocationView, OwnerScope or grant reference grants no authority. Only the exact issued invocation with its current lifetime and admission proof can bind services.

Core admits exact canonical nominal dataclasses and rejects their subclasses, including nested instances made without constructors. Its internal ancestry-based type classification preserves recursive validation, existing nesting/schema limits and original invocation deadlines; it neither grants a validation bypass nor changes the SDK contract/version. Contract catalogs cannot add virtual ABC or custom metaclass instance checks.

## Module model-tool aggregation (a23)

A module may publish one model-facing tool by declaring the paired
`ModuleManifest.model_tool_name` and `model_tool_description` fields and a
unique non-empty `ToolDescriptor.action` on every declared tool. A module that
omits the aggregate fields must leave every action unset and keeps the legacy
one-tool-per-descriptor publication path. Mixed declarations are rejected.

For an aggregated module, the model calls the parent with the closed shape
`{"action": <declared action>, "arguments": <legacy mapped parameters>}`.
The provider-facing `arguments` schema is only a portable envelope across the
actions. Core validates the selected action against that action's original
capability schema, required fields, types, ranges and unknown fields before
invoking module code. Parameters that belong only to another action are
rejected. Game-specific cross-field rules remain in the module's existing
validation; aggregation does not move those rules into Core. Modules keep
their existing handlers and capability schemas and do not implement a dispatcher.

`ToolDescriptor.name` remains the durable persona and Host permission identity.
Core publishes no legacy model entry for a migrated module, but reconstructs
legacy persona subsets action by action as limited exact-event tools. Main
requests first remove the raw parent, then atomically commit their permitted
limited projection. Native Host handoffs with `tools=None` or an explicit new
parent string/object retain Host lookup semantics and may select the callable
full-module parent; an explicit legacy subset remains limited to those exact
actions. Both full and limited calls recheck the current owned parent handle,
module epoch, action-to-descriptor mapping, parent and legacy Host permissions,
inactive preferences and public result authorization. The full parent does not
claim runner provenance: trusted in-process Python that already holds the exact
current object and an official event/Context retains the baseline direct-call
boundary and still passes those checks. A declaration alone is not authority,
and module code receives no Host event, parent handle or permission object.

## Declarative cards

`from yomihime_game_link_sdk import cards` exposes immutable, dependency-free
presentation declarations. A `DisplayDocument` may supply a trailing optional
`card=cards.Document(...)` beside its existing ordered text blocks. The text
blocks remain the fallback; model facts are separate and unchanged. Cards never
populate facts automatically. The entire card inherits its enclosing document's
privacy, so modules must omit any fields that audience cannot see.

Use `Text`, `Section`, `Row`, `PropertyGrid`, `Table` and `Image` with ordinary
strings or explicit `Inline((Span(...), ...))`. `Span` accepts an explicit
`#rrggbb` color or a semantic emphasis including `gold`; Core does not infer game
scores or grades. `Cell` optionally overrides table alignment. `Asset` accepts
only in-memory SVG text/bytes or PNG/JPEG bytes, never a URL or filesystem path.
Core validates bounded shapes and content, then owns decoding, fonts and layout.
No Pillow, renderer, Core or game-specific types are imported by the SDK.

`TaggedText("[purple]label[/purple] [gold glow]100.0%[/gold]")` explicitly
asks Core to parse bounded inline tags. Ordinary strings always stay literal.
Supported tags include `[color=#rrggbb]`, `[strong]`/`[b]`, `[secondary]`,
`[emphasis]`, the named color aliases and `[gold glow]`; closing tags must nest
exactly. `[gold]` chooses the gold color alone, whereas `[gold glow]` adds the
fixed glow style. Unknown/malformed tags fail card admission. For upstream names
and other external text, prefer `Inline`/`Span` rather than building tagged
source. If composing tagged source deliberately, escape each backslash as `\\`
and square brackets as `\[` / `\]`. No markup parser is imported into the SDK.

For generic help, `ModuleDisplay.logo_resource` may name a declared, hash-pinned
SVG/PNG/JPEG `ModuleManifest.resources` entry. Core binds that resource; the
module cannot use a logo declaration to fetch an arbitrary path or URL.

These are additive optional declarations in SDK a7; module ABI stays `2.0` and
DisplayDocument stays `1.8.0`. Old persisted documents with no `card` still read
as `None`. New cards persist losslessly in a namespaced, versioned representation
(including bounded base64 image bytes); unknown card storage versions or types
are rejected. This does not migrate or overwrite existing records. Downgrading
to a6 cannot read new card-bearing records and is not a supported rollback path.

## Background collectors

SDK a8 adds `ScheduleTrigger.BACKGROUND` (`"background"`) and the trailing
`ScheduleDescriptor.background_parameters: tuple[JsonObject, ...] = ()`.
The DTO remains descriptive: Core validates each fixed parameter set against
the collector's closed object schema and takes a detached, deeply immutable
snapshot. Background collectors require PUBLIC ownership and 1–32 parameter
sets. PERIODIC and ON_DEMAND declarations require the new field to stay empty.

BACKGROUND uses the same minimum, default and configurable interval fields as
PERIODIC. It runs independently of subscription demand; subscriptions cannot
reference a background collector. Core's scheduler normalizes the fixed inputs
through the collector and rejects duplicate normalized collection keys.

Fixed inputs have a total 2048-node budget (including object keys), maximum
depth 16 per parameter object, 4096 characters per key/string, 256 members per
container, and 4096-bit integers. Their complete JSON array must fit 64 KiB of
UTF-8 with `ensure_ascii=False`, `allow_nan=False` and default separators.
Cycles, non-finite numbers and non-JSON objects are rejected. No credential,
actor or grant authority may be supplied through these public parameters.
These declarations do not change module ABI `2.0`, disk/factory protocol `1`,
or display/facts/storage format `1.8.0`.

## Injected services and public failures

Factories receive `ModuleServices` with config, identities, accounts, subscriptions and scopes. The SDK exposes no Core repository, storage router, host administration or filesystem container. Use `await services.scopes.bind(invocation)` for invocation-bound records, cache, HTTP, resources, dependencies, tasks, diagnostics and message access. Binding and operations check authority before and after awaits.

Cache lookup accepts `ygl.CacheQuery(key, visibility=None)`. Core derives user, module and grant partitions from the bound invocation. HIT includes a complete `CacheEntry` with key, payload, expiry and revision; MISS, EXPIRED and REJECTED expose no entry or payload. `get(key)` uses the same default partition and returns that complete HIT entry or `None` for the three nonhit states. It discards their status distinction, not HIT metadata. Default visibility is PUBLIC for Tool, AUTHORIZED with a current grant, USER with an actor, and PUBLIC otherwise. Explicit visibility still requires current origin, identity and grant authority. Stored-row REJECTED (for example an older grant revision) is distinct from current permission revocation, which raises `AccessDenied`. Invalid query, released/copied invocation, deadline, unavailable service and cancellation remain errors; `get` does not turn them into `None`. Public `put(key, payload, *, ttl_seconds)` exposes no internal owner, visibility, revision or invalidation controls. Module cache keys use a private namespace; earlier raw rows remain stored but produce MISS through the new bound route, with no cross-owner fallback or migration.

Record duplicate and CAS errors use the unique SDK `UniqueConstraintViolation` and `RevisionConflict` identities. Invalid proof, denied authority, invalid input, unavailable dependency and deadline failures map to `InvalidInvocation`, `AccessDenied`, `ParameterError`, `ServiceUnavailable` and `OperationTimeout`. Public errors contain fixed messages and safe logical attributes, without raw dependency cause/context chains. Cancellation propagates unchanged; a cancelled or timed-out accepted write may already have committed and is never automatically retried. Existing SourceHttpError codes retain the source policy contract.

Subscriptions use only `create_request(invocation, SubscriptionRequest)`, `revise_request`, `list_current` and `cancel(invocation, id, *, expected_revision)`. Creation omits ID/revision; Core supplies a new ID and revision 1. Revision includes both and must match current CAS. A `SubscriptionView` omits collector command parameters and cannot replace a request. `creation_key` is optional. Without it, identical creates may form distinct subscriptions. When supplied, Core atomically reuses an active record only for the same trusted owner, route, type, canonical CollectionKey and key. Other associations and jobs retain their independent lifecycle. Revision/cancellation conflicts raise `RevisionConflict`; cancellation removes only the selected association, preserving other subscribers' jobs/data. A paused owner with fresh current proof may list/revise/cancel but cannot create. Command root, exact issuer, route, principal, module epoch and grant checks apply across awaits; old contexts/handles never regain authority on restore. An unconfigured delegate raises `ServiceUnavailable`. Collector input normalization and matching remain module business rules.

`OwnerScope.authorized` is the sole spelling for an authorization-bound descriptive scope; the historical `private` helper is retired. The Core-only `CacheService` synonym is also retired, without adding a module-facing repository API. Host administration remains outside `ModuleServices` and the SDK. Its Core-private unload/rollback receipts acknowledge observed facts only. An unload can disable before a later failure, and cancellation or late authorization failure after an accepted SQLite transaction can leave the commit outcome unknown. Refresh a newly authorized snapshot to reconcile; do not infer rollback or automatically retry from a missing receipt. Configuration, credentials and business data remain retained across unload/restore.

## Declared anonymous HTTP redirects

`SourceDeclaration.max_redirects` defaults to `0` (all redirects rejected).
An anonymous source may opt in with a limit of `1` or `2` and explicit
`redirect_path_prefixes`, for example `("/profiles/", "/id/")`. Prefixes match
whole path segments; `/id` does not authorize `/identity`. Only GET requests
and status 301/302/303/307/308 can follow a redirect. Every target must use
HTTPS, the exact declared host, and port 443. Userinfo, fragments, encoded
paths, traversal, loops, excess hops and undeclared paths are rejected with
`SourceHttpError("redirect_disallowed")`. Credentialed sources cannot opt in;
Cookie and authorization headers remain forbidden to modules.

Each hop uses the existing transport with automatic redirects disabled, the
same total deadline/concurrency slots, a fresh source quota reservation, and
the same request and response size limits. Bound invocation authority is
rechecked before and after each network attempt. Cancellation does not replay
requests. SourceHttp returns `HttpResponse.final_url` as the verified final
HTTPS origin and path only, with query and fragment omitted. It is optional
(`None`) for compatibility with existing three-argument response constructors.
Consumers must still validate upstream identity and login-wall content; a
successful HTTP response and final path do not establish business identity.

## Current root Tool message

The public read port is `MessageAccess.read() -> MessageContext`, available on
a bound `InvocationServices.message`. Both declarations are exported by the
canonical SDK. A module uses only its injected services:

```python
import yomihime_game_link_sdk as ygl

async def read_tool_input(
    services: ygl.ModuleServices, invocation: ygl.InvocationView
) -> ygl.MessageContext:
    scope = await services.scopes.bind(invocation)
    return await scope.message.read()
```

This read requires the exact Core-issued, admitted root LLM Tool invocation and
current trusted Host source. `MessageContext(text, event_ref)` is a frozen neutral
DTO, not authority: constructing, copying or retaining it cannot bind services
or replay a message. The module receives no AstrBot event. Read text inside
module business rules and use event_ref only for bounded local correlation.
Neither value belongs automatically in model facts, logs, public errors or UI.
A cached DTO is not a freshness check; re-read after other awaits and before
message-dependent effects, candidate publication or results. Core and Host
recheck issuer identity, module lifetime and current source at their boundaries.

Explicit command and Web entry points use their explicit parameters without
requiring a chat-message proof. A valid bound command, Web or scheduled scope
has no root Tool message and `message.read()` raises `ServiceUnavailable`; a
nested dependency does not inherit its parent's message. No new ADMIN or
subscription binding entry point is provided. Invalid or copied invocations
fail with `InvalidInvocation`; ended, timed-out, cancelled, revoked or unloaded
handles cannot continue reading. Source/current failures use safe public SDK
errors without raw exception chains; asyncio cancellation still propagates.

The AstrBot adapter correlates the exact weak-referenceable event object, not
text or platform message IDs. Same-object retries retain correlation without
renewing the 300-second maximum lifetime; new objects with the same text are
new messages. Its in-memory bound is 128 live receipts plus expired tombstones
combined. A full table rejects new receipts rather than evicting an existing
one. An expired object cannot be re-signed; weak-reference GC releases its
entry. Raw text is cleared lazily on access/capture, with no cleanup timer,
while permission expires immediately. Correlation is also scoped to user,
session, adapter, module instance and epoch. No restart persistence, stable
message ID, QQ/NapCat capability or generic wait framework is promised.

| Earlier internal path | Supported a2 module path |
| --- | --- |
| AstrBot-to-FF14 private `_bind_tool_event` with a raw event | Core-issued invocation → `services.scopes.bind` → `scope.message.read` |
| Module reading Host event getters or retaining an event | Module receives neutral text/event_ref data and re-reads the bound port |
| Explicit command/Web parameters | Continue using explicit parameters; no fabricated chat message |

The earlier private binding has been removed from active Host/FF14 code; no
legacy raw-event fallback or second message channel is supported. Item,
quality, region and candidate-confirmation rules remain FF14 business rules.

## Public ERROR facts

An ERROR result may omit model facts. When it supplies facts, Core accepts only
`{status, error[, supplement]}`: status is exactly `"error"`, error contains
exactly `code` and `message`, and both equal the result's public ErrorDetail at
Core input. Optional supplement is a JSON object. Unknown top-level business
keys, mismatched messages/status/codes, and ERROR display documents are refused.
There is no module-ID selector, extension registration or new error DTO.

For example, a module selects fixed public fields and a safe recovery hint:

```python
import yomihime_game_link_sdk as ygl

error = ygl.ErrorDetail(ygl.ErrorCode.NO_RECORDS,
                        "No matching public records; choose another source.")
result = ygl.CapabilityResult(
    "public-source-error", ygl.ResultStatus.ERROR, error=error,
    model_facts=ygl.FactDocument({
        "status": "error",
        "error": {"code": error.code.value, "message": error.message},
        "supplement": {
            "archive": {"attempted_source": "offline", "available": None,
                        "confidence": 0.25},
            "recovery_hint": "Choose another public source.",
        },
    }),
)
```

ERROR facts use JSON null, booleans, integers, finite floats, strings, arrays and
objects with nonempty string keys. Arbitrary objects, bytes, display wrappers,
Decimal, cyclic values and NaN/infinity are refused. Noncyclic aliases count at
each occurrence. Existing nonerror Decimal facts and DisplayDocument typed-value
rules keep their previous domains; accepting a fact float does not admit a
DisplayDocument float.

Core retains a total 512-node budget including map keys, values and fact sources,
a maximum depth of 16 with the root at depth zero, and 4096 characters per
string/key/source. In addition, the complete ERROR facts body, including its
status/error/supplement envelope, must fit 256 KiB of UTF-8 using the Tool JSON
serialization (ensure_ascii=False, allow_nan=False, default separators). The
independent Web projection has its existing depth-8 and whole-response 256-KiB
limits and may reject a result that fits Core. Limits are rejection boundaries;
Core does not silently drop fields or turn an invalid ERROR into success.

FF14 places its selected error-market diagnostics at `supplement.market`.
SUCCESS, PARTIAL_SUCCESS and NEEDS_SELECTION retain their existing market and
selection layouts. FF14 owns the distinction between failed and empty sources,
missing prices, coverage, cache provenance and recovery guidance; a missing
price remains null, never an invented zero. Other modules choose their own
supplement keys. Core validates generic structure and the envelope and does not
read market fields to decide whether an error is valid.

The module must explicitly choose public fields and safe messages/hints. Never
copy an upstream response, headers, exception attributes, credential material,
proof, internal message reference or full chat message into facts. JSON validity
is not secret detection or permission: a legal string can still contain private
data. The receiver detaches accepted facts and publication rechecks current
authority after awaits. Already accepted writes, claims or started HTTP effects
cannot be recovered by rejecting a later public result.

Command replies continue to use Core's fixed code-specific error text. Web's
public error message is independently masked to its existing safe recovery hint;
code/message equality is checked at Core input, not between these projected
messages. SDK service APIs retain canonical safe typed failures and cancellation;
Gateway retains its existing ERROR-result protocol. Neither a DTO nor a recovery
hint grants permission to retry or change a user's request.

## Explicit public query cache and safe retry hints (a13)

`CacheAccess.put(..., visibility=CacheVisibility.PUBLIC)` pairs an explicit
public lookup with a public write. Core permits this selection only for an
invocation's PUBLIC, READ_ONLY capability. It cannot publish OWNER or private
capability data. Omitting visibility preserves the existing current-owner
partition; this is not a change to USER isolation. Payloads returned from a HIT
are deeply immutable mappings and tuples, so consumers must accept `Mapping`
and finite sequences rather than require mutable `dict` or `list` values.

`SourceHttpError.retry_after_seconds` carries only an optional integer in
0..86400. Core parses Retry-After delta seconds or an HTTP date for 429/503,
discards invalid or excessive values, and never forwards arbitrary headers or
upstream bodies as error details. No automatic retry is introduced. A module can
copy the bounded value to `ErrorDetail.retry_after_seconds`; receiving validation,
Gateway reconstruction and the fixed Core error renderer preserve it. The hint
does not extend the invocation deadline or grant retry authority.

Explicit read-only OWNER dependency scopes can open an existing USER record
collection without registering or modifying storage metadata. An absent catalog
with no records is readable as empty; conflicting metadata and orphan rows fail
closed. Read-only scopes retain worker-level epoch checks and reject all writes.
This does not expose a new principal selector or a writable grant to modules.

## Packaged examples

The wheel includes `_examples/empty_module` and `_examples/offline_sample`, each with manifest.json, module.py and README.md. Extract them using importlib.resources; rename manifest.json to yomihime.manifest.json when installing a package. The empty factory is inert; the offline sample uses deterministic data and the existing injected service contracts. Neither example introduces real model or upstream calls.

The offline sample also exercises configuration, USER records, cache put/lookup/get,
safe public ERROR facts, current Tool message input, subscription revision/CAS and
finite task cleanup. Its source imports only this SDK and the standard library.
The separate repository harness `tests.integration.test_s4_core_runtime` installs
the wheel into a task-local real Core deployment and places the module beside it
as an external extension. It uses native discovery, SQLite and public Core ingress;
Host transport and message ports are controlled offline adapters. Host-issued
administration and trusted subscription gates remain outside ModuleServices.
Unloading rejects old scopes and instance services; legal recovery preserves
configuration, records and unexpired USER cache entries while enforcing owner
isolation. See the packaged example README for cleanup and evidence boundaries.
This Core exercise does not claim native AstrBot startup, model or IM delivery,
global Core Ready, or a process sandbox.

ModuleDisplay metadata remains optional, bounded and localized; it never changes module IDs, owner identities, command routing or storage. Unloading a module preserves its existing persisted data. Existing 1.7 display read adaptation retains the historical codec, tags and identities; no data-format migration is included in this SDK change.


## Invocation-bound diagnostics (a10)

Declare optional `ModuleManifest.diagnostics: tuple[DiagnosticDeclaration, ...]` with an event ID and a closed finite JSON `payload_schema`. Event IDs are 1-96 ASCII label characters; at most 32 unique events are admitted. Only a separately selected trusted deployment policy enables events. Scanning a manifest never enables diagnostics.

SDK a10 adds `diagnostics` to the invocation-scoped `InvocationServices` protocol. Existing modules that only consume injected services remain compatible. A Host or test double that implements the provider side of `InvocationServices` must supply this member to satisfy the current protocol; modules that require a10 are not promised to run on older Hosts without it. The module ABI remains `2.0` because this adds a service to the current SDK contract without changing manifest/factory ABI selection.

```python
import yomihime_game_link_sdk as ygl

EVENT = ygl.DiagnosticDeclaration("ReadSummary", {
    "type": "object", "additionalProperties": False,
    "properties": {"count": {"type": "integer", "minimum": 0, "maximum": 100}},
    "required": ["count"],
})

async def report(services, invocation):
    scope = await services.scopes.bind(invocation)
    await scope.diagnostics.emit("ReadSummary", {"count": 1})
```

The data-only schema vocabulary supports closed objects, finite arrays (`maxItems` at most 512), bounded strings (`maxLength` at most 4096), bounded finite numbers/integers, booleans, null and scalar enums. A type may be nullable. Local `#/$defs/name` references can reuse finite schema nodes; external references, cycles, callbacks, imports and unknown schema constraints are rejected. Schema validation has an independent 2048-node / depth-16 ceiling per event, within the unchanged 262-KiB manifest byte bound; capability schemas retain their existing limits.

Core derives owner, epoch and invocation trace; modules supply neither identity nor sinks. Each payload has at most 512 nodes including object keys, depth 8, 4096 characters per string and a 64-KiB event envelope. Limits only drop diagnostics. Per invocation the trusted policy cannot exceed 64 events / 512 KiB; retained payloads cannot exceed 64 events / 512 KiB and eight traces. Retention is a rolling memory limit, not a runtime lifetime acceptance quota. Accounting has at most eight live invocation slots; when full, new invocations drop optional diagnostics until an exact Core issuer release frees a slot. Released invocations cannot regain authority, and retention eviction never resets a still-live invocation budget. Events too large for the selected per-invocation byte budget allocate no counter metadata. FF14 calendar events retain a 48-event per-invocation cap. Closing the sink and ordinary sink faults are optional; every emit still checks current invocation, owner/epoch, deadline, admission, grant and instance lifetime. Authorization, lifecycle and cancellation failures propagate and cannot be treated as dropped diagnostics.

Business field selection belongs to the module. Never copy headers, chat, exception bodies, raw arbitrary responses, credentials or proof. Closed field whitelists are the publication basis; sensitive-shape rejection is an additional guard and cannot detect every possible secret. A keyword or pattern scan cannot certify arbitrary plaintext as secret-free, and Host forwarding only validates the generic envelope; it does not interpret or sanitize module meaning. Prefer bounded typed values and finite enums over free-form strings. Reviewed status fields such as `client_secret_configured: bool` and `authorization_status` with a closed enum are suitable when declared by the module schema and enabled by trusted policy. Malformed schema/payload uses a fixed public error, with no rejected values in the error.

## Cache backend availability (a10)

`CacheUnavailable` is a public subclass of `ServiceUnavailable`. It means an actual cache backend fault after current authority checks. It is the only service-availability type that a module may downgrade to uncached computation. Current permission, grant/epoch, deadline, copied/released invocation and cancellation failures are never converted to it. Post-await authority failure takes precedence over the backend error. No `CacheAccess.recover` or `rebind` method exists; cache recovery stays inside Core.

## Generic page roles (a10)

`PageDescriptor.role` is `"home"`, `"config"` or `"aux"` (default). Each module may have one home and one config page; multiple auxiliary pages are allowed. Unknown roles and duplicate primary roles are rejected by the receiving boundary. Roles are descriptive, not permission or an arbitrary code loader. Owner and entry resources come from trusted captured manifests. The actual frontend interfaces, page services and mount lifecycle are in [the frontend contract](frontend-module-contract.md), never shipped as TypeScript application code in the Python wheel.

PageDescriptor also declares config_fields and credential_fields as separate bounded name tuples. They must match ordinary and sensitive fields in the owning manifest, without overlap. The reserved generic reply_theme field may appear in config_fields; its declaration and palette are injected by Core, never overridden by a module. The receiving page service requires the current page declaration, trusted deployment policy and actual management authority; adding a field name never grants access.

`ModulePageServices.preview(capabilityId, parameters)` is an owner-bound public rendering path. It is available only to the caller's current public read-only Web capability and accepts only PUBLIC success/partial-success card results. It returns at most eight validated PNG pages and 16 MiB total (4 MiB per page); it has no message target, personal finished-image cache or reusable grant pixels. Current invocation, module, capability and resource proofs are rechecked across awaits. Preview does not grant permission to invoke a different capability or expose private output.


### Effective page configuration and safe descriptors

Module page current configuration uses the same trusted effective ordinary-field reader as ModuleServices. A destination absent from storage may have `source: "legacy"` and `migration_pending: true`; only the validated destination field value is exposed, without the Core source owner, field, revision or object. The response revision remains the destination CAS revision. Existing destination values immediately stop projection. Invalid or secret legacy values fail rather than silently becoming defaults.

Current config may also contain `descriptors` for the exact authorized page fields: description, default and a closed approved value_schema subset, plus optional bounded value/label choices supplied by trusted Core composition. Private schema annotations and unbounded JSON are rejected. This metadata never grants field rights. Core injects the generic reply_theme field and palette; modules must not declare its schema or hardcode theme choices. A missing override projects null as valid follow-Core; saving null maps to CLEAR. An explicitly persisted null remains invalid data.

Host module page requests require a short-lived opaque page_session after actual Dashboard JWT/Origin authentication. Host validates current subject/session, owner/page/runtime/epoch, field grants and generation on each request and commit, then removes the proof before the strict Core helper body. The proof demonstrates scoped Host authority; AstrBot does not attest the originating DOM. Asset tokens, client labels and page names alone grant no management rights.

Trusted legacy policies may register retained ordinary source names with a configuration coordinator. Such rows remain stored but are hidden from ordinary current snapshots and cannot be patched or cleared through current declarations. Unknown fields and secret metadata still fail. A bad legacy source value is independently rejected by destination-schema projection and does not prevent unrelated valid Core theme updates.

## Managed binary resources and weighted collages (a15, updated a16)

Bound resources provide `store(media_type, content, *, visibility=None, ttl_seconds=None)`, `read(asset_id, *, visibility=None)`, `metadata(asset_id, *, visibility=None)` and `stats(*, visibility=None)`. Core allocates an opaque content reference; modules never construct IDs, paths or owner hashes. PNG/JPEG bytes are validated in Core. New managed resources have a 4 MiB per-file limit; PUBLIC scope allows 256 MiB / 2048 items, other scopes retain 64 MiB / 512 items. The seven-day default TTL (at most thirty days), indexed expiry/LRU eviction and read leases remain. Cleanup is bounded and runs on writes; existing legacy resources are outside this managed eviction set. Missing or corrupt reads raise `FileNotFoundError`; metadata returns None. Expired unmanaged content is not silently adopted or renewed by storing identical bytes. Authority and cancellation failures remain errors. `ResourceCacheStats` reports bytes, items, max_bytes, max_items and evictions. Stats expose only the requested authorized partition.

PUBLIC writes require a current PUBLIC, READ_ONLY capability. A private caller may invoke a declared public preparation dependency with public parameters, then read its public reference; this grants no right to publish private results or write personal data into PUBLIC. Personal list/result caches stay USER or AUTHORIZED as appropriate. Runtime bytes live in Host-managed data storage and survive ordinary restart; development directories and plugin installation paths are not cache locations.

`cards.ResourceImage(asset_id, visibility=CacheVisibility.PUBLIC, alt=..., required=False)` refers to prepared bytes. A sole `cards.WeightedCollage(items, max_height=2800, max_pages=8, preferred_aspect=2/3)` block accepts up to 200 `cards.CollageItem(key, weight, image=None, title=..., caption=...)` values. Weights must be positive and finite; business filtering, units and weighting belong to the module. Core resolves references before rendering and never performs HTTP while painting. Missing optional images become placeholders with their supplied text. Keep complete ordered text blocks as fallback.

The deterministic integer shelf layout fills each page body, assigns areas monotonically by weight and stable key, limits relative weights and cell aspect variation, and bounds cover cropping; unsuitable ratios use contain/placeholder behavior. It does not promise exact linear proportions. Collage PNGs use a local 4 MiB/page, 16 MiB total and eight-page budget, plus the existing pixel/conversion limits. Ordinary cards retain their existing limits. The command path prepares all pages before final admission and sends one exact-event chain; ambiguous sends are never retried. Commands without a grant may cache finished pages through current USER-scoped services with normalized content, resource versions, resolved theme and layout inputs in the key. Grant-authorized finished pages are not cached into USER storage. Sole weighted collages retain a local text fallback budget of 24,000 characters and 1,024 lines; exceeding it requests a smaller selection. Full layout details are in [the rendering specification](command-presentation/declarative-rendering-spec.md).

## Dense integer-grid collages (a16)

`cards.DenseCollageItem(key, span, image=None, title="", caption="")` and `cards.DenseCollage(items, columns=20, cell_width=54, cell_height=81, max_height=2592, max_pages=8, fit="cover")` form a sole dense document body. Document width must equal columns * cell_width, an explicit edge-to-edge content width; the header is Core-owned. Each span is an integer square k-by-k in grid cells. Tuple order is logical rank; physical placement sorts by descending span then logical rank and uses deterministic first-fit. Each item appears once without overlap, rotation, split or implicit duplication; tail whitespace is possible. Business filtering and time-to-span rules belong entirely to the module.

The receiving boundary accepts 1–1000 items, unique keys, span1–8 within columns, columns8–32, cell width24–96, height24–192, total width400–2048, page body height128–6000 accommodating the largest tile, and at most8 pages. Dense text fallback allows160,000 characters/6000 lines. Aggregate source bytes are bounded at32MiB; actual image/pixel/page/PNG budgets still apply. These maxima are rejection boundaries, not a promise that every combination fits. `fit` is cover or contain with aspect-preserving resizing. WeightedCollage contracts remain unchanged.

Core checks a current authority-bound resource integrity snapshot before looking up finished pages. Known unchanged file-version evidence avoids full source reads on a hit; changed or unknown evidence requires hashing. A miss reads each unique source once. Finished PNGs remain fully checked. Derivatives depend on source hash, size, fit and rendering version. None of these internal caches grant new module rights or survive revoked authority as readable results.

## HTTP budgets and subscription events (a16)

`SourceDeclaration` appends `requests_per_hour`, `request_budget_config` and `authorization_only`. `scope.http.budget(source_id)` returns `HttpBudgetStatus(source_id, requests_per_hour, used, remaining, next_available_at)`, with aware UTC time or None. `SourceHttpError.next_available_at` preserves cooldown information for trusted scheduler defer. Normal bound HTTP refuses authorization-only sources. Secrets and authorization runners remain Core-only; modules declare public protocol data and never receive the credential. See [trusted source policies](source-policies.md).

`SubscriptionRequest.creation_key` optionally requests atomic active creation reuse within the exact trusted owner, route, type, CollectionKey and key. `EvaluationDecision.events` accepts at most3 independent `EvaluationEvent` values and is disjoint with legacy single-event fields. `PublicEventContribution` selects a declared pure composer in `ModuleHandlers.event_composers`; Core derives exact PUBLIC/full-route event aggregation, retaining every member's durable receipt and fence. `CommentaryRequest` is bounded and optional, executed after event persistence by the Host port with deterministic fallback. `SubscriptionDescriptor.enabled_config_key` declares an ordinary boolean pause control defaulting True, without granting bundled-module trust. `SubscriptionView.evaluation_state` is only returned by trusted own-current-session listing; evaluators/create/revise views retain None. See [subscription events](subscription-events.md) for limits, sealed membership and UNKNOWN handling.

## Module catalog metadata (a19)

`ModuleDisplay.summary: str | None = None` is optional plain text limited to 160 Unicode code points. Missing or null means absent; blank strings and Cc/Cf/Cs characters are rejected.

`PackageManifest.display_name: str | None = None` is an optional trailing field, limited to 128 Unicode code points without Cc/Cf/Cs. Missing, null and whitespace-only values normalize to null; other spacing is preserved. Missing names fall back to `package_id`.

`PackageManifest.module_order: tuple[str, ...] | None = None` is an optional trailing field. The disk root accepts a JSON array of local module IDs or null. Missing, null and empty normalize to an empty tuple. Partial lists put declared IDs first, followed by other modules sorted by their actual localized display name and stable ID. Duplicate, unknown, qualified/cross-package and non-string IDs are rejected.

These fields affect catalog presentation only; they do not change module IDs, command routing, owner identity, storage or permissions. Built-in classification requires an exact trusted bundled declaration. Extension labels and order require a completed local package and its exact current registered module manifest. Discovery alone and unclassified modules remain unknown. Neither metadata nor the public UI facade grants trust, a lease, storage, credential or management authority. Older closed-schema Core versions reject these disk keys, so packages using them require a matching Core/SDK pair.

The frontend may use the public `@game-link/ui` presentation facade, including `FormField` and `FormGrid`, for shared form layout and accessible field presentation. These are view components; they do not define submission, business schemas or authorization. See [shared UI integration](module-ui.md). The catalog projection and frontend contract remain separate from module services.

## Durable work and cache classes (a22)

The closed disk envelope has a 4096-node manifest traversal budget, with a separate 2048-node schema budget and depth 16. Job parameter schemas participate in schema accounting. These finite aggregate limits do not relax individual document, image, JSON, result or execution budgets.

`ModuleManifest.jobs` declares bounded `JobDescriptor` input schemas and deadlines. `ModuleHandlers.jobs` maps each declared ID to `JobHandler.run(invocation, parameters, control)`. Submit through invocation-bound `services.jobs`; every status/result/cancel/retry request rechecks current owner and module. IDs are locators. Workers receive fresh `InvocationOrigin.JOB` contexts with no command identity, default account binding, conversation or arbitrary send authority. Running jobs interrupted by restart require an explicit retry with a fresh generation. Closing a page stops polling without cancelling accepted work.

`JobControl.update` persists bounded progress/checkpoints; `publish` appends bounded result entries. `publish_document` delegates declared document rendering to Core and stores owner-bound output assets; it never sends a group message. Results paginate using `JobResultPage`. A failed renderer cannot produce a successful empty job. Cancel/retry require current CAS revisions and reject late publication.

`ResourceAccess.store` accepts generic `ResourceCacheClass.DEFAULT`, `RAW`, `DERIVED` or `OUTPUT`; existing default callers retain their scope checks. `pin(asset_ids, lease_seconds=300)` protects only currently authorized resources for a bounded lease, and `unpin` releases the same invocation's protection. A pin grants no read permission. Cache visibility, current owner, module, integrity and expiry remain independent checks.

## Public event projection and command analysis (a22)

`ModuleHandlers.event_projectors[composer_id]` optionally supplies a pure `PublicEventProjector.project(subscription, contribution, observed_at=...)`. Core selects one current PUBLIC subscription from the same module, type, source and complete recipient route. The callback receives that one `SubscriptionView` and bounded public facts, never a subscription list, repository, service or send handle. It returns `EvaluationDecision` or `None`; Core retains baseline, CAS, generation, cancellation and outbox authority. Presentation/template versions do not define the underlying business event identity.

An optional `CapabilityResult.analysis_request` is an `AnalysisRequest` with template identity, trusted instructions, focus identity and bounded timeout/output. It requires accompanying public `model_facts`. Only an authorized command can request Core-managed Host model generation; Tool output exposes the facts without recursively calling a model. Host calls use separate untrusted fact data, no history, tools or persistence. Missing provider, limits, timeout or generation failure retain a deterministic report and explicit fallback. Module code receives no model Key or client. Late results remain subject to the original authorization.
