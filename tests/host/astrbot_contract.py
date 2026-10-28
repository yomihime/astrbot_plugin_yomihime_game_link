"""Pinned upstream AstrBot contracts, loaded without importing or starting Host.

Only public metadata and hashes live here; contract code comes from a separately
prepared checkout. Missing prerequisites are errors, never skips or DTO mocks.
"""

import ast
import hashlib
import importlib.metadata
import inspect
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any, cast
from unittest.mock import patch
from urllib.parse import quote

SOURCE_ENV = "YGL_TEST_ASTRBOT_SOURCE"
HOST_VERSION = "4.29.1"
HOST_COMMIT = "dffe922773725e9c178392c423676ad65044748c"
HOST_REPOSITORY = "https://github.com/AstrBotDevs/AstrBot"
# SHA256 of UTF-8 source with universal newlines (LF), identical on Windows/Linux.
SOURCE_SHA256 = {
    "astrbot/core/utils/upload.py": "211c23524bee41187ac4e350ad48430113bf5491043e1e8a3518dcfd5d2b4518",
    "astrbot/dashboard/services/config_service.py": "8775d098b2eb4c521106da14c2860844bea85c62467542b66810448796c203b8",
    "astrbot/core/pipeline/process_stage/stage.py": "b4e83a1d81955351a863ddb56016153b531333b6b9e4cd0df2e07db4ea13a268",
    "astrbot/core/star/context.py": "10f5f5dd40491564ee196854abb5f6a4e4e6fc46a6c5e62e29935e2c53b6294f",
    "astrbot/core/star/register/star_handler.py": "8eeeb59901d9944f10e326bfa10f76eee0629c3f380ed1fb7c5c47b3e50dc266",
    "astrbot/core/agent/tool.py": "6f5e22a90ebc4c11e87af169d62ca49347c772eab479bfa62a230547e4bb43a5",
    "astrbot/core/agent/agent.py": "4e81bae926f5351ab7a58619b4c13a3dfe80c8673a2e66f07441556c26f8a119",
    "astrbot/core/agent/handoff.py": "6665df6c6f53ffe41b0afed8c0316f65fc0885cc5b979ca9ae508f7376d9ab13",
    "astrbot/core/astr_agent_tool_exec.py": "4af653ba8abea62958124d10dd2cf56e1f14d1098ff1583865660d378bf51d04",
    "astrbot/core/provider/register.py": "596f9090fbd6dc7e97259b678865c92aeb126b5a7eb563e7836bc432d50984e4",
    "astrbot/core/tools/computer_tools/__init__.py": "356585f48e277586efeb69de1db2c864d644434a88b9a12a996ccbf168a43760",
    "astrbot/core/tools/computer_tools/shell.py": "d4981b4e15a0912746315d5d2e254ccad082f922845da0a68ee85caeb52c8e28",
    "astrbot/core/tools/computer_tools/python.py": "413563165c3e9a10f3d55ec5cc491e7f61e184f5d2073bf783ad20f1e40a0488",
    "astrbot/core/tools/computer_tools/fs.py": "3b178e01c2392fd736b609cebc7d002a45240ecb1c73a285d09fcb4cfd8d92b5",
    "astrbot/core/tools/computer_tools/cua.py": "97d3550335c2ead8513d0f75c810682c775ec0fa6d8ecc20faef6a2bfc25b00c",
    "astrbot/core/subagent_orchestrator.py": "0268e783a82eaf5461ce54c4f804ff92b53db75909557f5611c3a265adabc90f",
    "astrbot/core/agent/runners/tool_loop_agent_runner.py": "ac20138ada8ce83bc88ffec72e40ef4d5ac174a3695a27ae84ad2d9912537645",
    "astrbot/core/astr_main_agent.py": "a466eaff725b0c0834a694008ea960461f71ce98eda3ee78dbcc2a12ac0117c5",
    "astrbot/core/provider/func_tool_manager.py": "bef0154d4bd50b924cd9e1be82426d67bfc1f410c2f725d5812d9d936316eab4",
    "astrbot/core/astr_agent_context.py": "6c99c09bf086ce484c9c2f7d180fe3d17c268be398a013e68066a7b92dfa0c5e",
    "astrbot/core/platform/astr_message_event.py": "1c51db0b7e78e8cf6c8aa0f64c621d87e2af72a21fee0ff1524b68e6b5850954",
    "astrbot/core/star/star_manager.py": "6146ef6162716a6aa64cae5bf13bd93bcdd6c681a45e35ffd017f690715255fd",
    "astrbot/core/agent/tool_executor.py": "f061a0541fc968a1952d0506d5ea5e2fe96f28caa07d0ee625a38f9d5cad9d7a",
    "astrbot/core/agent/run_context.py": "1dc78f2312877a1fee7ec53ed62fd95329c00c8172f765ea6814f0126c562c58",
    "astrbot/dashboard/services/plugin_page_service.py": "262c68315e5f957d8c0dcd13904b958a6d8268aa2b3dfc236c915f9f8418771f",
    "astrbot/core/config/astrbot_config.py": "3a3223bbbc2212e856a48e11a5d0c755bafd8b5c375b87f30f847d826ff3af7a",
    "pyproject.toml": "dee4024d40f70fdfc986d9e81b46024d47fdde076b5b4e888d586e422021ab70",
    "astrbot/__init__.py": "2c31376dc80eb209592e5d75b7ff239fb0fa2865026d0bb12d15c372a8957da1",
    "astrbot/api/web.py": "faeb09a65f0f0559edfc4794ce152b5465e18c31ee4ab40c0a4e0f5ec8579d04",
    "astrbot/dashboard/responses.py": "2f3638f8196fbdd3315e6fe021a3163174e310b04f781b147ef5073c5f109e38",
    "astrbot/dashboard/plugin_page_auth.py": "69de4913a649f847bee3d24f30b8450ff77f289ce2f6d15c6844ecf0dc567d0d",
    "astrbot/dashboard/api/auth.py": "1ac9e90ee25412656f295aadcb48a119386ef62dc0c7516cf50e1a40512026ee",
    "astrbot/dashboard/server.py": "d911fd8273505aad4e088fafe55b6b634ea67e3515d67107dc6a23a76abea821",
    "astrbot/dashboard/asgi_runtime.py": "adb7abe6ae3f09e27440ce72a65a174d509c97bb7827c450f74c055057f14991",
    "astrbot/dashboard/api/plugins.py": "4ae3fa76ac36ec4cfb286057627cf97cee589ee6d7d8a36a83dc2fd43897c074",
    "astrbot/dashboard/services/auth_service.py": "9ef9124915d99eba5c1754b5c6efdc6cb508991140309fb57d0c6c1f0c216ba8",
}
SOURCE_OBJECT_SHA256 = {
    "dashboard/src/components/shared/AstrBotConfig.vue": "48332387d458377c1dc278b09328124ef787ebc33a6423338797117b4d7c4ebc",
    "dashboard/src/views/ExtensionPage.vue": "abdc929ada08aaa2304509673e62bdd887b5a43045c496191cbe368ecb1d53d7",
}

