# Alpha cleanup and AstrBot 4.29.1 handoff

## Current installation contract

Install the supported built ZIP into a disposable AstrBot 4.29.1 first. A repository source ZIP is not an installation artifact. Fresh configuration comes from current declarations and validated SQLite values; no legacy configuration snapshot, preparation command, rollback selector or migration-completion marker is required. Existing invalid values remain blocked and can be repaired through the authenticated settings page; startup does not erase existing databases or subscriptions. Historical SQL schema steps remain because they create the current schema safely.

The plugin remains AGPL-3.0; the standalone SDK is MIT, beginning with `0.1.0a9`. SDK contract/ABI validation and exact artifact hashes remain strict. External runtime dependencies use supported minima; build/test tools remain separately pinned.

## Host pages and authorization

The official `astrbot_version: ">=4.29.1"` metadata declares the minimum Host version without a speculative upper cap. AstrBot 4.29.1's native view serving, scoped token URLs, relative nested assets and `AstrBotPluginView` bridge are used. The physical `pages` root remains the officially supported alias, avoiding a second shadowing `views` root. The authenticated catalog entry continues to redirect into the host-issued view URL. Official `.astrbot-plugin/i18n` titles label sidebar/workspace views in Chinese and English. Matching `views`/`pages` title keys are both required by different 4.29.1 UI consumers; they do not restore a retired plugin page. No duplicate routing or lifecycle framework was added.

Management mutations still require an ordinary Dashboard JWT, correct origin and path, and the actual dashboard verifier. API keys, view tokens, malformed tokens and forged requests do not grant management privileges. Real-host and source-contract checks are separate; see [the reproducible real-host smoke](real-host-smoke.md).

For a fresh installation, open the official plugin settings, check that the credential storage panel is ready, then fill the regional FFLogs Client ID and Client Secret and save. Core automatically creates a random persistent managed master key only after proving that the complete shared encrypted inventory is empty. Ordinary users do not need an environment variable or a terminal. The key lives in the private persistent plugin data area and is reused after restart, plugin stop/start and code updates; keep that data area when updating the plugin.

Existing ciphertext is never silently discarded or replaced with a new key. If its original key is missing or damaged, the credential storage panel offers **Restore key** and **Reconfigure credentials**. Restore uses a secure input and verifies every affected ciphertext before selecting the supplied key. Reconfiguration requires explicit confirmation of the affected modules and authorization credentials; they must be configured again. Ordinary configuration, grant identities, business data and fonts remain intact. Unknown or inconsistent inventory blocks mutation rather than assuming an empty installation. Key storage failure does not disable non-credential Core functionality.

`YGL_SECRET_KEY` remains an advanced deployment option for a standard Base64 encoding of exactly 32 bytes. Explicit empty, malformed or conflicting environment values never fall back to a new managed key; the chosen source is persisted. Back up the consistent plugin database, encrypted payloads and private managed-key files together in restricted local storage. Environment-managed keys remain the deployment operator's responsibility. No management API returns a master key or saved credential value, and there is no plaintext fallback. Storage readiness, saved-record presence, decryptability and successful upstream FFLogs validation are separate states.

## Repository cleanup

- Configuration defaults, catalog and operations now live together under `services/configuration`; native Windows identity-sensitive I/O lives under `infrastructure/windows`
- Retired FF14 locator pages and old assembly descriptor fallbacks were removed; the unified FF14 page remains
- Unused definitions and forwarding modules were removed after checking dynamic dispatch and public SDK exports
- Frontend sources and the dependency lock are canonical. Build projections are ignored in Git, regenerated and checked for reproducibility; the final ZIP still contains every required isolated page root
- The large font is a verified development fixture, prepared by `scripts/prepare_test_font.py`; production retains its managed download/cache path. Font licenses, notices, SDK typing marker and useful examples remain
- Private workflow files are no longer tracked. Historical design and execution documents are indexed under `docs/history`; current developer entry points are [the documentation index](README.md), [architecture](code-architecture.md) and [module guide](module-maintainer-guide.md)

## Verification and local handoff

Use [CONTRIBUTING](../CONTRIBUTING.md) for dependency setup, frontend build, exact SDK wheel and test commands. `scripts/check_doc_links.py` checks local documentation links; `scripts/verify_frontend_build.py` checks generated bytes. `scripts/build_release.py` emits the plugin ZIP, SDK wheel and `SHA256SUMS` without publishing a release.

