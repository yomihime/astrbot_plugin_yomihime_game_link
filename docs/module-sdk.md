# Game Link module SDK

The independently installable distribution is `yomihime-game-link-sdk==0.1.0a6`. Import its public declarations with `import yomihime_game_link_sdk as ygl`. It requires Python 3.11 or newer and has no third-party runtime dependencies.

## Install and import

Install the reviewed wheel into your module development environment:

```powershell
python -m pip install --no-deps ./yomihime_game_link_sdk-0.1.0a6-py3-none-any.whl
python -I -c "import yomihime_game_link_sdk as ygl; assert ygl.__version__ == '0.1.0a6'; assert ygl.MODULE_ABI_VERSION == '2.0'"
```

The plugin release contains the exact content-pinned SDK from that wheel. Its bootstrap rejects changed contents, foreign package origins, partial preload graphs, and any preloaded retired `yomihime_sdk` graph. The retired root `api` and `yomihime_sdk.api` modules have no compatibility redirects. Historical stored-data readers are separate from module import compatibility.

## Versions and receiving validation

Package manifests use `contract_version: "2.0"` as the current module ABI selector. Disk manifest schema remains 1 and factory ABI remains 1. Old or unknown module ABI versions are rejected before factory resolution or registration. SDK release, module ABI and output formats are distinct: current display, facts and persisted B04 documents remain at output version 1.8.0. An output version of 2.0 is not admitted.

SDK constructors describe data and freeze supported containers; they do not establish permissions or perform Core receiving validation. Core checks the current canonical types and nested invariants at registration, invocation, service, renderer and storage boundaries. Constructing or copying an InvocationView, OwnerScope or grant reference grants no authority. Only the exact issued invocation with its current lifetime and admission proof can bind services.

## Injected services and public failures

Factories receive `ModuleServices` with config, identities, accounts, subscriptions and scopes. The SDK exposes no Core repository, storage router, host administration or filesystem container. Use `await services.scopes.bind(invocation)` for invocation-bound records, cache, HTTP, resources, dependencies, tasks and message access. Binding and operations check authority before and after awaits.

Cache lookup accepts `ygl.CacheQuery(key, visibility=None)`. Core derives user, module and grant partitions from the bound invocation. HIT includes a complete `CacheEntry` with key, payload, expiry and revision; MISS, EXPIRED and REJECTED expose no entry or payload. `get(key)` uses the same default partition and returns that complete HIT entry or `None` for the three nonhit states. It discards their status distinction, not HIT metadata. Default visibility is PUBLIC for Tool, AUTHORIZED with a current grant, USER with an actor, and PUBLIC otherwise. Explicit visibility still requires current origin, identity and grant authority. Stored-row REJECTED (for example an older grant revision) is distinct from current permission revocation, which raises `AccessDenied`. Invalid query, released/copied invocation, deadline, unavailable service and cancellation remain errors; `get` does not turn them into `None`. Public `put(key, payload, *, ttl_seconds)` exposes no internal owner, visibility, revision or invalidation controls. Module cache keys use a private namespace; earlier raw rows remain stored but produce MISS through the new bound route, with no cross-owner fallback or migration.

Record duplicate and CAS errors use the unique SDK `UniqueConstraintViolation` and `RevisionConflict` identities. Invalid proof, denied authority, invalid input, unavailable dependency and deadline failures map to `InvalidInvocation`, `AccessDenied`, `ParameterError`, `ServiceUnavailable` and `OperationTimeout`. Public errors contain fixed messages and safe logical attributes, without raw dependency cause/context chains. Cancellation propagates unchanged; a cancelled or timed-out accepted write may already have committed and is never automatically retried. Existing SourceHttpError codes retain the source policy contract.

Subscriptions use only `create_request(invocation, SubscriptionRequest)`, `revise_request`, `list_current` and `cancel(invocation, id, *, expected_revision)`. Creation omits ID/revision; Core supplies a new ID and revision 1. Revision includes both and must match current CAS. A `SubscriptionView` omits collector command parameters and cannot replace a request. Repeated identical creates can make distinct subscriptions sharing one collection job; no idempotency or automatic deduplication is promised. Revision/cancellation conflicts raise `RevisionConflict`; cancellation removes only the selected association, preserving other subscribers' jobs/data. A paused owner with fresh current proof may list/revise/cancel but cannot create. Command root, exact issuer, route, principal, module epoch and grant checks apply across awaits; old contexts/handles never regain authority on restore. An unconfigured delegate raises `ServiceUnavailable`. Collector input normalization and matching remain module business rules.

`OwnerScope.authorized` is the sole spelling for an authorization-bound descriptive scope; the historical `private` helper is retired. The Core-only `CacheService` synonym is also retired, without adding a module-facing repository API. Host administration remains outside `ModuleServices` and the SDK. Its Core-private unload/rollback receipts acknowledge observed facts only. An unload can disable before a later failure, and cancellation or late authorization failure after an accepted SQLite transaction can leave the commit outcome unknown. Refresh a newly authorized snapshot to reconcile; do not infer rollback or automatically retry from a missing receipt. Configuration, credentials and business data remain retained across unload/restore.

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