TEST_DEPENDENCIES = {
    "PyJWT": "2.10.1",
    "fastapi": "0.135.2",
    "starlette": "0.52.1",
    "httpx": "0.28.1",
    "Quart": "0.20.0",
    "jsonschema": "4.23.0",
}


class HostContractEnvironmentError(RuntimeError):
    """The required independent Host contract environment is unavailable."""


def _environment_error(detail):
    return HostContractEnvironmentError(
        f"AstrBot contract test environment: {detail}. "
        f"Prepare {HOST_REPOSITORY} v{HOST_VERSION} at {HOST_COMMIT}, "
        f"set {SOURCE_ENV} to its astrbot directory and install "
        "requirements-test.txt; see CONTRIBUTING.md."
    )


def _git(root, *arguments):
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _environment_error("Git cannot verify the independent checkout") from exc
    return result.stdout.strip()


def _literal_assignment(source, name):
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise _environment_error(f"required Host constant {name} is missing")


def validate_host_source():
    """Revalidate each call and return the verified source snapshot for execution."""
    if sys.version_info < (3, 12):
        raise _environment_error("Host contracts require Python >= 3.12")
    configured = os.environ.get(SOURCE_ENV)
    if not configured:
        raise _environment_error(f"{SOURCE_ENV} is not set")
    source = Path(configured).expanduser().resolve()
    if source.name != "astrbot" or not source.is_dir():
        raise _environment_error(f"{SOURCE_ENV} must point to checkout/astrbot")
    root = source.parent
    if Path(_git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise _environment_error(
            "source must belong to its own independent Git checkout"
        )
    commit = _git(root, "rev-parse", "HEAD")
    if commit != HOST_COMMIT:
        raise _environment_error(
            f"Host commit mismatch: expected {HOST_COMMIT}, got {commit}"
        )
    sources = {}
    for relative in SOURCE_SHA256:
        try:
            sources[relative] = (root / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise _environment_error(
                f"required source is missing or unreadable: {relative}"
            ) from exc
    try:
        project_version = tomllib.loads(sources["pyproject.toml"])["project"]["version"]
        package_version = _literal_assignment(
            sources["astrbot/__init__.py"], "__version__"
        )
    except (ValueError, SyntaxError, KeyError) as exc:
        raise _environment_error("Host version metadata is invalid") from exc
    if project_version != HOST_VERSION or package_version != HOST_VERSION:
        raise _environment_error(f"Host version mismatch: expected {HOST_VERSION}")
    for relative, expected in SOURCE_SHA256.items():
        if hashlib.sha256(sources[relative].encode("utf-8")).hexdigest() != expected:
            raise _environment_error(f"Host source SHA256 mismatch: {relative}")
    dirty = _git(
        root, "status", "--porcelain=v1", "--untracked-files=all", "--", *SOURCE_SHA256
    )
    if dirty:
        raise _environment_error(
            "related Host source has staged, unstaged or untracked changes"
        )
    for relative, expected in SOURCE_OBJECT_SHA256.items():
        try:
            actual = subprocess.run(
                ["git", "-C", str(root), "show", f"{HOST_COMMIT}:{relative}"],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise _environment_error(
                f"required Host Git object is unavailable: {relative}"
            ) from exc
        if hashlib.sha256(actual.encode("utf-8")).hexdigest() != expected:
            raise _environment_error(f"Host Git object SHA256 mismatch: {relative}")
        sources[relative] = actual
    return source, sources


def host_config_integrity():
    """Actual upstream method; callers supply temporary dictionaries only."""
    import logging

    _, sources = validate_host_source()
    tree = ast.parse(sources["astrbot/core/config/astrbot_config.py"])
    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AstrBotConfig"
    )
    method = next(
        node
        for node in owner.body
        if isinstance(node, ast.FunctionDef) and node.name == "check_config_integrity"
    )
    namespace = {"logger": logging.getLogger("host-config-contract")}
    exec(
        compile(
            ast.Module(body=[method], type_ignores=[]), "actual_host_config", "exec"
        ),
        namespace,
    )
    return type("ActualHostConfigMethod", (), {method.name: namespace[method.name]})()


def host_plugin_config_display(config):
    """Execute fixed upstream wrapper against a synthetic plugin config only."""
    from types import SimpleNamespace

    _, sources = validate_host_source()
    tree = ast.parse(sources["astrbot/dashboard/services/config_service.py"])
    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ConfigDisplayService"
    )
    method = next(
        node
        for node in owner.body
        if isinstance(node, ast.FunctionDef) and node.name == "get_plugin_config"
    )
    namespace = {
        "star_registry": [SimpleNamespace(name="game_link", config=config, i18n={})]
    }
    exec(
        compile(
            ast.Module(body=[method], type_ignores=[]),
            "actual_host_config_display",
            "exec",
        ),
        namespace,
    )
    service = type(
        "ActualHostConfigDisplay", (), {method.name: namespace[method.name]}
    )()
    return service.get_plugin_config("game_link")


def validate_test_dependencies():
    for package, expected in TEST_DEPENDENCIES.items():
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as exc:
            raise _environment_error(
                f"missing test dependency {package}=={expected}"
            ) from exc
        if actual != expected:
            raise _environment_error(
                f"test dependency mismatch: {package}=={expected} required, got {actual}"
            )


def _host_module(source, sources, relative):
    # Execute the exact validated text, avoiding startup imports and bytecode caches.
    name = "ygl_host_" + relative.replace("/", "_")
    module = ModuleType(name)
    module.__file__ = str(source / relative)
    sys.modules[name] = module
    try:
        exec(
            compile(sources["astrbot/" + relative], module.__file__, "exec"),
            module.__dict__,
        )
    except ImportError as exc:
        raise _environment_error(
            f"a transitive test dependency could not be imported for {relative}"
        ) from exc
    return module


def host_contracts():
    """Use actual public DTO and unchanged Host auth/dispatch AST without startup."""

    source, sources = validate_host_source()
    validate_test_dependencies()

    try:
        import jwt
        from fastapi import Request
        from starlette.responses import JSONResponse
    except ImportError as exc:
        raise _environment_error("test dependencies could not be imported") from exc

    upload = _host_module(source, sources, "core/utils/upload.py")
    with patch.dict(sys.modules, {"astrbot.core.utils.upload": upload}):
        web = _host_module(source, sources, "api/web.py")
        responses = _host_module(source, sources, "dashboard/responses.py")
        page_auth = _host_module(source, sources, "dashboard/plugin_page_auth.py")
        asgi = _host_module(source, sources, "dashboard/asgi_runtime.py")
    namespace = {
        "__name__": "host_contract_probe",
        "jwt": jwt,
        "Request": Request,
        "JSONResponse": JSONResponse,
        "Any": Any,
        "cast": cast,
        "re": re,
        "quote": quote,
        "ApiError": responses.ApiError,
        "error": responses.error,
        "PluginPageAuth": page_auth.PluginPageAuth,
        "PluginRequest": web.PluginRequest,
        "bind_request_context": web.bind_request_context,
        "DASHBOARD_JWT_COOKIE_NAME": _literal_assignment(
            sources["astrbot/dashboard/services/auth_service.py"],
            "DASHBOARD_JWT_COOKIE_NAME",
        ),
    }
    namespace.update(
        {
            "DashboardRequestState": asgi.DashboardRequestState,
            "call_request_view": asgi.call_request_view,
            "FastAPIAppAdapter": asgi.FastAPIAppAdapter,
        }
    )
    auth = ast.parse(sources["astrbot/dashboard/api/auth.py"])
    names = {
        "_get_dashboard_state_username",
        "_extract_dashboard_jwt",
        "require_dashboard_user",
    }
    exec(
        compile(
            ast.Module(
                body=[
                    node for node in auth.body if getattr(node, "name", None) in names
                ],
                type_ignores=[],
            ),
            "actual_host_auth",
            "exec",
        ),
        namespace,
    )
    server = ast.parse(sources["astrbot/dashboard/server.py"])
    owner = next(
        node
        for node in server.body
        if isinstance(node, ast.ClassDef)
        and any(
            getattr(child, "name", None) == "auth_middleware" for child in node.body
        )
    )
    methods = [
        node
        for node in owner.body
        if getattr(node, "name", None)
        in {"auth_middleware", "_validate_dashboard_token", "_extract_dashboard_jwt"}
    ]
    cls = ast.ClassDef(
        name="HostAuth", bases=[], keywords=[], body=methods, decorator_list=[]
    )
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])),
            "actual_host_middleware",
            "exec",
        ),
        namespace,
    )

    async def no_rate_limit(*_):
        return None

    namespace["HostAuth"]._apply_auth_rate_limit = no_rate_limit
    plugins = ast.parse(sources["astrbot/dashboard/api/plugins.py"])
    names = {
        "_normalize_plugin_api_route",
        "_plugin_api_route_pattern",
        "_match_registered_web_api",
        "_plugin_extension_legacy_path",
        "_call_plugin_extension",
    }

    async def run_maybe_async(callback):
        value = callback()
        return await value if inspect.isawaitable(value) else value

    namespace["run_maybe_async"] = run_maybe_async
    exec(
        compile(
            ast.Module(
                body=[
                    node
                    for node in plugins.body
                    if getattr(node, "name", None) in names
                ],
                type_ignores=[],
            ),
            "actual_host_dispatch",
            "exec",
        ),
        namespace,
    )
    return web, namespace


