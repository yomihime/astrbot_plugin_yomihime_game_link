# C00 services handoff

Implemented the C00 bounded public contracts in `api/storage.py`,
`api/subscriptions.py`, `api/services.py`, and `core/ports.py`.

Public storage exports: `GrantReference`, `OwnerScope`, `OwnershipKind`,
`VersionedRecord`, `DeclaredIndexQuery`, `RecordPage`, `RecordCollection`,
`CacheEntry`, and `freeze_json`. It distinguishes ownerless public data,
user-owned data, and authorization-bound private data; all JSON DTO snapshots
are frozen. Collections are issued pre-bound to the invocation scope.

Scheduling exports: `NormalizedInput`, `CollectionKey`, `Observation`,
`SubscriptionView`, `EvaluationState`, `EvaluationDecision`, `Collector`,
`SubscriptionEvaluator`, and `IntervalLimits`. Evaluators are synchronous
protocols, observations retain opaque frozen JSON snapshots (including valid
empty result sets), trigger events require an event key, version, and typed
`DisplayDocument`, and `validate_evaluation_decision` prevents a private
observation from widening into a public delivery. Registration DTOs
`ScheduleDescriptor` and `SubscriptionDescriptor` carry collector/matcher,
schema, scope, trigger, version, and notification metadata.

Module contracts export scope-bound services plus `ModuleServices`,
`CapabilityHandler`, `ModuleHandlers`, `HealthReport`, `ModuleInstance`, and
`ModuleFactory`. Account and subscription protocols include explicit bind,
view/unbind, create/revise/list/cancel operations. Core ports expose only
typed rendered-message delivery, host admin lookup, minimal unit of work,
grant/subscription lookup, and a single result outlet. No database, network,
scheduler, authorization issuer, or message implementation was added.

`ModuleServices` contains the module-lifetime configuration/account services
and an `InvocationServiceBinder`. A handler calls `await services.scopes.bind`
with its issued `InvocationView` to receive an `InvocationServices` bundle for
records, cache, HTTP, resources, dependency calls, and tasks. Implementations
must validate the bound invocation on each operation and reject reuse in another
invocation. The Protocol alone does not enforce this runtime isolation.

`ResourceAccess.register` accepts only asset metadata and bytes; the bound
service determines the resulting `ResourceReference.scope`. Callers cannot
supply a scope to widen a private resource. Resource IDs use the same grammar
as display asset IDs.

Validation: `python -m unittest tests.contracts.test_services -v` passed 7
tests; `python -m unittest tests.contracts.test_context_issuer -v` passed 8
tests; ruff passed for all five assigned Python files. Runtime integration
remains outside this contract task; the real issuer and host adapters must
validate invocation, grant, subscription, and module epoch before performing
operations.

Integration follow-up: root resolved recursive JSON annotations and added the
Astra negative cases for typed record pages, collection contexts, and integer
HTTP status codes. The integrated suite now has 40 tests. Collector-to-invocation
binding is still a deferred C00 contract requirement before C40 implementation.
