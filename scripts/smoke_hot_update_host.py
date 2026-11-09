# ruff: noqa: E402
# Imports are deliberately staged after offline guards and disposable-root checks.
"""Install an actual release ZIP in isolated official AstrBot and probe it."""

import argparse
import asyncio
import base64
import hashlib
import importlib
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
parser.add_argument("--dlc-zip", required=True, type=Path)
parser.add_argument("--test-signing-key", required=True, type=Path)
args = parser.parse_args()
dlc_zip = args.dlc_zip.resolve(strict=True)
test_key = args.test_signing_key.resolve(strict=True)
repo_root = Path(__file__).resolve().parents[1]

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
if host_commit not in {
    "dffe922773725e9c178392c423676ad65044748c",
    "61335414433db721b3f7ecc93f90deafe0a59d78",
}:
    parser.error("Expected allowlisted official v4.29.1 or v4.29.4 commit")
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
    from astrbot.core.utils.version_comparator import VersionComparator
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
        load_pem_private_key,
    )
    from hot_update_host_fixtures import derive

    key = load_pem_private_key(test_key.read_bytes(), password=None)
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    fixtures = root / "fixtures"
    fixtures.mkdir()
    core_next = derive(plugin_zip, fixtures / "core-0.2.1.zip", key, version="0.2.1")
    dlc_next = derive(dlc_zip, fixtures / "dlc-0.3.1.zip", key, version="0.3.1")
    dlc_market = derive(
        dlc_zip, fixtures / "dlc-0.3.2.zip", key, version="0.3.2", sequence=3
    )
    unknown = derive(
        dlc_zip,
        fixtures / "unknown.zip",
        key,
        unknown="never_predeclared_native",
        repo_root=repo_root,
    )
    astrbot_config["disable_metrics"] = True
    astrbot_config["provider"] = []
    astrbot_config["provider_sources"] = []
    astrbot_config["platform"] = []
    astrbot_config["dashboard"]["jwt_secret"] = (
        "synthetic-native-hot-update-key-at-least-32-bytes"
    )
    astrbot_config.save_config()
    target = (
        root / "data/plugin_data" / NAME / "resources/fonts/NotoSansCJKsc-Regular.otf"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(font, target)
    host = AstrBotCoreLifecycle(LogBroker(), db_helper)
    await host.initialize()
    result = {
        "schema": "hot-update-native-host/1",
        "host_commit": host_commit,
        "python": sys.version,
        "assertions": [],
        "artifact_sha256": {
            str(p.name): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (plugin_zip, dlc_zip, core_next, dlc_next, dlc_market, unknown)
        },
        "fixture_boundary": "Unmodified native Host lifecycle/routes/install/updater/extraction/permissions; external ZIP HTTP transport only is deterministic; signed derivatives are synthetic test-only.",
    }

    def record(name, **facts):
        row = {"assertion": name, "status": "passed", **facts}
        result["assertions"].append(row)
        print("NATIVE_ASSERTION " + json.dumps(row), flush=True)
        (root / "native-progress.json").write_text(json.dumps(result, indent=2))

    def star(name):
        return host.star_context.get_registered_star(name).star_cls

    async def ready_core():
        owner = star(NAME)
        await asyncio.wait_for(owner._start_task, 30)
        assert owner._runtime.ready and owner._state == "active"
        return owner

    async def eventually(predicate, label, timeout=30):
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(0.05)
        return True

    def fenced(package):
        try:
            package.gate.check()
        except Exception:
            return True
        return False

    dashboard = AstrBotDashboard(host, db_helper, asyncio.Event())
    token = dashboard.asgi_app.state.services.auth.generate_jwt("isolated-native-admin")
    headers = {"Authorization": "Bearer " + token, "Origin": "https://test.invalid"}
    dlc_name = "astrbot_plugin_yomihime_game_link_steam_dlc"
    unknown_name = "astrbot_plugin_hot_update_host_never_predeclared_native"
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(dashboard.asgi_app),
            base_url="https://test.invalid",
            follow_redirects=True,
        ) as client:

            async def call(method, path, **kwargs):
                response = await client.request(method, path, headers=headers, **kwargs)
                assert response.status_code == 200, (
                    path,
                    response.status_code,
                    response.text,
                )
                doc = response.json()
                assert doc.get("status") in ("ok", "success"), (path, doc)
                return doc.get("data")

            async def upload(path):
                with path.open("rb") as stream:
                    return await call(
                        "POST",
                        "/api/v1/plugins/install/upload",
                        files={"file": (path.name, stream, "application/zip")},
                        data={"ignore_version_check": "false"},
                    )

            async def admin(action, data=None):
                base = (
                    "/api/v1/plugins/extensions/" + NAME + "/admin/module-hot-update-"
                )
                body = {} if data is None else data
                doc = await call("POST", base + action, json=body)
                assert doc.get("ok") is True, (action, doc)
                return doc

            async def bootstrap():
                return await admin("bootstrap")

            async def approve(package_id):
                epoch = (await bootstrap())["core_epoch"]
                plan = await admin(
                    "inspect", {"core_epoch": epoch, "package_id": package_id}
                )
                await admin(
                    "package-approve",
                    {
                        "core_epoch": epoch,
                        "package_id": package_id,
                        "plan_id": plan["plan_id"],
                        "expected_revision": plan["grant_revision"],
                    },
                )
                await admin("retry", {"core_epoch": epoch, "package_id": package_id})

            # DLC first: no Core installation exists yet.
            await upload(dlc_zip)
            dlc = star(dlc_name)
            await eventually(
                lambda: dlc.connection_state == "waiting_core", "DLC waiting Core"
            )
            record(
                "native_dashboard_upload_dlc_before_core_waits",
                connection_state=dlc.connection_state,
            )
            await upload(plugin_zip)
            owner = await ready_core()
            original_digest = owner._running_digest
            original_namespace = owner._generation.bundle.namespace
            assert "yomihime_game_link_sdk" not in sys.modules
            record(
                "native_dashboard_upload_core_private_generation",
                version=owner._running_version,
                namespace=original_namespace,
            )
            epoch = (await bootstrap())["core_epoch"]
            denied = await client.post(
                "/api/v1/plugins/extensions/"
                + NAME
                + "/admin/module-hot-update-publisher-enroll",
                json={
                    "core_epoch": epoch,
                    "public_key_base64": base64.b64encode(public).decode(),
                    "channels": ["preview"],
                    "expected_revision": 0,
                },
            )
            assert denied.status_code in (401, 403)
            await admin(
                "publisher-enroll",
                {
                    "core_epoch": epoch,
                    "public_key_base64": base64.b64encode(public).decode(),
                    "channels": ["preview"],
                    "expected_revision": 0,
                },
            )
            await approve("steam_dlc")
            await eventually(
                lambda: dlc.connection_state == "connected", "signed DLC connect"
            )
            manager = owner._runtime._independent_packages
            original_package = manager.packages["steam_dlc"]
            old_lease = dlc._lease
            record(
                "authenticated_publisher_enrollment_exact_package_approval_and_native_reconnect",
                package="steam_dlc",
                core_epoch=epoch,
                package_epoch=dlc._package_epoch,
                unauthorized_status=denied.status_code,
            )
            await upload(unknown)
            await approve("never_predeclared_native")
            other = star(unknown_name)
            await eventually(
                lambda: other.connection_state == "connected", "unknown connect"
            )
            sibling = manager.packages["never_predeclared_native"]
            sibling_epoch = sibling.epoch
            assert owner._running_digest == original_digest
            record(
                "native_unknown_dlc_admitted_without_core_change",
                package="never_predeclared_native",
                core_digest=original_digest,
            )

            async def sibling_health():
                instance = next(iter(sibling.instances.values()))
                report = await instance.check_health()
                assert report.capabilities

            await sibling_health()
            await upload(dlc_next)
            await sibling_health()
            replacement = star(dlc_name)
            await eventually(
                lambda: replacement.connection_state == "connected",
                "DLC overwrite connect",
            )
            new_package = manager.packages["steam_dlc"]
            assert replacement is not dlc and new_package is not original_package
            assert dlc._closed and fenced(original_package)
            assert sibling.active and sibling.epoch == sibling_epoch
            assert star(NAME) is owner and owner._running_digest == original_digest
            old_status = await manager.endpoint.request(
                dlc, {"protocol": "ygl.control/1", "op": "status", "lease": old_lease}
            )
            assert old_status.get("state") != "active"
            record(
                "native_dashboard_upload_overwrite_dlc_fences_exact_old_lease_and_preserves_sibling",
                installed_version=new_package.bundle.release_doc["release"],
                old_state=old_status["state"],
                sibling_epoch=sibling_epoch,
            )
            # Use actual registry cache parsing/source binding. Only remote ZIP bytes
            # are supplied by a deterministic HTTPX transport; native updater runs.
            service = dashboard.asgi_app.state.services.plugins
            metadata = host.star_context.get_registered_star(dlc_name)
            archive_url = "https://fixture.invalid/native-dlc-0.3.2.zip"
            market = {
                "native-fixture/steam": {
                    "market_plugin_id": "native-fixture/steam",
                    "name": dlc_name,
                    "repo": metadata.repo,
                    "version": "0.3.2",
                    "download_url": archive_url,
                }
            }
            source = service.build_registry_source(None)
            service.save_plugin_cache(source.cache_file, market, "")
            await call(
                "POST",
                "/api/v1/plugins/" + dlc_name + "/source",
                json={
                    "install_method": "market",
                    "market_plugin_id": "native-fixture/steam",
                },
            )
            info = await service.resolve_market_update_info(dlc_name)
            assert info["download_url"] == archive_url
            assert VersionComparator.compare_version("0.3.2", metadata.version) == 1
            downloads = []

            def transport(request):
                assert str(request.url) == archive_url, str(request.url)
                downloads.append(str(request.url))
                return httpx.Response(200, content=dlc_market.read_bytes())

            host.plugin_manager._updater._create_httpx_client = (
                lambda **kw: httpx.AsyncClient(
                    transport=httpx.MockTransport(transport), follow_redirects=True
                )
            )
            previous = new_package
            await call("POST", "/api/v1/plugins/" + dlc_name + "/update", json={})
            replacement = star(dlc_name)
            await eventually(
                lambda: replacement.connection_state == "connected", "market reconnect"
            )
            assert (
                manager.packages["steam_dlc"].bundle.release_doc["release"] == "0.3.2"
            )
            assert fenced(previous) and sibling.active and star(NAME) is owner
            assert downloads == [archive_url]
            record(
                "native_market_source_version_comparator_download_url_update_and_reload",
                downloads=downloads,
                running_version="0.3.2",
                sibling_active=True,
            )
            # Ten successive native uploads, plus native reload, retaining
            # old package objects only to test that their gates/importers close.
            repeated = []
            for index in range(1, 11):
                previous_unknown = manager.packages["never_predeclared_native"]
                old_namespace = previous_unknown.bundle.namespace
                candidate = derive(
                    unknown,
                    fixtures / f"unknown-1.0.{index}.zip",
                    key,
                    version=f"1.0.{index}",
                    sequence=index + 2,
                )
                await upload(candidate)
                current_unknown = star(unknown_name)
                await eventually(
                    lambda: current_unknown.connection_state == "connected",
                    "repeat native update",
                )
                if index % 3 == 0:
                    await host.plugin_manager.reload(unknown_name)
                    current_unknown = star(unknown_name)
                    await eventually(
                        lambda: current_unknown.connection_state == "connected",
                        "repeat native reload",
                    )
                assert fenced(previous_unknown)
                assert old_namespace not in sys.modules
                assert manager.packages["steam_dlc"].active
                assert star(NAME) is owner
                repeated.append(
                    {"version": f"1.0.{index}", "old_namespace_released": True}
                )
            other = star(unknown_name)
            record(
                "ten_native_compatible_upload_updates_with_repeated_reload",
                generations=repeated,
                shared_core_digest=owner._running_digest,
                rss_claim="not measured",
            )
            # Core overwrite through native dashboard; DLC installations stay put.
            old_endpoint = manager.endpoint
            old_packages = list(manager.packages.values())
            instances = (star(dlc_name), other)
            await upload(core_next)
            new_owner = await ready_core()
            assert new_owner is not owner
            await eventually(
                lambda: all(
                    p.connection_state == "connected" and p._core_instance is new_owner
                    for p in instances
                ),
                "Core reconnect all DLC",
            )
            assert new_owner._running_version == "0.2.1"
            assert new_owner._generation.receipt["core_epoch"] > epoch
            assert new_owner._generation.bundle.namespace != original_namespace
            assert all(fenced(p) for p in old_packages) and old_endpoint._closed
            assert original_namespace not in sys.modules
            manager = new_owner._runtime._independent_packages
            assert set(manager.packages) == {"steam_dlc", "never_predeclared_native"}
            record(
                "native_core_upload_update_reconnects_all_dlc_without_reinstall_or_host_restart",
                version=new_owner._running_version,
                core_epoch=new_owner._generation.receipt["core_epoch"],
                old_namespace_released=True,
            )
            current = manager.packages["steam_dlc"]
            current_instance = star(dlc_name)
            current_lease = current_instance._lease
            await call(
                "PATCH",
                "/api/v1/plugins/" + dlc_name + "/enabled",
                json={"enabled": False},
            )
            assert current_instance._closed and fenced(current)
            response = await manager.endpoint.request(
                current_instance,
                {"protocol": "ygl.control/1", "op": "status", "lease": current_lease},
            )
            assert response.get("state") != "active"
            assert manager.packages["never_predeclared_native"].active
            record(
                "native_disable_fences_old_instance_and_exact_lease",
                old_state=response["state"],
                sibling_active=True,
            )
            await call(
                "PATCH",
                "/api/v1/plugins/" + dlc_name + "/enabled",
                json={"enabled": True},
            )
            enabled = star(dlc_name)
            await eventually(
                lambda: enabled.connection_state == "connected", "enable reconnect"
            )
            current = manager.packages["steam_dlc"]
            await call(
                "DELETE",
                "/api/v1/plugins/by-id",
                params={"plugin_id": dlc_name},
                json={"delete_config": False, "delete_data": False},
            )
            assert fenced(current) and enabled._closed
            assert manager.packages["never_predeclared_native"].active
            assert host.star_context.get_registered_star(dlc_name) is None
            record("native_uninstall_fences_removed_dlc_and_preserves_sibling")
            # Removal invalidates any in-flight resource-scoped management
            # proof. A fresh read must succeed after owned cleanup settles.
            final_read_statuses = []
            async with asyncio.timeout(30):
                while True:
                    response = await client.post(
                        "/api/v1/plugins/extensions/"
                        + NAME
                        + "/admin/module-hot-update-bootstrap",
                        headers=headers,
                        json={},
                    )
                    final_read_statuses.append(response.status_code)
                    if response.status_code == 200:
                        result["final_status"] = response.json()["data"]
                        assert result["final_status"]["ok"] is True
                        break
                    assert response.status_code == 403, response.text
                    await asyncio.sleep(0.1)
            record(
                "fresh_authenticated_management_read_after_cleanup",
                statuses=final_read_statuses,
            )
            result["status"] = "passed"
    finally:
        await host.stop()
        result["network_connections_allowed_by_fixture"] = 0
        result["blocked_attempts"] = attempts
        result["offline_background_jobs"] = offline_jobs
        (root / "native-hot-update.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        runner.run(main())
    finally:
        runner.close()
