# Offline command, Tool, identity, and subscription sample

This installed-wheel example contains two modules. The `status` module keeps
the original public `status` command and `sample_status` Tool, and adds a
`from source` capability that depends on `offline_sample/source:read`. Its
separate `status` capability has no dependency, so the example can exercise
dependent-capability degradation while an independent capability remains
available. `configured` requires the module's `region` setting; instance
health reports its declared capabilities as available and the Core health
resolver applies configuration availability per capability.

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
requests. The extension starts no background tasks and never sends a host
message; scheduling and delivery remain Core responsibilities.

The only standard-library import in `module.py` is `datetime`, used to create
the fixed aware timestamp required by the SDK observation DTO. All module,
handler, result, health, identity, and subscription interfaces come from the
supported `yomihime_sdk` package. Unit-test service stubs verify that handlers
delegate through those SDK interfaces; they do not establish CoreRuntime,
installed-artifact activation, or host behavior.
