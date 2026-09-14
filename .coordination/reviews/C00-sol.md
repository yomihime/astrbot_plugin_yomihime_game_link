# C00 Sol independent review

## Conclusion

**PASS for the explicitly frozen C00 local minimum contract baseline.** I found no
remaining blocking defect in the final files reviewed below. This is not approval
of the whole C00 architecture or of downstream runtime integration. The deferred
items in `.coordination/contracts/C00-baseline.md` remain deferred.

## Scope reviewed

- Architecture and task evidence: `docs/code-architecture/core.md` sections 6 and
  8, `C00-display`, `C00-registration`, `C00-services`, `C00-integration`, their
  handoffs, and `.coordination/contracts/C00-baseline.md`.
- Public contracts: `api/display.py`, `api/results.py`, `api/manifests.py`,
  `api/contexts.py`, `api/validation.py`, `api/storage.py`,
  `api/subscriptions.py`, and `api/services.py`.
- Core and package boundary: `core/context_issuer.py`, `core/ports.py`, root,
  `api`, `core`, and test package initializers, plus `examples/contracts.py`.
- Contract tests: public annotation resolution, display, manifests, context
  issuer, validation, services, and examples.

## Independent negative verification

The initial implementations accepted several invalid cases. I reported them
against exact files while implementation was active, then repeated the probes on
the final stable files. The final code rejects all of the following:

- forged invocation copies, expired or released parents, ownerless granted or
  subscription scheduler contexts, and malformed global module IDs;
- unsupported output versions, non-object/open input schemas, enum values of the
  wrong concrete type (including bool as integer), bad parameter mappings, and
  Tool exposure of command-only, write, or private capabilities;
- wrong parameter value types, non-finite numbers, out-of-range values, missing or
  extra closed-schema fields, and mutation of the returned parameter snapshot;
- mutable nested display/grid values, non-finite raw decimals, malformed typed
  times, invalid block flags/fallbacks, wrong series point values, unknown required
  blocks, arbitrary paths, and private grid/image assets in public documents;
- arbitrary/callable model facts, private model facts, malformed result payload
  types, invalid status/privacy values, empty selection results, model facts on an
  error result, and untyped result timestamps;
- non-JSON record/query/observation values, invalid owner/grant scopes, false-like
  non-bool evaluation triggers, mismatched private observation ownership, malformed
  resource IDs, source IDs, HTTP paths/credentials/bodies, and mutable or malformed
  message resource IDs;
- untyped `RecordPage` members, a non-`CollectionKey` collection view, and a
  non-integer HTTP response status such as `200.5`;
- unresolved recursive aliases or forward references in the public DTO and
  Protocol annotations.

The resource service remains invocation-bound. `ResourceAccess.register` no longer
accepts a caller-selected scope: the bound service determines the stored scope and
must revalidate it when reading. The baseline document correctly states that a
module-provided visibility marker is not authority.

## Verification evidence

- `python -m unittest discover -v`: **40 tests passed**.
- `ruff check api core tests examples __init__.py`: **passed**.
- `ruff format --check api core tests examples __init__.py`: **25 files already
  formatted**.
- `python -m compileall -q api core examples tests main.py`: **passed**.
- Example objects use the same `ygl_test_subject` package type identity as their
  consumers.

The documented root-level discovery command is required. Running unittest with
`discover -s tests` imports `contracts.*` directly and bypasses `tests/__init__.py`,
so the test-only `ygl_test_subject` alias is not installed. This does not affect the
documented command or production imports, but alternate test runners must arrange
the package alias themselves.

## Unverified runtime limits

- Python 3.12 was not available; verification used Python 3.13.9.
- No AstrBot loader, host identity/admin bridge, real registry/gateway, epoch or
  revision revalidation, database, HTTP source, resource store, renderer, message
  delivery, account authorization, scheduler, or game module was exercised.
- Scheduling descriptors are not yet linked into `ModuleManifest`; configuration,
  source and collection declarations, full notification/delivery records, complete
  repositories, and the formal third-party import namespace remain outside this
  minimum baseline.
- `Collector.collect` still receives a `CollectionView` without the trusted
  invocation context needed by `scopes.bind`. The collector-side binder signature
  remains a later C00 gate, so C40 real collector implementation is not ready for
  dispatch from this baseline.
