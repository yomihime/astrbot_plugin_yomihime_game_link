# ruff: noqa: E402
# Imports are deliberately staged after offline guards and disposable-root checks.
"""Install an actual release ZIP in isolated official AstrBot and probe it."""

import argparse
import asyncio
import base64
import hashlib
import importlib
import io
import json
import os
import shutil
import socket
import sys
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--host-source",
    required=True,
    type=Path,
    help="Official AstrBot checkout root (v4.29.1)",
)
parser.add_argument("--plugin-zip", required=True, type=Path)
parser.add_argument(
    "--work-root",
    required=True,
    type=Path,
    help="New nonexistent disposable directory; never use a real AstrBot root",
)
parser.add_argument(
    "--font",
    type=Path,
    default=Path(__file__).resolve().parents[1]
    / "presentation/assets/fonts/NotoSansCJKsc-Regular.otf",
)
args = parser.parse_args()
if sys.flags.optimize:
    parser.error("Run without -O: smoke assertions must remain enabled")
host_source = args.host_source.resolve(strict=True)
plugin_zip = args.plugin_zip.resolve(strict=True)
font = args.font.resolve(strict=True)
root = args.work_root.absolute()
if root.exists() or root.is_symlink():
    parser.error(
        "--work-root must not already exist; choose a new disposable directory"
    )
if not (host_source / "astrbot/dashboard/server.py").is_file():
    parser.error("--host-source must be the official checkout root")
if not plugin_zip.is_file() or not font.is_file():
    parser.error("ZIP and font must be readable regular files")
if (
    hashlib.sha256(font.read_bytes()).hexdigest()
    != "2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b"
):
    parser.error("Font does not match the canonical offline fixture")
import subprocess

host_commit = subprocess.check_output(
    ["git", "-C", str(host_source), "rev-parse", "HEAD"], text=True
).strip()
if host_commit != "dffe922773725e9c178392c423676ad65044748c":
    parser.error(
        "Expected official v4.29.1 commit dffe922773725e9c178392c423676ad65044748c"
    )
if subprocess.check_output(
    ["git", "-C", str(host_source), "status", "--porcelain", "--untracked-files=no"],
    text=True,
).strip():
    parser.error("Official Host tracked files must be clean")
# Fail before Host startup if installation might invoke online dependency recovery.
import importlib.metadata
import zipfile

from packaging.requirements import Requirement

with zipfile.ZipFile(plugin_zip) as archive:
    requirements_text = archive.read("requirements.txt").decode("utf-8")
for line in requirements_text.splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    requirement = Requirement(line)
    if requirement.url:
        parser.error("Direct URL dependencies are not allowed in this offline smoke")
    if requirement.marker and not requirement.marker.evaluate():
        continue
    try:
        installed_version = importlib.metadata.version(requirement.name)
    except importlib.metadata.PackageNotFoundError:
        parser.error(
            f"Preinstall missing dependency before offline smoke: {requirement}"
        )
    if installed_version not in requirement.specifier:
        parser.error(f"Preinstall required version before offline smoke: {requirement}")
root.mkdir(parents=True, exist_ok=False)
root = root.resolve()
sys.path[:0] = [str(root), str(host_source)]
os.chdir(root)
os.environ["ASTRBOT_ROOT"] = str(root)
os.environ["ASTRBOT_TEST_MODE"] = "true"
os.environ["ASTRBOT_DASHBOARD_INITIAL_PASSWORD"] = "IsolatedSmokePassword123!"
os.environ.pop("ASTRBOT_RESET_DASHBOARD_PASSWORD", None)
os.environ.pop("YGL_SECRET_KEY", None)
# Windows Proactor creates its self-pipe with socketpair during loop creation.
# Complete that runtime setup before denying all Host and plugin socket calls.
runner = asyncio.Runner()
runner.get_loop()
# Fully offline fixture: deny DNS and sockets before Host imports. ASGI uses no sockets.
os.environ["ASTRBOT_DISABLE_METRICS"] = "1"
attempts = []
offline_jobs = []


def deny_network(*args, **kwargs):
    attempts.append("network_operation_denied")
    raise OSError("Network prohibited in fully offline integration fixture")


socket.getaddrinfo = deny_network
socket.socket.connect = deny_network
socket.socket.connect_ex = deny_network
socket.socket.sendto = deny_network


# No subprocess may bypass Python network guards during the smoke.
class OfflinePopen(subprocess.Popen):
    def __init__(self, *args, **kwargs):
        attempts.append("subprocess_denied")
        raise OSError(
            "Subprocess prohibited during offline smoke; preinstall dependencies"
        )


subprocess.Popen = OfflinePopen
import aiohttp


async def deny_http(*args, **kwargs):
    from urllib.parse import urlsplit

    target = urlsplit(str(args[2] if len(args) > 2 else kwargs.get("url", "")))
    attempts.append({"blocked_http_host": target.hostname, "path": target.path})
    raise aiohttp.ClientConnectionError(
        "HTTP prohibited in fully offline integration fixture"
    )


