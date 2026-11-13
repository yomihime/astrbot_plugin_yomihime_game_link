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
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--host-source",
    required=True,
    type=Path,
    help="Official AstrBot checkout root (v4.29.1 or v4.29.4)",
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
parser.add_argument(
    "--test-signing-key",
    type=Path,
    help="Enable the legacy signed fixture with this test key; omit for ordinary ZIPs",
)
args = parser.parse_args()
dlc_zip = args.dlc_zip.resolve(strict=True)
test_key = args.test_signing_key.resolve(strict=True) if args.test_signing_key else None
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
if not all(path.is_file() for path in (plugin_zip, dlc_zip, font)):
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

from hot_update_host_fixtures import (
    HOST_RELEASE_SCHEMA,
    LEGACY_RELEASE_SCHEMA,
    derive,
    read_release,
)
from packaging.requirements import Requirement
from packaging.version import Version

core_release, core_digest = read_release(plugin_zip)
dlc_release, dlc_digest = read_release(dlc_zip)
expected_schema = LEGACY_RELEASE_SCHEMA if test_key else HOST_RELEASE_SCHEMA
if any(doc["schema"] != expected_schema for doc in (core_release, dlc_release)):
    parser.error(f"Both source ZIPs must use {expected_schema} in this fixture mode")

for path in (plugin_zip, dlc_zip):
    with zipfile.ZipFile(path) as archive:
        requirements_text = archive.read("requirements.txt").decode("utf-8")
    for line in requirements_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.url:
            parser.error(
                "Direct URL dependencies are not allowed in this offline smoke"
            )
        if requirement.marker and not requirement.marker.evaluate():
            continue
        try:
            installed_version = importlib.metadata.version(requirement.name)
        except importlib.metadata.PackageNotFoundError:
            parser.error(
                f"Preinstall missing dependency before offline smoke: {requirement}"
            )
        if installed_version not in requirement.specifier:
            parser.error(
                f"Preinstall required version before offline smoke: {requirement}"
            )
root.mkdir(parents=True, exist_ok=False)
root = root.resolve()
temp = root / "system-temp"
temp.mkdir()
tempfile.tempdir = str(temp)
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
DLC_NAME = "astrbot_plugin_yomihime_game_link_steam_dlc"
UNKNOWN_ID = "never_predeclared_native"
UNKNOWN_NAME = "astrbot_plugin_hot_update_host_" + UNKNOWN_ID