def page_asset_contract():
    """Pinned official view discovery/path-token/HTML contracts, without startup."""
    import posixpath
    from dataclasses import dataclass
    from urllib.parse import urlencode, urlunsplit

    import aiofiles.ospath as aio_ospath

    _, sources = validate_host_source()
    source = sources["astrbot/dashboard/services/plugin_page_service.py"]
    tree = ast.parse(source)
    owner = next(
        n
        for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == "PluginPageService"
    )
    names = {
        "normalize_plugin_page_name",
        "normalize_plugin_page_path",
        "build_plugin_page_view_content_path",
        "get_plugin_page_bridge_sdk_url",
        "process_plugin_page_html",
        "apply_theme_to_html",
        "discover_plugin_pages",
        "resolve_plugin_pages_root",
    }
    methods = [n for n in owner.body if getattr(n, "name", None) in names]
    if {n.name for n in methods} != names:
        raise _environment_error("official view contract method missing")
    namespace = {
        "__name__": "host_view_contract_probe",
        "re": re,
        "posixpath": posixpath,
        "quote": quote,
        "urlencode": urlencode,
        "urlunsplit": urlunsplit,
        "StarMetadata": Any,
        "Path": Path,
        "aio_ospath": aio_ospath,
        "dataclass": dataclass,
    }
    # Complete real upstream dataclass and constants, no replacement DTOs.
    definitions = [
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PluginPage"
    ]
    constants = {
        "PLUGIN_PAGE_ENTRY_FILE_NAME",
        "PLUGIN_PAGE_ROOT_DIR_NAMES",
        "_HTML_ASSET_ATTR_RE",
    }
    definitions = [
        n
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id in constants for t in n.targets)
    ] + definitions
    cls = ast.ClassDef(
        name="PluginPageService", bases=[], keywords=[], body=methods, decorator_list=[]
    )
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(body=[*definitions, cls], type_ignores=[])
            ),
            "actual_host_view_contract",
            "exec",
        ),
        namespace,
    )
    return namespace["PluginPageService"](), namespace
