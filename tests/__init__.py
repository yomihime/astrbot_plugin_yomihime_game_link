"""Load the plugin under a test-only namespace without importing AstrBot.

Production modules use package-relative imports. This alias exercises the same
package layout without assuming a particular checkout directory or host loader.
"""

import importlib.util
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ygl_test_subject", _root / "__init__.py", submodule_search_locations=[str(_root)]
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("Cannot load the plugin package for local verification")
if "ygl_test_subject" not in sys.modules:
    _package = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _package
    _spec.loader.exec_module(_package)

# Tests use the development SDK as the exact test subject graph. Production never
# changes canonical names; runtime bundle tests exercise independent private SDKs.
import importlib  # noqa: E402

_test_sdk = importlib.import_module("yomihime_game_link_sdk")
for _name, _module in tuple(sys.modules.items()):
    if _name == "yomihime_game_link_sdk" or _name.startswith("yomihime_game_link_sdk."):
        sys.modules["ygl_test_subject." + _name] = _module
