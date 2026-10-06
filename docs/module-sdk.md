# Yomihime module SDK

`yomihime-module-sdk` is the supported, host-independent import surface for
extensions. Import public contracts from `yomihime_sdk`. Their canonical source
declarations live in `yomihime_sdk/api/`; the repository-root `api/` package is
a compatibility forwarding surface whose exported objects retain identity
with the canonical declarations. The SDK does not import Core runtime
implementations or AstrBot objects.

The current artifact is `yomihime-module-sdk` version `1.5.0`, carrying contract
`1.5.0` (`R1-MODULE-PAGES`) and compatible contract versions `1.0.0`, `1.1.0`,
`1.2.0`, `1.3.0`, `1.4.0`, and `1.5.0`.
Extension declarations use manifest schema v1 and factory ABI v1. The declared
minimum is Python 3.11 because the public API uses `StrEnum`. A local
installed-wheel check proves the bundled SDK imports from the installed site
directory and that compatibility exports share object identity; it does not
prove activation inside the target AstrBot environment. Host Python selection,
installation location, and import precedence remain W0-H evidence.

Contract 1.2.0 added `SourceHttpError` in the canonical
`yomihime_sdk.api.services` module and the top-level package. It exposes stable,
sanitized `code` and optional `status_code` fields for source HTTP failures.
`HttpRequest.query` remains an ordered tuple of string key/value pairs; raw
spaces, Unicode, and percent characters are accepted as data and are encoded
once by the transport. Control characters remain rejected.

Contract 1.3.0 adds `PrivacyFloor.OWNER` to capability declarations. Owner-floor
capabilities must use `InvocationPolicy.COMMAND_ONLY`, and the manifest rejects
exposing them through a Tool. This restriction is validated when descriptors
and manifests are constructed; host Core authorization remains responsible for
proving the current principal before an owner-scoped operation is dispatched.

Contract 1.4.0 adds `InvocationOrigin.WEB_PUBLIC` and the explicit
`InvocationPolicy.COMMAND_AND_PUBLIC_WEB` policy. This policy requires
`PrivacyFloor.PUBLIC` and a read-only capability. `COMMAND_ONLY` continues to
exclude web invocation. Tools still require `NATURAL_LANGUAGE_ALLOWED`; the new
web policy does not authorize natural-language invocation. Declaring the policy
alone does not enable an HTTP endpoint: the host and Core must separately verify
the request, admit the deployed capability, and enforce its lifetime and limits.
This SDK contract does not grant an administrator, conversation, or subscription
owner identity to a web request.

## Offline build and isolated install

The build backend is setuptools `80.9.0` with wheel `0.45.1`, pinned in
`pyproject.toml`. The checked local build machine already has both versions.
Build with `--no-build-isolation` and `--no-index` so no build helper is
downloaded. A different build machine must provide those exact build tools
before running this command. The standalone SDK wheel has no runtime
dependencies. The AstrBot Core plugin separately declares its HTTP runtime
dependencies in the repository-root `requirements.txt`.

The focused artifact test builds from a temporary source copy, installs with
`--target` into a temporary site directory, and runs its identity probe with
`python -I`. This avoids leaving setuptools build products in the checkout.
From the repository root in PowerShell, the equivalent manual build sequence
is:

```powershell
python -c "import setuptools, wheel; assert setuptools.__version__ == '80.9.0'; assert wheel.__version__ == '0.45.1'"
$work = Join-Path $env:TEMP 'yomihime-sdk-artifact'
New-Item -ItemType Directory -Force $work | Out-Null
$source = Join-Path $work 'source'
$wheelhouse = Join-Path $work 'wheelhouse'
$site = Join-Path $work 'site'
New-Item -ItemType Directory -Force (Join-Path $source 'docs') | Out-Null
New-Item -ItemType Directory -Force $wheelhouse | Out-Null
Copy-Item -LiteralPath LICENSE, pyproject.toml, setup.py -Destination $source
Copy-Item -LiteralPath docs/module-sdk.md -Destination (Join-Path $source 'docs')
Copy-Item -LiteralPath yomihime_sdk -Destination $source -Recurse
New-Item -ItemType Directory -Force (Join-Path $source 'examples') | Out-Null
Copy-Item -LiteralPath examples/empty_module, examples/offline_sample -Destination (Join-Path $source 'examples') -Recurse
$env:SOURCE_DATE_EPOCH = '315532800'
python -m pip wheel --no-index --no-deps --no-build-isolation --wheel-dir $wheelhouse $source
python -m pip install --no-index --no-deps --target $site (Join-Path $wheelhouse 'yomihime_module_sdk-1.5.0-py3-none-any.whl')
python -I -c "import sys; sys.path.insert(0, r'$site'); import importlib.metadata, yomihime_sdk; assert importlib.metadata.version('yomihime-module-sdk') == '1.5.0'; assert yomihime_sdk.CONTRACT_VERSION == '1.5.0'; assert yomihime_sdk.CONTRACT_REVISION == 'R1-MODULE-PAGES'; print(yomihime_sdk.__version__)"
```

The artifact test performs two builds with the fixed `SOURCE_DATE_EPOCH` and
compares both wheel hashes with the reviewed pin. It installs the second wheel
offline into an otherwise empty target directory and uses `python -I` to check
the installed SDK, compatibility-shim, and Core import identities. It then
extracts all six example files through `importlib.resources` from that wheel.
The artifact check proves deterministic packaging and import identity, not
CoreRuntime activation or AstrBot host compatibility. The separate E scanner
probe reports platform support or an explicit unsupported result; scanner
support is not an artifact property.

