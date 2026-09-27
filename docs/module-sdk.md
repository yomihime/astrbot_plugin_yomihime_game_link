# Yomihime module SDK

`yomihime-module-sdk` is the supported, host-independent import surface for
extensions. Import public contracts from `yomihime_sdk`. Their canonical source
declarations live in `yomihime_sdk/api/`; the repository-root `api/` package is
a compatibility forwarding surface whose exported objects retain identity
with the canonical declarations. The SDK does not import Core runtime
implementations or AstrBot objects.

The current artifact is `yomihime-module-sdk` version `1.1.0`, carrying contract
`1.1.0` (`B04-C-13`) and compatible contract versions `1.0.0` and `1.1.0`.
Extension declarations use manifest schema v1 and factory ABI v1. The declared
minimum is Python 3.11 because the public API uses `StrEnum`. A local
installed-wheel check proves the bundled SDK imports from the installed site
directory and that compatibility exports share object identity; it does not
prove activation inside the target AstrBot environment. Host Python selection,
installation location, and import precedence remain W0-H evidence.

## Offline build and isolated install

The build backend is setuptools `80.9.0` with wheel `0.45.1`, pinned in
`pyproject.toml`. The checked local build machine already has both versions.
Build with `--no-build-isolation` and `--no-index` so no build helper is
downloaded. A different build machine must provide those exact build tools
before running this command; the project has no runtime dependencies.

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
python -m pip install --no-index --no-deps --target $site (Join-Path $wheelhouse 'yomihime_module_sdk-1.1.0-py3-none-any.whl')
python -I -c "import sys; sys.path.insert(0, r'$site'); import importlib.metadata, yomihime_sdk; assert importlib.metadata.version('yomihime-module-sdk') == '1.1.0'; assert yomihime_sdk.CONTRACT_VERSION == '1.1.0'; print(yomihime_sdk.__version__)"
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
10 commands, two Tools, two schedules, two subscriptions, and two config
fields; `source` has one independent public command. The status module
demonstrates a required capability dependency, configuration-based health,
command-only account/subscription operations, and public/private collection.
The package contains no service stubs; component and installed-artifact tests
provide explicit inert doubles through an actual `ModuleServices` DTO. The
sample returns deterministic SDK display/fact DTOs and
does not send host messages, perform network I/O, or start background work.
Only a later E/CoreRuntime integration can establish host dispatch and
lifecycle behavior.

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
