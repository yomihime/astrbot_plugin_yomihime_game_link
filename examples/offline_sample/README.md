# Offline command, Tool, identity, and subscription sample

This installed-wheel example contains two modules. The `status` module keeps
the original public `status` command and `sample_status` Tool, and adds a
`from source` capability that depends on `offline_sample/source:read`. Its
separate `status` capability has no dependency, so the example can exercise
dependent-capability degradation while an independent capability remains
available. `configured` requires the module's `region` setting; instance
health reports its declared capabilities as available and the Core health
resolver applies configuration availability per capability.

The ordinary `sample_subscriptions_enabled` configuration field defaults to
the boolean `true` and controls subscription eligibility only when the host
explicitly binds it to the verified `offline_sample/status` deployment.
The field is non-sensitive and does not grant administrator or owner rights.
An ordinary discovery without a trusted host mapping cannot create or deliver
subscriptions. An explicitly trusted deployment also applies the Core's
normal module-intent initialization; tests must distinguish that deployment
from an inert discovery. Gate changes and disable/enable cycles follow the
Core's persistent revision and cutoff rules, with no replay of old events.

The `status` route also declares command-only account and subscription
operations:

- `account bind`, `account list`, and `account unbind` delegate to the injected
  `ModuleServices.accounts` methods. A binding records a default query identity;
  the example does not authenticate an external account.
- `subscription create`, `subscription list`, and `subscription cancel`
  delegate to `ModuleServices.subscriptions`. Creation accepts the
  `public_watch` or `private_watch` type and instant or digest delivery. The
  private type requires a real, current grant from the Core command context.
- These six account and subscription commands return the same fixed PUBLIC
  receipt after the real service operation succeeds. The list commands still
  query the service, then discard the returned private records. Their output
  contains no IDs, revisions, counts, presence information, configuration, or
  command parameters. This offline sample therefore does not provide usable
  private binding or subscription listings, or IDs for interactive follow-up.
  That product capability remains for the later `CLOSEOUT-MANAGEMENT-PRIVACY`
  Core contract decision; this receipt does not reclassify private records as
  public or weaken their authorization and delivery rules.
- `private status` is command-only and uses the account status operation. It
  returns a fixed private result and never reads or returns credential bytes.

The package registers `public_catalog` and `private_catalog` schedules with the
corresponding public and authorized collection scopes. Both use the same
deterministic matcher and return a fixed observation at a fixed UTC timestamp;
the public schedule can share one collection across users, while the private
schedule key remains scoped to its owner and grant. They perform no network
requests. The extension never sends a host message itself; scheduling and
delivery remain Core responsibilities. The finite `state hold` and `state late`
demonstrations create an SDK-owned task and release their own standard-library
event in `finally`, then wait for that child's actual cleanup. The late case has
a two-second local bound and accepts at most three cancellation notifications;
it does not change a Core invocation deadline.

Subscription creation uses `create_request(invocation, SubscriptionRequest)`;
revision uses `revise_request(invocation, SubscriptionRequest)` with the
current subscription revision. Repeated creation may produce distinct IDs while
sharing a collection job. Cancellation removes the caller's subscription, and
does not cancel another subscriber's shared job. Bind a fresh invocation for each
operation; a released, revoked or stopped owner's old handle cannot be reused.
The sample does not retry a failed operation automatically.

For cache consumers, `lookup(CacheQuery(...))` distinguishes HIT, MISS, EXPIRED and
REJECTED. `get(key)` projects the default-partition lookup: HIT returns the complete
`CacheEntry` (key, payload, expiry and revision); other statuses return `None`.
Permission failures, service errors and cancellation still propagate.

The standard-library imports in `module.py` are `asyncio` and `datetime`. All module,
handler, result, health, identity, and subscription interfaces come from the
supported `yomihime_game_link_sdk` package. Unit-test service stubs verify that handlers
delegate through those SDK interfaces; they do not establish CoreRuntime,
installed-artifact activation, or host behavior.

## Current Tool message input (SDK 0.1.0a6)

`read_tool_message(services, invocation)` is a minimal SDK-only helper for a
current root LLM Tool invocation: it binds the issued invocation and calls
`await scope.message.read()`. The existing fixed status handler does not need
message text, so it continues to return only its declared public sample facts.
The `message` capability and `sample_message` Tool use the same public bound
service. A command calling that message port receives a safe SDK failure.

The returned `MessageContext(text, event_ref)` is a neutral DTO, not proof.
Use text inside module business rules and event_ref only for local correlation;
do not automatically emit either into model facts, logs or UI. Re-read after
other awaits before effects or results; do not retain the handle after invocation
end, timeout, cancellation, revocation or unload. Commands and Web requests have
no message input; their explicit parameters remain sufficient. A nested call
does not inherit the root message. Service failures propagate through the SDK.

The AstrBot adapter correlates the exact weak-referenceable event object for
at most 300 seconds, with at most 128 live receipts and tombstones. Retries do
not renew that lifetime. Same-text new objects are new messages; expired objects
cannot be re-signed. Raw text is cleared lazily while access expires immediately.
This is not a persistent message ID, restart deduplication or QQ/NapCat guarantee.

Source tests of this helper use controlled injected services; trusted Core
issuer/Gateway tests are separate. This sample alone does not prove native
CoreRuntime/AstrBot initialization, real model calls or actual message delivery.

## Public ERROR facts (SDK 0.1.0a6)

`public_error_example()` returns a fixed public NO_RECORDS result with the strict
status/error envelope and an optional JSON-object supplement. Its archive fields
include null availability and a finite float; its recovery hint is fixed public
text. The registered `public_error` capability returns that helper's result.
It performs no HTTP call and adds no authority. Existing management and
subscription handlers retain their fixed public receipt.

Core checks the envelope against ErrorDetail and bounds JSON at the receiver.
See the SDK guide for the 512-node/depth16/string4096 and complete-ERROR-facts
256-KiB limits; Web depth8/whole-response256-KiB is independent. Select public
fields explicitly: response bodies, headers, exception attributes, credentials,
proof and full message text are not public facts merely because they are JSON.
FF14 uses supplement.market only for ERROR; other modules need no market key.
Construction alone proves no issuer/binder or native Host acceptance.

## Actual offline Core exercise

`state write` and `state read` use injected configuration and a USER-owned
`notebook` record. `state cache` calls the existing cache `put`, `lookup`, and
`get` ports, including absent and expired entries. Their OWNER declaration maps
to a fixed PRIVATE receipt; private values, messages, subscription identifiers and
invocation material stay out of public facts. `subscription revise` uses the existing SDK
request/CAS operation, with no module-issued authorization.

The repository's `tests.integration.test_s4_core_runtime` provides a separate
Host harness. Its task layout is `deployment/plugin` (real Core plus installed
SDK), `deployment/extensions/offline_sample` (this external package), and a
separate `harness` directory. Core initializes real SQLite, scans the native
filesystem, obtains a factory lease, starts instances, issues invocation scopes,
and dispatches through its public command/Tool entry points. Trusted subscription
gates and short-lived administrative sources are Host inputs, never module
services. External transport and message ports are controlled offline adapters.

Unload and final Core close revoke old instance services and scopes. A restored
legal instance retains its USER records, configuration and unexpired cache entry;
another user cannot read them. This deterministic Core layer does not establish
AstrBot native initialization, actual IM delivery, model calls or global Core
Ready. The example demonstrates service contracts, not a process sandbox.
