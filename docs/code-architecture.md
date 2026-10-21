# Current code boundaries

| Layer | Responsibility |
| --- | --- |
| `yomihime_game_link_sdk/` | Independent MIT declarations and neutral protocols. Source targets a10; module ABI 2.0 and output 1.8.0 remain separate. |
| `core/` | Generic authority, budgets, admission, registration, lifecycle and receiving validation. |
| `services/` | Generic configuration CAS, cache, restricted module pages, trusted assembly and safe diagnostics. |
| `infrastructure/` | SQLite, HTTP, files and encryption. Windows boundary: [credential storage](credential-storage.md). |
| `adapters/astrbot/` | AstrBot events, tools, sending, page bridge, actual platform authorization and trusted assembly selection. |
| `modules/ff14/` | Region, items/market, FFLogs source/duty/job rules, calendar sources/parsing, defaults, diagnostic fields and pages. |
| `presentation/` | Generic image layout, fonts and themes. |
| `pages/public-contract.ts` | Public frontend module contract, separate from the Python wheel. |

Modules use only public SDK/frontend contracts. Core implements restricted SDK ports and injects them; modules cannot import Core/Host/infrastructure, inspect service private fields or use repositories, universal context objects, arbitrary management dispatch or cache recover/rebind. [Dependency tests](../tests/contracts/test_module_dependency_boundary.py) enforce absolute/relative imports, dynamic loaders and private service access.

Core's receiving validator catalogs only nominal dataclasses with the ordinary Python type metaclass. Exact canonical instances receive recursive field validation; subclasses, including fabricated nested instances, are rejected through their real ancestry. Scalar classification follows that value's ancestry rather than scanning every contract type. This changes validation cost, not authority, proof deadlines, recursive/schema budgets or the validation of every nested field; no cache of caller-controlled types is retained.

ModuleServicesFactory checks the exact issued invocation, owner/epoch, admission, deadline, instance lifetime and actual authority around service awaits. Discovery declarations never grant access. Diagnostics require both declared schemas and trusted selected policy: Core derives identity/trace, validates closed schemas, bounds and sanitizes; Host accepts only safe envelopes. Core/HTTP runtime observations do not recognize calendar sources or parser warnings.

Cache is an accelerator. Only actual backend faults after authority checks become CacheUnavailable; modules may bypass that type. Permission, cancellation, expiry and epoch failures propagate. Modules do not rebind to rotate proof and do not treat generic ServiceUnavailable as a miss.

Configuration is owner-scoped. Core declares generic presentation fields; FF14 owns `ff14_default_region`. The reviewed `LegacyConfigCopyPolicy` is an active compatibility path from the old Core `default_region` field to the FF14 destination. Effective projection is allowed only while the destination is absent and is validated by its schema; missing source uses the module default and invalid source fails. Explicit copy requires independent source READ and destination UPDATE authority, both revisions and a single transaction copy-if-absent. Source rows are retained, existing destination values survive, and modules receive no source object or arbitrary Core read port.

Module page operations check current Host session/token/page binding plus owner/runtime/epoch, declared fields, trusted field policies and independent READ/UPDATE authorization. services/module_pages.py is trusted Core implementation, never a module API. Config snapshots contain only own ordinary fields; credential snapshots contain configured/decryptable/upstream=unverified. Entry and resource behavior follow [the frontend contract](frontend-module-contract.md).

Release ZIPs inject a pinned wheel and verify source bytes, hashes, licenses and member closure. Source, isolated tests, release package, actual Host, browser and IM evidence remain separate. A checkout or successful manifest parse does not prove installation, global Core Ready or production behavior.


Diagnostic payload retention and live invocation quotas are separate. The store keeps bounded live accounting until the exact ContextIssuer release observer retires it, while old retained payloads may be evicted. Full accounting drops new optional output without resetting existing live quotas; later released/new invocations continue normally. Generic AstrBot forwarding lives in adapters/astrbot/diagnostics.py; the former game-aware FFLogs handler and logging/chunk backchannel have been removed.

Both SDK config.current and restricted module-page current use services/configuration/legacy_copy.effective_configuration with the destination CAS revision and one source/target repository snapshot per selected policy. Only destination field names/validated values and controlled pending origin reach the page. Retained legacy source fields are trusted policy metadata, cannot be client-selected or patched, and are excluded from ordinary snapshots. The module owns its region schema; Core performs generic projection and CAS.

Safe page descriptors cover only declared/trusted/authorized nonsecret fields. Generic reply_theme and choices are injected by Core presentation metadata, not defined by FF14 or the Python SDK. Host opaque sessions narrow real Dashboard management authority and are checked again at commit; they do not attest a specific DOM.