async def main():
    from astrbot.core.utils.version_comparator import VersionComparator

    key = public = None
    if test_key is not None:
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
            load_pem_private_key,
        )

        key = load_pem_private_key(test_key.read_bytes(), password=None)
        public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    def next_patch(release, step):
        version = Version(release["release"])
        return f"{version.major}.{version.minor}.{version.micro + step}"

    fixtures = root / "fixtures"
    fixtures.mkdir()
    core_version = next_patch(core_release, 1)
    dlc_version = next_patch(dlc_release, 1)
    market_version = next_patch(dlc_release, 2)
    core_sequence = core_release.get("release_sequence", 0)
    dlc_sequence = dlc_release.get("release_sequence", 0)
    core_next = derive(
        plugin_zip,
        fixtures / f"core-{core_version}.zip",
        key,
        version=core_version,
        sequence=core_sequence + 1,
    )
    dlc_next = derive(
        dlc_zip,
        fixtures / f"dlc-{dlc_version}.zip",
        key,
        version=dlc_version,
        sequence=dlc_sequence + 1,
    )
    dlc_market = derive(
        dlc_zip,
        fixtures / f"dlc-{market_version}.zip",
        key,
        version=market_version,
        sequence=dlc_sequence + 2,
    )
    unknown = derive(
        dlc_zip,
        fixtures / "unknown.zip",
        key,
        unknown=UNKNOWN_ID,
        repo_root=repo_root,
    )
    fixture_paths = [plugin_zip, dlc_zip, core_next, dlc_next, dlc_market, unknown]
    if key is None:
        dlc_same_version = derive(dlc_market, fixtures / "dlc-same-version.zip")
        core_same_version = derive(core_next, fixtures / "core-same-version.zip")
        fixture_paths.extend((dlc_same_version, core_same_version))
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
    result = {
        "schema": "hot-update-native-host/1",
        "host_commit": host_commit,
        "python": sys.version,
        "release_schema": expected_schema,
        "status": "incomplete",
        "host_process_pid": os.getpid(),
        "assertions": [],
        "artifact_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in fixture_paths
        },
        "fixture_boundary": "Official Host lifecycle, routes, install and updater in process. Market ZIP HTTP transport and unrelated network background jobs are isolated. Derived update ZIPs are synthetic.",
    }

    def record(name, **facts):
        row = {"assertion": name, "status": "passed", **facts}
        result["assertions"].append(row)
        print("NATIVE_ASSERTION " + json.dumps(row), flush=True)
        (root / "native-progress.json").write_text(json.dumps(result, indent=2))

    def star(name):
        return host.star_context.get_registered_star(name).star_cls

    async def ready_core(version, digest):
        owner = star(NAME)
        await asyncio.wait_for(owner._start_task, 30)
        assert owner._runtime.ready and owner._state == "active"
        assert owner._running_version == owner._installed_version == version
        assert owner._running_digest == owner._installed_digest == digest
        return owner

    async def eventually(predicate, label, timeout=30):
        try:
            async with asyncio.timeout(timeout):
                while not predicate():
                    await asyncio.sleep(0.05)
        except TimeoutError as exc:
            raise AssertionError(f"Timed out waiting for {label}") from exc

    def fenced(package):
        try:
            package.gate.check()
        except Exception:
            return True
        return False

    # Preexisting native configuration and plugin data in the disposable root.
    config_path = root / "data/config" / f"{NAME}_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    original_config = b'{"web_public_origin":"https://fixture.invalid"}\n'
    config_path.write_bytes(original_config)
    sentinels = {config_path: original_config}
    for name in (NAME, DLC_NAME):
        marker = root / "data/plugin_data" / name / "native-smoke-data.bin"
        marker.parent.mkdir(parents=True, exist_ok=True)
        contents = f"preexisting plugin data for {name}\n".encode()
        marker.write_bytes(contents)
        sentinels[marker] = contents

    def verify_data():
        assert all(path.read_bytes() == data for path, data in sentinels.items())

    try:
        await host.initialize()
        dashboard = AstrBotDashboard(host, db_helper, asyncio.Event())
        token = dashboard.asgi_app.state.services.auth.generate_jwt(
            "isolated-native-admin"
        )
        headers = {"Authorization": "Bearer " + token, "Origin": "https://test.invalid"}
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

            async def ready_dlc(plugin_name, package_id, path, manager):
                instance = star(plugin_name)
                expected_release, digest = read_release(path)
                await eventually(
                    lambda: (
                        instance.connection_state == "connected"
                        and instance._core_instance is manager.generation.owner
                    ),
                    f"{package_id} connect ({expected_release['release']})",
                )
                package = manager.packages[package_id]
                assert package.active and package.published
                assert (
                    package.bundle.release_doc["release"] == expected_release["release"]
                )
                assert package.receipt.artifact_digest == digest
                if key is None:
                    assert await manager.repository.grant(package_id) is None
                    assert await manager.repository.approval(digest) is None
                return instance, package

            async def check_ordinary_status(epoch, package_ids):
                status = await admin("status", {"core_epoch": epoch})
                assert status["publishers"] == []
                core = status["core"]
                assert core["ready"] and core["phase"] == "active"
                assert core["installed_digest"] == core["running_digest"]
                assert {
                    item["package_id"] for item in status["packages"]
                } == package_ids
                for item in status["packages"]:
                    assert item["signature_state"] == "host_install"
                    assert item["state"] == "active" and item["reason_code"] is None
                    assert item["installed_digest"] == item["running_digest"]
                    assert item["installed_version"] == item["running_version"]
                    assert item["grant_revision"] == item["artifact_revision"] == 0
                    assert item["publisher_revision"] == 0

            with dlc_zip.open("rb") as stream:
                denied = await client.post(
                    "/api/v1/plugins/install/upload",
                    files={"file": (dlc_zip.name, stream, "application/zip")},
                    data={"ignore_version_check": "false"},
                )
            assert denied.status_code in (401, 403)
            assert host.star_context.get_registered_star(DLC_NAME) is None
            record(
                "unauthenticated_native_upload_denied", http_status=denied.status_code
            )

            # DLC first: no Core installation exists yet.
            await upload(dlc_zip)
            pending_dlc = star(DLC_NAME)
            await eventually(
                lambda: pending_dlc.connection_state == "waiting_core",
                "DLC waiting Core",
            )
            record(
                "native_dashboard_upload_dlc_before_core_waits",
                connection_state=pending_dlc.connection_state,
            )
            await upload(plugin_zip)
            owner = await ready_core(core_release["release"], core_digest)
            verify_data()
            assert owner._host_config["web_public_origin"] == "https://fixture.invalid"
            original_digest = owner._running_digest
            original_namespace = owner._generation.bundle.namespace
            assert "yomihime_game_link_sdk" not in sys.modules
            record(
                "native_dashboard_upload_core_private_generation",
                version=owner._running_version,
                digest=original_digest,
                namespace=original_namespace,
            )
            epoch = owner._generation.receipt["core_epoch"]
            if key is not None:
                assert (await bootstrap())["core_epoch"] == epoch
                await admin(
                    "publisher-enroll",
                    {
                        "core_epoch": epoch,
                        "public_key_base64": base64.b64encode(public).decode(),
                        "channels": [dlc_release["channel"]],
                        "expected_revision": 0,
                    },
                )
                await approve("steam_dlc")
            manager = owner._runtime._independent_packages
            dlc, original_package = await ready_dlc(
                DLC_NAME, "steam_dlc", dlc_zip, manager
            )
            assert dlc is pending_dlc
            old_lease = dlc._lease
            denied = await client.post(
                f"/api/v1/plugins/extensions/{NAME}/admin/module-hot-update-status",
                json={"core_epoch": epoch},
            )
            assert denied.status_code in (401, 403)
            record(
                "unauthenticated_hot_update_status_denied",
                http_status=denied.status_code,
            )
            if key is None:
                await check_ordinary_status(epoch, {"steam_dlc"})
            verify_data()
            record(
                "ordinary_dlc_native_connect_without_publisher_or_approval"
                if key is None
                else "authenticated_legacy_package_approval_and_native_reconnect",
                package="steam_dlc",
                digest=dlc_digest,
                core_epoch=epoch,
                package_epoch=dlc._package_epoch,
            )
            await upload(unknown)
            if key is not None:
                await approve(UNKNOWN_ID)
            _, sibling = await ready_dlc(UNKNOWN_NAME, UNKNOWN_ID, unknown, manager)
            sibling_epoch = sibling.epoch
            assert owner._running_digest == original_digest
            record(
                "native_unknown_dlc_admitted_without_core_change",
                package=UNKNOWN_ID,
                core_digest=original_digest,
            )

            async def sibling_health():
                instance = next(iter(sibling.instances.values()))
                report = await instance.check_health()
                assert report.capabilities

            await sibling_health()
            await upload(dlc_next)
            await sibling_health()
            replacement, new_package = await ready_dlc(
                DLC_NAME, "steam_dlc", dlc_next, manager
            )
            verify_data()
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
            metadata = host.star_context.get_registered_star(DLC_NAME)
            archive_url = f"https://fixture.invalid/native-dlc-{market_version}.zip"
            market = {
                "native-fixture/steam": {
                    "market_plugin_id": "native-fixture/steam",
                    "name": DLC_NAME,
                    "repo": metadata.repo,
                    "version": market_version,
                    "download_url": archive_url,
                }
            }
            source = service.build_registry_source(None)
            service.save_plugin_cache(source.cache_file, market, "")
            await call(
                "POST",
                f"/api/v1/plugins/{DLC_NAME}/source",
                json={
                    "install_method": "market",
                    "market_plugin_id": "native-fixture/steam",
                },
            )
            info = await service.resolve_market_update_info(DLC_NAME)
            assert info["download_url"] == archive_url
            assert (
                VersionComparator.compare_version(market_version, metadata.version) == 1
            )
            downloads = []

            def transport(request):
                assert str(request.url) == archive_url, str(request.url)
                downloads.append(str(request.url))
                return httpx.Response(200, content=dlc_market.read_bytes())

            host.plugin_manager._updater._create_httpx_client = lambda **kw: (
                httpx.AsyncClient(
                    transport=httpx.MockTransport(transport), follow_redirects=True
                )
            )
            previous = new_package
            await call("POST", f"/api/v1/plugins/{DLC_NAME}/update", json={})
            replacement, new_package = await ready_dlc(
                DLC_NAME, "steam_dlc", dlc_market, manager
            )
            verify_data()
            assert previous is not new_package and fenced(previous)
            assert sibling.active and star(NAME) is owner
            assert downloads == [archive_url]
            record(
                "native_market_source_version_comparator_download_url_update_and_reload",
                downloads=downloads,
                running_version=market_version,
                sibling_active=True,
            )
            installed_dlc = dlc_market
            if key is None:
                previous, old_instance = new_package, replacement
                await upload(dlc_same_version)
                replacement, new_package = await ready_dlc(
                    DLC_NAME, "steam_dlc", dlc_same_version, manager
                )
                verify_data()
                assert replacement is not old_instance and old_instance._closed
                assert fenced(previous)
                assert sibling.active and star(NAME) is owner
                next_digest = new_package.receipt.artifact_digest
                assert next_digest != previous.receipt.artifact_digest
                installed_dlc = dlc_same_version
                record(
                    "same_version_native_dlc_upload_activates_changed_release_bytes",
                    running_version=market_version,
                    running_digest=next_digest,
                )
            # Ten successive native uploads, plus native reload, retaining
            # old package objects only to test that their gates/importers close.
            repeated = []
            installed_unknown = unknown
            for index in range(1, 11):
                previous_unknown = manager.packages[UNKNOWN_ID]
                old_namespace = previous_unknown.bundle.namespace
                candidate = derive(
                    unknown,
                    fixtures / f"unknown-1.0.{index}.zip",
                    key,
                    version=f"1.0.{index}",
                    sequence=index + 2,
                )
                await upload(candidate)
                installed_unknown = candidate
                await ready_dlc(UNKNOWN_NAME, UNKNOWN_ID, installed_unknown, manager)
                if index % 3 == 0:
                    await host.plugin_manager.reload(UNKNOWN_NAME)
                    await ready_dlc(
                        UNKNOWN_NAME, UNKNOWN_ID, installed_unknown, manager
                    )
                verify_data()
                assert fenced(previous_unknown)
                assert old_namespace not in sys.modules
                assert manager.packages["steam_dlc"].active
                assert star(NAME) is owner
                repeated.append(
                    {
                        "version": f"1.0.{index}",
                        "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                        "old_namespace_released": True,
                    }
                )
            record(
                "ten_native_compatible_upload_updates_with_repeated_reload",
                generations=repeated,
                shared_core_digest=owner._running_digest,
                rss_claim="not measured",
            )
            # Consecutive native Core uploads; both DLC instances stay installed.
            core_updates = [
                (
                    core_next,
                    "native_core_upload_update_reconnects_all_dlc_without_reinstall_or_host_restart",
                )
            ]
            if key is None:
                core_updates.append(
                    (
                        core_same_version,
                        "same_version_native_core_upload_activates_changed_release_bytes_and_reconnects_all_dlc",
                    )
                )
            for candidate, assertion in core_updates:
                old_endpoint = manager.endpoint
                old_packages = list(manager.packages.values())
                old_namespace = owner._generation.bundle.namespace
                instances = (star(DLC_NAME), star(UNKNOWN_NAME))
                _, digest = read_release(candidate)
                assert digest != owner._running_digest
                await upload(candidate)
                new_owner = await ready_core(core_version, digest)
                assert new_owner is not owner
                assert new_owner._generation.bundle.namespace != old_namespace
                assert all(fenced(p) for p in old_packages) and old_endpoint._closed
                assert old_namespace not in sys.modules
                manager = new_owner._runtime._independent_packages
                steam, _ = await ready_dlc(
                    DLC_NAME, "steam_dlc", installed_dlc, manager
                )
                custom, _ = await ready_dlc(
                    UNKNOWN_NAME, UNKNOWN_ID, installed_unknown, manager
                )
                assert steam is instances[0] and custom is instances[1]
                assert set(manager.packages) == {"steam_dlc", UNKNOWN_ID}
                assert (
                    new_owner._host_config["web_public_origin"]
                    == "https://fixture.invalid"
                )
                verify_data()
                next_epoch = new_owner._generation.receipt["core_epoch"]
                assert next_epoch > epoch
                if key is None:
                    await check_ordinary_status(next_epoch, {"steam_dlc", UNKNOWN_ID})
                record(
                    assertion,
                    version=new_owner._running_version,
                    digest=digest,
                    core_epoch=next_epoch,
                    old_namespace_released=True,
                )
                epoch, owner = next_epoch, new_owner
            current = manager.packages["steam_dlc"]
            current_instance = star(DLC_NAME)
            current_lease = current_instance._lease
            await call(
                "PATCH",
                f"/api/v1/plugins/{DLC_NAME}/enabled",
                json={"enabled": False},
            )
            assert current_instance._closed and fenced(current)
            response = await manager.endpoint.request(
                current_instance,
                {"protocol": "ygl.control/1", "op": "status", "lease": current_lease},
            )
            assert response.get("state") != "active"
            assert manager.packages[UNKNOWN_ID].active
            record(
                "native_disable_fences_old_instance_and_exact_lease",
                old_state=response["state"],
                sibling_active=True,
            )
            await call(
                "PATCH",
                f"/api/v1/plugins/{DLC_NAME}/enabled",
                json={"enabled": True},
            )
            enabled, current = await ready_dlc(
                DLC_NAME, "steam_dlc", installed_dlc, manager
            )
            await call(
                "DELETE",
                "/api/v1/plugins/by-id",
                params={"plugin_id": DLC_NAME},
                json={"delete_config": False, "delete_data": False},
            )
            assert fenced(current) and enabled._closed
            assert manager.packages[UNKNOWN_ID].active
            assert host.star_context.get_registered_star(DLC_NAME) is None
            verify_data()
            record(
                "native_install_update_and_preserving_remove_keep_config_and_data_bytes",
                sentinels={
                    str(path.relative_to(root)): hashlib.sha256(data).hexdigest()
                    for path, data in sentinels.items()
                },
            )
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
        try:
            await host.stop()
            result["host_stopped"] = True
        except BaseException:
            result["status"] = "cleanup_failed"
            raise
        finally:
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
