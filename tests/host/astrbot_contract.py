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
from urllib.parse import quote

SOURCE_ENV = "YGL_TEST_ASTRBOT_SOURCE"
HOST_VERSION = "4.28.2"
HOST_COMMIT = "3c7adafa1397e182d60b1016bf88759265113c8a"
HOST_REPOSITORY = "https://github.com/AstrBotDevs/AstrBot"
# SHA256 of UTF-8 source with universal newlines (LF), identical on Windows/Linux.
SOURCE_SHA256 = {
    "astrbot/dashboard/services/config_service.py": "f9efc39d92432dec56180f3593b2f2211584a09ee00ed6fce4738e17187cf472",
    "astrbot/core/pipeline/process_stage/stage.py": "b4e83a1d81955351a863ddb56016153b531333b6b9e4cd0df2e07db4ea13a268",
    "astrbot/core/star/context.py": "a9a2772080b463583af2c3082b4c13e3612ebd9fda0d234147be462394a1ba2e",
    "astrbot/core/star/register/star_handler.py": "8eeeb59901d9944f10e326bfa10f76eee0629c3f380ed1fb7c5c47b3e50dc266",
    "astrbot/core/agent/tool.py": "e761f599d3d6a8ab3c4afb47be6bb093fdad3ac5ffa9bd2d71cf58ee02c32935",
    "astrbot/core/provider/func_tool_manager.py": "bef0154d4bd50b924cd9e1be82426d67bfc1f410c2f725d5812d9d936316eab4",
    "astrbot/core/astr_agent_context.py": "6c99c09bf086ce484c9c2f7d180fe3d17c268be398a013e68066a7b92dfa0c5e",
    "astrbot/core/platform/astr_message_event.py": "1c51db0b7e78e8cf6c8aa0f64c621d87e2af72a21fee0ff1524b68e6b5850954",
    "astrbot/core/star/star_manager.py": "9f6d2f51f8ef006a7026e7aec0b232b06f4743734c4afdca494a0e6cb539c3ce",
    "astrbot/core/agent/tool_executor.py": "f061a0541fc968a1952d0506d5ea5e2fe96f28caa07d0ee625a38f9d5cad9d7a",
    "astrbot/core/agent/run_context.py": "1dc78f2312877a1fee7ec53ed62fd95329c00c8172f765ea6814f0126c562c58",
    "astrbot/dashboard/services/plugin_page_service.py": "18e41ee0bb5d25e5ced473224cd4a89686d794dae0c2a74a053f8b8d154cef33",
    "astrbot/core/config/astrbot_config.py": "1907838acd129201e92c96dd8b4a704183fa72150226ccaf1f4e82f9426ba014",
    "pyproject.toml": "9834b677dae6bacfc2f1e75ed6da1e8e99c7f011bf31deeb278903ff8786689c",
    "astrbot/__init__.py": "a199525f1f93fcc5799bcbf847381094de975e1a78ec161e0579decac715373c",
    "astrbot/api/web.py": "3e171e2e0f48769ac481cf494576a9f4cd41d3ecd0c9f0af67722026f7228ed6",
    "astrbot/dashboard/responses.py": "2f3638f8196fbdd3315e6fe021a3163174e310b04f781b147ef5073c5f109e38",
    "astrbot/dashboard/plugin_page_auth.py": "c853a4e56beaea3a9ca51aadf6dd050eac369fbbee12334c0d556872ea26cf21",
    "astrbot/dashboard/api/auth.py": "1ac9e90ee25412656f295aadcb48a119386ef62dc0c7516cf50e1a40512026ee",
    "astrbot/dashboard/server.py": "cf715728509af8805658500a39602b0f058987feabaaf855939bdbdff07b62f7",
    "astrbot/dashboard/asgi_runtime.py": "8013d0837b0ce8dd819c4933a4d72c98d70011da7d17d1ad8d58fb554ba5cfc0",
    "astrbot/dashboard/api/plugins.py": "b2db96766684bff2af4e652fab37ba2b2cee8d1796fd0ef3e35a4dac7b5ccd86",
    "astrbot/dashboard/services/auth_service.py": "9ef9124915d99eba5c1754b5c6efdc6cb508991140309fb57d0c6c1f0c216ba8",
}
SOURCE_OBJECT_SHA256 = {
    "dashboard/src/components/shared/AstrBotConfig.vue": "92fd19c409c7b194a66df6e4c2ad0f89e3c98b10be55d4f762c456f4afd0bce1",
    "dashboard/src/views/ExtensionPage.vue": "2fdc98b9e0291e14fed0d888081caf8cbb5ad7d5d552db73a006fad5e1c3526a",
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
    """Unchanged fixed Host HTML/CSS/JS rewrite methods, without Host startup."""
    import posixpath
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    _, sources = validate_host_source()
    tree = ast.parse(sources["astrbot/dashboard/services/plugin_page_service.py"])
    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PluginPageService"
    )
    names = {
        "normalize_plugin_page_name",
        "normalize_plugin_page_path",
        "is_rewritable_asset_url",
        "resolve_referenced_asset_path",
        "build_plugin_page_asset_url",
        "build_plugin_page_content_path",
        "get_plugin_page_bridge_sdk_url",
        "is_js_relative_module_specifier",
        "rewrite_relative_asset_url",
        "rewrite_plugin_page_html",
        "rewrite_plugin_page_css",
        "rewrite_plugin_page_js",
        "apply_theme_to_html",
        "discover_plugin_pages",
    }
    methods = [node for node in owner.body if getattr(node, "name", None) in names]
    namespace = {
        "re": re,
        "posixpath": posixpath,
        "parse_qsl": parse_qsl,
        "quote": quote,
        "urlencode": urlencode,
        "urlsplit": urlsplit,
        "urlunsplit": urlunsplit,
        "StarMetadata": Any,
        "Path": Path,
    }
    from types import SimpleNamespace

    async def isfile(path):
        return Path(path).is_file()

    namespace["aio_ospath"] = SimpleNamespace(isfile=isfile)
    namespace["PLUGIN_PAGE_ENTRY_FILE_NAME"] = _literal_assignment(
        sources["astrbot/dashboard/services/plugin_page_service.py"],
        "PLUGIN_PAGE_ENTRY_FILE_NAME",
    )
    namespace["PluginPage"] = lambda **values: SimpleNamespace(**values)
    regexes = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id
            in {
                "_HTML_ASSET_ATTR_RE",
                "_CSS_URL_RE",
                "_JS_DYNAMIC_IMPORT_RE",
                "_JS_MODULE_FROM_RE",
                "_JS_SIDE_EFFECT_IMPORT_RE",
            }
            for target in node.targets
        )
    ]
    cls = ast.ClassDef(
        name="PluginPageService", bases=[], keywords=[], body=methods, decorator_list=[]
    )
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(body=[*regexes, cls], type_ignores=[])
            ),
            "actual_host_page_asset_rewrite",
            "exec",
        ),
        namespace,
    )
    return namespace["PluginPageService"](), namespace