aiohttp.ClientSession._request = deny_http
# Explicitly neutralize only unrelated Host background refreshes; record each call.
import astrbot.core.core_lifecycle as lifecycle_module
from astrbot.core import LogBroker, astrbot_config, db_helper
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
from astrbot.core.utils.t2i.network_strategy import NetworkRenderStrategy
from astrbot.dashboard.server import AstrBotDashboard


async def offline_t2i(self):
    offline_jobs.append("official_t2i_endpoint_refresh")


async def offline_metadata():
    offline_jobs.append("llm_metadata_refresh")


NetworkRenderStrategy.get_official_endpoints = offline_t2i
lifecycle_module.update_llm_metadata = offline_metadata
import httpx
import jwt
from PIL import Image

NAME = "astrbot_plugin_yomihime_game_link"


class Event:
    def __init__(self):
        self.calls = []

    def get_platform_id(self):
        return "isolated"

    def get_sender_id(self):
        return "tester"

    def get_session_id(self):
        return "tester"

    def get_message_type(self):
        return "FriendMessage"

    def get_message_str(self):
        return "/ygl help"

    def chain_result(self, components):
        return type("Result", (), {"chain": components})()

    async def send(self, chain):
        self.calls.append(chain)


async def main():
    astrbot_config["disable_metrics"] = True
    astrbot_config["provider"] = []
    astrbot_config["provider_sources"] = []
    astrbot_config["platform"] = []
    astrbot_config["dashboard"]["jwt_secret"] = (
        "synthetic-isolated-zip-smoke-key-at-least-32-bytes"
    )
    astrbot_config.save_config()
    # Fixed canonical font fixture, never downloaded during this test.
    assert (
        hashlib.sha256(font.read_bytes()).hexdigest()
        == "2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b"
    )
    target = (
        root
        / "data/plugin_data"
        / NAME
        / "resources/fonts"
        / "NotoSansCJKsc-Regular.otf"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(font, target)
    host = AstrBotCoreLifecycle(LogBroker(), db_helper)
    await host.initialize()
    result = {
        "zip_sha256": hashlib.sha256(plugin_zip.read_bytes()).hexdigest(),
        "secret_key_present": False,
        "host_commit": host_commit,
    }
    try:
        disposable = root / "input.zip"
        shutil.copy2(plugin_zip, disposable)
        result["install"] = await host.plugin_manager.install_plugin_from_file(
            str(disposable), ignore_version_check=False
        )
        star = next(s for s in host.star_context.get_all_stars() if s.name == NAME)
        assert star.i18n["zh-CN"]["views"]["00-game-link"]["title"] == "游戏连结"
        assert (
            star.i18n["en-US"]["pages"]["management"]["title"]
            == "Settings and Maintenance"
        )
        result["native_view_localization"] = True
        runtime = star.star_cls._runtime
        assert runtime.ready, "runtime not ready"
        assert runtime.core_runtime.registry.is_active("ff14/ff14"), "FF14 inactive"
        result["core_ready"] = runtime.ready
        result["ff14_active"] = True
        mainmod = sys.modules[type(star.star_cls).__module__]
        sdk = importlib.import_module("yomihime_game_link_sdk")
        assert sdk.__version__ == mainmod.SDK_VERSION
        Path(sdk.__file__).resolve().relative_to(
            Path(mainmod.__file__).resolve().parent
        )
        result["sdk_version"] = sdk.__version__
        result["sdk_local"] = True
        dashboard = AstrBotDashboard(host, db_helper, asyncio.Event())
        token = dashboard.asgi_app.state.services.auth.generate_jwt("isolated-admin")
        headers = {"Authorization": "Bearer " + token, "Origin": "https://test.invalid"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(dashboard.asgi_app),
            base_url="https://test.invalid",
            follow_redirects=True,
        ) as client:
            base = "/api/v1/plugins/extensions/" + NAME + "/admin/"
            r = await client.post(
                base + "catalog", headers=headers, json={}, follow_redirects=True
            )
            assert r.status_code == 200, (r.status_code, r.text)
            assert [h.status_code for h in r.history] == [307]
            result["settings_catalog"] = r.json()
            result["redirect_status"] = 307
            read = await client.post(base + "read", headers=headers, json={})
            assert read.status_code == 200
            rows = read.json()["data"]
            current_core = runtime.core_runtime
            recovery = await client.post(
                base + "recover",
                headers=headers,
                json={
                    "expected_revisions": {
                        key: row["revision"] for key, row in rows.items()
                    }
                },
            )
            assert recovery.status_code == 403
            assert runtime.core_runtime is current_core and runtime.ready
            assert (
                await client.post(base + "read", headers=headers, json={})
            ).status_code == 200
            result["ready_recovery_rejected_without_shutdown"] = True
            update = {
                "module_id": "game_link/core",
                "expected_revision": rows["game_link/core"]["revision"],
                "updates": [
                    {"field": "image_theme", "mode": "replace", "value": "deep_indigo"}
                ],
            }
            saved = await client.post(base + "update", headers=headers, json=update)
            assert saved.status_code == 200, saved.text
            assert (
                await client.post(base + "update", headers=headers, json=update)
            ).status_code == 409
            rows = (await client.post(base + "read", headers=headers, json={})).json()[
                "data"
            ]
            assert (
                rows["game_link/core"]["fields"]["image_theme"]["value"]
                == "deep_indigo"
            )
            assert (
                await current_core.theme_settings.current()
            ).core_theme == "deep_indigo"
            update["expected_revision"] = rows["game_link/core"]["revision"]
            update["updates"][0]["value"] = "invalid-theme"
            assert (
                await client.post(base + "update", headers=headers, json=update)
            ).status_code == 400
            update["updates"] = [{"field": "image_theme", "mode": "clear"}]
            assert (
                await client.post(base + "update", headers=headers, json=update)
            ).status_code == 200
            assert (
                await current_core.theme_settings.current()
            ).core_theme == "warm_paper"
            result["ordinary_update_read_invalid_and_stale_cas"] = True
            entry = await client.get(
                "/api/v1/plugins/" + NAME + "/views/00-game-link", headers=headers
            )
            assert entry.status_code == 200, (entry.status_code, entry.text)
            content_path = entry.json()["data"]["content_path"]
            assert "/views/00-game-link/_t/" in content_path
            from urllib.parse import urljoin

            view = await client.get(content_path)
            assert view.status_code == 200, (view.status_code, view.text)
            assert "./app.js" in view.text and "bridge-sdk.js?asset_token=" in view.text
            result["view_html_status"] = view.status_code
            for asset in (
                "app.js",
                "styles.css",
                "runtime.js",
                "module-loader.js",
                "module-assets/ff14/ff14/pages/dist/entry.js",
            ):
                asset_url = urljoin(content_path, asset)
                asset_response = await client.get(asset_url)
                assert asset_response.status_code == 200, (
                    asset,
                    asset_response.status_code,
                )
                result["view_asset_" + asset] = asset_response.status_code
            wrong_view = content_path.replace("/views/00-game-link/", "/views/shell/")
            denied = await client.get(wrong_view)
            assert denied.status_code == 401, ("wrong view scope", denied.status_code)
            result["wrong_view_token_status"] = denied.status_code
            for name, h in [
                ("missing", {}),
                (
                    "api_key",
                    {"X-API-Key": "synthetic-key", "Origin": "https://test.invalid"},
                ),
                ("wrong_origin", {**headers, "Origin": "https://wrong.invalid"}),
                (
                    "forged",
                    {
                        **headers,
                        "Authorization": "Bearer "
                        + jwt.encode(
                            {"username": "isolated-admin", "exp": 9999999999},
                            "other-synthetic-key-at-least-32-bytes",
                            algorithm="HS256",
                        ),
                    },
                ),
            ]:
                r = await client.post(
                    base + "catalog", headers=h, json={}, follow_redirects=True
                )
                assert r.status_code in (401, 403), (name, r.status_code, r.text)
                result["auth_" + name] = r.status_code
            for endpoint in ("font-status", "credential-status", "modules"):
                r = await client.post(
                    base + endpoint, headers=headers, json={}, follow_redirects=True
                )
                assert r.status_code == 200, (endpoint, r.status_code, r.text)
                result[endpoint] = {"status": r.status_code, "body": r.json()}
        if runtime._managed_font._task:
            await runtime._managed_font._task
        event = Event()
        text = await runtime.handle_event(event)
        assert text
        await runtime.send_help(event, text)
        assert len(event.calls) == 1, ("help send count", len(event.calls), text)
        components = event.calls[0].chain
        images = [
            c for c in components if getattr(c, "file", "").startswith("base64://")
        ]
        assert len(images) == 1, ("help image count", len(images), components)
        png = base64.b64decode(images[0].file[len("base64://") :])
        img = Image.open(io.BytesIO(png))
        img.verify()
        (root / "help.png").write_bytes(png)
        result["help_image"] = {"bytes": len(png), "format": "PNG"}
        result["network_blocked_attempt_count"] = len(attempts)
        result["offline_background_jobs"] = offline_jobs
        result["outbound_policy"] = (
            "DNS,socket,HTTP denied before host imports; metrics disabled"
        )
        result["network_connections_allowed_by_fixture"] = 0
        result["blocked_http_targets"] = attempts
    finally:
        await host.stop()
    (root / "zip-smoke.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False)
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    try:
        runner.run(main())
    finally:
        runner.close()