Run the focused checks from the repository root:

```text
python -m unittest tests.sdk.test_public_exports tests.sdk.test_empty_template tests.sdk.test_offline_sample tests.packaging.test_sdk_artifact_install -v
ruff check yomihime_sdk examples/empty_module examples/offline_sample tests/sdk tests/packaging/test_sdk_artifact_install.py
ruff format --check yomihime_sdk examples/empty_module examples/offline_sample tests/sdk tests/packaging/test_sdk_artifact_install.py
python -m compileall -q yomihime_sdk examples/empty_module examples/offline_sample tests/sdk tests/packaging/test_sdk_artifact_install.py
git diff --check -- pyproject.toml yomihime_sdk examples/empty_module examples/offline_sample docs/module-sdk.md tests/sdk tests/packaging/test_sdk_artifact_install.py .coordination/handoffs/CORE-HARDENING-01-K-ARTIFACT.md
```

Both example trees are included in the same wheel under
`yomihime_sdk/_examples/{empty_module,offline_sample}/`. The empty package has
no module declarations. The offline sample declares two modules: `status` has
10 commands, two Tools, two schedules, two subscriptions, and three config
fields; `source` has one independent public command. The status module
demonstrates a required capability dependency, configuration-based health,
command-only account/subscription operations, and public/private collection.
The package contains no service stubs; component and installed-artifact tests
provide explicit inert doubles through an actual `ModuleServices` DTO. The
sample returns deterministic SDK display/fact DTOs and
does not send host messages, perform network I/O, or start background work.
Only a later E/CoreRuntime integration can establish host dispatch and
lifecycle behavior.

The ordinary, non-sensitive `sample_subscriptions_enabled` field defaults to
the boolean `true`. It is a sample subscription gate, not an authorization
grant. A deploying host must explicitly bind the gate to the verified
`offline_sample/status` manifest using the Core's trusted deployment mapping.
Discovery alone does not establish that trust; without the mapping,
subscription creation and delivery remain closed. The integration fixture
uses unmodified installed-wheel resources and normal Core initialization,
administrator operations, and scheduler fences to exercise this binding.

## Example resources

The wheel contains these exact entries:

```text
yomihime_sdk/_examples/empty_module/manifest.json
yomihime_sdk/_examples/empty_module/module.py
yomihime_sdk/_examples/empty_module/README.md
yomihime_sdk/_examples/offline_sample/manifest.json
yomihime_sdk/_examples/offline_sample/module.py
yomihime_sdk/_examples/offline_sample/README.md
```

Retrieve them from an installed artifact with `importlib.resources`; do not
copy examples from the source checkout when validating a built wheel. The
packaging test extracts both resources into temporary extension package roots,
parses the manifests against the frozen E schema, checks each module's
declaration counts, and exercises both factories and representative handlers.
It does not instantiate a second extension runtime.

Unsupported Python is declared through wheel metadata `Requires-Python >=3.11`
so pip rejects it before installing the artifact. The SDK performs no install
or upgrade actions. Manifest incompatibility remains the E parser's stable
reject path; a missing runtime dependency cannot occur because this wheel has
no runtime dependencies.

## H-ADMIN-02 host-independent administration revision

The 2026-10-05 product decision replaces the universal independent-credential requirement. Core authorization is host independent: explicitly trusted authorities attest subjects, authentication sources, exact operations/resources, and live request lifetimes. Native Core credentials remain one source with their own durable ACTIVE/generation checks; a trusted Host source uses its own ownership/epoch/expiry and never fabricates a native key.

In AstrBot deployment, the normal formally authenticated management user needs no second Core credential. The Adapter attests a server-owned request; JSON roles, page assets, ordinary API keys and public-query proofs cannot mint administrative grants. Core checks the bounded policy again before effects and inside configuration/rollback transactions. Only the four declared ordinary fields are opened; no lifecycle, secret, subscription or future-module permissions follow automatically.

SDK descriptive IDs and data classes are not authorization evidence. Tests must retain forged, wrong-source/resource, ended/expired/cancelled requests, transaction fencing, revision conflict and recovery rejection cases. Core standalone/test adapters require no AstrBot import or FF14/config-service changes. The exact contract and startup-failure boundaries are frozen in [host-management-authorization-contract.md](host-management-authorization-contract.md). Artifact/source pins must be rebuilt and statically updated for the final implementation; source presence does not claim runtime acceptance.

## Contract 1.5.0

`CommandDescriptor.parameter_mode` defaults to `structured`; `raw_tail` declares a single `raw_tail_parameter` referencing a closed capability string property and requires an empty mapping. The generic Host preserves the original tail and Core validates it.

`ModuleManifest.pages` and `resources` default to empty tuples. `PageDescriptor(route_id, title, entry, order=0, access="public_web", capability_id=None, styles=())` references `PageResource(path, sha256)` declarations. These are trusted local relative assets with bounded reads and SHA-256 verification; public declarations grant no user authority. Lifecycle ACTIVE and the existing run identity fence callable page catalog entries. SDK 1.4.0 and older examples remain supported.

Optional `ModuleServices.storage` contains read-only `ModuleStoragePaths` describing stable module ownership directories. Existing SQLite config, cache, secrets and records retain their shared transactions. Stop/uninstall never deletes these paths or records.