The original real-host smoke baseline installs the exact ZIP in official AstrBot 4.29.1, starts the Core and FF14 module without credentials, exercises management/view authentication and nested resources, renders a real PNG using a verified font fixture and shuts down. It does not by itself verify managed-key initialization, credential persistence or live FFLogs. Public calendar requests are deliberately denied offline; this does not prove live calendar service or notification delivery. Mock/source-contract tests separately cover negative authorization, current configuration validation/recovery, renderer, calendar evaluation and HTTP failure handling. The current candidate's isolated credential-storage, browser and live acceptance results are recorded separately in [PR #8](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/pull/8).

Native Windows qualification is independent of the AstrBot version. The existing conservative NTFS/OS/Python qualification matrix is unchanged; Linux tests and skipped Windows tests do not extend it. Before deployment, local Codex should verify the exact ZIP hash, actual Windows qualification, cold fresh startup, dashboard/browser bridge behavior, help image rendering and lifecycle shutdown. Use disposable data and fixed fixtures first; live credentials, business APIs and production delivery require separate local verification.

This cleanup deliberately does not split every large implementation file, replace the native Windows trust architecture, or redesign the UI. Those changes need their own coherent behavioral and security review. Obsolete configuration and page compatibility is not promised; existing encrypted data still receives the protection described above. No automatic merge, tag or release is performed.

## Historical Windows follow-up (ed6b2ba, 2026-10-19)

The following results and artifact hashes predate managed-key initialization and recovery. They are historical evidence, not acceptance of the current credential-storage implementation.

The local qualification check passed on Windows build 26300, local NTFS and CPython 3.13.9 x64 with the GIL enabled. The native filesystem and six Core integration checks completed as 47 tests: 46 passed and one skipped because the account could not create a symbolic link. The frontend file-symlink escape test likewise fails at fixture creation with `EPERM`, before its rejection assertion, including under the ordinary Windows account. This remains a permission-related verification gap; no ACL, system policy, skip or security assertion was changed.

The official isolated AstrBot 4.29.1 smoke also passed under CPython 3.12 using the fixed CI ZIP, a new data directory and no storage master key. It verified SDK a9, Core Ready and FF14 active; current configuration and CAS; rejection of recovery against a healthy Core; native localized view titles, nested resources and authentication failures; and a real help PNG. External connections remained blocked. These results are separate from browser interaction, Local Test installation and actual IM delivery.

Two developer-check fixes were needed on Windows: documentation links now test private paths relative to the repository, so an isolated checkout beneath a private workspace directory remains usable; the real-host smoke creates its Windows event loop before installing its network-denial guards, without adding a network exception. The loop and network guards have separate positive and negative checks. These scripts and developer documents are outside the plugin ZIP allowlist.

The previous installation candidate was the CI ZIP with SHA256 `245934b1da35b6f30680966a1e70b1cc17b16b443510acd9afece50aa73169ee`, 1,537,851 bytes and 290 members. Its formal Windows rebuild has SHA256 `c084802b58d0e8d9a58b3183a20662aab736521ea653e3f832c3c9fa1c47e9ed`: every member's uncompressed bytes agree, but ZIP `create_system` is Windows (`0`) instead of Unix (`3`). The archives must not be described as byte-identical. The SDK a9 wheel is byte-identical across these builds: 42,636 bytes, SHA256 `4d8d71c4ab04805c3a1edbd9e194b5228e66c9e0290caf01daae9374d65a74e5`.

The final Windows frontend run covered the same files as CI, with test concurrency limited to one: shell 132 passed and one failed at symlink fixture creation; management and FF14 148 passed. The total is 280 passed, one failed and no skipped tests. Typecheck, the frontend build and reproducibility checks for all 76 generated files passed. The Windows result is not an all-green frontend run.

The final Python-suite result, Local Test installation state, browser acceptance and remaining limits are tracked in the [PR #8 delivery record](https://github.com/yomihime/astrbot_plugin_yomihime_game_link/pull/8). Earlier environment-preparation failures and interrupted runs remain separate from the final prepared test batch; an unfinished run is not a pass.
