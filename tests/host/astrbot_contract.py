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
TEST_DEPENDENCIES = {
    "PyJWT": "2.10.1",
    "fastapi": "0.135.2",
    "starlette": "0.52.1",
    "httpx": "0.28.1",
    "Quart": "0.20.0",
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
