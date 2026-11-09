# ruff: noqa: E402
# Imports are deliberately staged after offline guards and disposable-root checks.
"""Probe offline native upload failure and unsigned source boundaries."""

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
parser.add_argument("--approved-fallback", action="store_true")
parser.add_argument("--only-unsigned", action="store_true")
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
    os.environ["PIP_NO_INDEX"] = "1"
    os.environ["PIP_NO_CACHE_DIR"] = "1"
    distributions_before = {
        d.metadata["Name"]: d.version for d in importlib.metadata.distributions()
    }
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
    malformed = fixtures / "malformed.zip"
    malformed.write_bytes(b"not a ZIP archive")
    with zipfile.ZipFile(plugin_zip) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}

    def broken_zip(name, changes):
        path = fixtures / name
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for member, data in sorted({**files, **changes}.items()):
                archive.writestr(member, data)
        return path

    broken_main = broken_zip(
        "import-broken.zip",
        {"main.py": b'raise RuntimeError("synthetic native import boundary")\n'},
    )
    missing_dep = broken_zip(
        "missing-dependency.zip",
        {
            "requirements.txt": b"ygl-hot-update-host-deliberately-missing-dependency==0.0.1\n"
        },
    )
    unsigned = derive(
        dlc_zip, fixtures / "unsigned-0.3.1.zip", key, version="0.3.1", sequence=2
    )
    with zipfile.ZipFile(unsigned) as archive:
        unsigned_files = {
            name: archive.read(name)
            for name in archive.namelist()
            if name != "release.sig.json"
        }
    with zipfile.ZipFile(unsigned, "w", zipfile.ZIP_DEFLATED) as archive:
        for member, data in unsigned_files.items():
            archive.writestr(member, data)
    astrbot_config["disable_metrics"] = True
    astrbot_config["provider"] = []
    astrbot_config["provider_sources"] = []
    astrbot_config["platform"] = []
    astrbot_config["dashboard"]["jwt_secret"] = (
        "synthetic-native-faults-key-at-least-32-bytes"
    )
    astrbot_config.save_config()
    target = (
        root / "data/plugin_data" / NAME / "resources/fonts/NotoSansCJKsc-Regular.otf"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(font, target)
    host = AstrBotCoreLifecycle(LogBroker(), db_helper)
    await host.initialize()
    dashboard = AstrBotDashboard(host, db_helper, asyncio.Event())
    token = dashboard.asgi_app.state.services.auth.generate_jwt("isolated-native-admin")
    headers = {"Authorization": "Bearer " + token, "Origin": "https://test.invalid"}
    result = {
        "schema": "native-hot-update-faults/1",
        "host_commit": host_commit,
        "python": sys.version,
        "assertions": [],
        "fixture_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (
                plugin_zip,
                dlc_zip,
                malformed,
                broken_main,
                missing_dep,
                unsigned,
            )
        },
    }

    def record(name, **facts):
        row = {"assertion": name, "status": "passed", **facts}
        result["assertions"].append(row)
        print("NATIVE_ASSERTION " + json.dumps(row), flush=True)
        (root / "native-progress.json").write_text(json.dumps(result, indent=2))

    def owner():
        metadata = host.star_context.get_registered_star(NAME)
        return metadata.star_cls if metadata else None

    async def ready():
        current = owner()
        assert current is not None
        await asyncio.wait_for(current._start_task, 30)
        assert current._runtime.ready
        return current

    async def eventually(predicate):
        async with asyncio.timeout(30):
            while not predicate():
                await asyncio.sleep(0.05)

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(dashboard.asgi_app),
            base_url="https://test.invalid",
            follow_redirects=True,
        ) as client:

            async def upload(path, success=True):
                with path.open("rb") as stream:
                    response = await client.post(
                        "/api/v1/plugins/install/upload",
                        headers=headers,
                        files={"file": (path.name, stream, "application/zip")},
                    )
                doc = response.json()
                assert response.status_code == 200, (response.status_code, doc)
                assert (doc.get("status") == "ok") is success, doc
                return doc

            async def admin(action, body=None):
                async with asyncio.timeout(30):
                    while True:
                        tracing = (
                            result.pop("trace_next_approval", False)
                            and action == "package-approve"
                        )
                        if tracing:
                            import threading

                            trace_events = []

                            def trace(frame, event, arg):
                                if (
                                    event == "exception"
                                    and getattr(arg[0], "__name__", "")
                                    == "AdminAuthorizationDenied"
                                    and len(trace_events) < 100
                                ):
                                    trace_events.append(
                                        {
                                            "file": Path(frame.f_code.co_filename).name,
                                            "line": frame.f_lineno,
                                            "function": frame.f_code.co_name,
                                        }
                                    )
                                return trace

                            threading.settrace_all_threads(trace)
                        response = await client.post(
                            "/api/v1/plugins/extensions/"
                            + NAME
                            + "/admin/module-hot-update-"
                            + action,
                            headers=headers,
                            json={} if body is None else body,
                        )
                        native_denied = (
                            response.status_code == 403
                            or response.json()
                            .get("data", {})
                            .get("error", {})
                            .get("code")
                            == "admin_authorization_denied"
                        )
                        if tracing:
                            threading.settrace_all_threads(None)
                            result["approval_trace"] = trace_events
                            if native_denied:
                                manager = owner()._runtime._independent_packages
                                grant = await manager.repository.grant("steam_dlc")
                                result["approval_403_readback"] = {
                                    "http_status": response.status_code,
                                    "grant": {
                                        k: grant[k]
                                        for k in (
                                            "publisher_key_id",
                                            "revision",
                                            "revoked",
                                        )
                                    },
                                    "module_source_present": owner()._runtime._module_admin_source
                                    is not None,
                                    "management_current": owner()._runtime.management_current(
                                        owner()._runtime._core,
                                        owner()._runtime._module_admin_source,
                                    ),
                                    "trace": trace_events,
                                    "mutation_retransmitted": False,
                                }
                                print(
                                    "NATIVE_DIAGNOSTIC "
                                    + json.dumps(result["approval_403_readback"]),
                                    flush=True,
                                )
                        if native_denied and action in {
                            "bootstrap",
                            "inspect",
                            "status",
                        }:
                            result.setdefault("read_only_proof_refreshes", []).append(
                                {
                                    "action": action,
                                    "http_status": response.status_code,
                                    "reason": "admin_authorization_denied",
                                }
                            )
                            await asyncio.sleep(0.1)
                            continue
                        assert response.status_code == 200, response.text
                        doc = response.json()["data"]
                        assert doc["ok"], doc
                        return doc

            await upload(plugin_zip)
            initial = await ready()
            for path in (
                () if args.only_unsigned else (malformed, broken_main, missing_dep)
            ):
                before = owner()
                old_generation = before._generation
                before_digest = before._running_digest
                reply = await upload(path, success=False)
                after = await ready()
                assert after._running_digest == before_digest
                assert after._state == "active"
                installed_main = after._plugin_root / "main.py"
                assert (
                    hashlib.sha256(installed_main.read_bytes()).hexdigest()
                    == hashlib.sha256(files["main.py"]).hexdigest()
                )
                if after is not before:
                    assert old_generation.kernel is after._generation.kernel
                    assert before._stopping
                record(
                    "native_upload_reject_" + path.stem,
                    reply=reply,
                    same_instance=after is before,
                    restored_ready=True,
                    running_version=after._running_version,
                    source_main_restored=True,
                )
            assert initial._running_version == "0.2.0"
            distributions_after = {
                d.metadata["Name"]: d.version
                for d in importlib.metadata.distributions()
            }
            assert distributions_before == distributions_after
            record(
                "native_unsigned_probe_environment_unchanged"
                if args.only_unsigned
                else "native_missing_dependency_failure_did_not_mutate_installed_distributions",
                installed_distribution_count=len(distributions_after),
                pip_index_disabled=True,
            )
            # Bind an actual market source before unsigned overwrite, to prove
            # persisted installer provenance cannot grant execution authority.
            await upload(dlc_zip)
            await ready()
            epoch = (await admin("bootstrap"))["core_epoch"]
            await admin(
                "publisher-enroll",
                {
                    "core_epoch": epoch,
                    "public_key_base64": base64.b64encode(public).decode(),
                    "channels": ["preview"],
                    "expected_revision": 0,
                },
            )
            if args.approved_fallback:
                initial_plan = await admin(
                    "inspect", {"core_epoch": epoch, "package_id": "steam_dlc"}
                )
                await admin(
                    "package-approve",
                    {
                        "core_epoch": epoch,
                        "package_id": "steam_dlc",
                        "plan_id": initial_plan["plan_id"],
                        "expected_revision": initial_plan["grant_revision"],
                    },
                )
                await admin("retry", {"core_epoch": epoch, "package_id": "steam_dlc"})
                initial_dlc = host.star_context.get_registered_star(
                    "astrbot_plugin_yomihime_game_link_steam_dlc"
                ).star_cls
                await eventually(lambda: initial_dlc.connection_state == "connected")
            # Source provenance exists before any business execution grant.
            # The separate sequence already tests automatic signed grant reuse.
            dlc_name = "astrbot_plugin_yomihime_game_link_steam_dlc"
            service = dashboard.asgi_app.state.services.plugins
            metadata = host.star_context.get_registered_star(dlc_name)
            market = {
                "fixture/steam": {
                    "market_plugin_id": "fixture/steam",
                    "name": dlc_name,
                    "repo": metadata.repo,
                    "version": "0.3.1",
                    "download_url": "https://fixture.invalid/unsigned.zip",
                }
            }
            source = service.build_registry_source(None)
            service.save_plugin_cache(source.cache_file, market, "")
            response = await client.post(
                "/api/v1/plugins/" + dlc_name + "/source",
                headers=headers,
                json={"install_method": "market", "market_plugin_id": "fixture/steam"},
            )
            assert response.json()["status"] == "ok", response.text
            old_records = await service.get_plugin_install_sources()
            assert old_records[metadata.root_dir_name]["install_method"] == "market"
            await upload(unsigned)
            plan = await admin(
                "inspect", {"core_epoch": epoch, "package_id": "steam_dlc"}
            )
            assert plan["signature_state"] == "unsigned"
            assert plan["reason_code"] == "approval_required", plan
            assert plan["running_digest"] != plan["installed_digest"]
            unsigned_digest = plan["installed_digest"]
            record(
                "unsigned_native_upload_cannot_inherit_old_market_provenance",
                signature_state=plan["signature_state"],
                reason_code=plan["reason_code"],
                running_version=plan["running_version"],
                installed_version=plan["installed_version"],
                exact_approval_missing=True,
            )
            plan = await admin(
                "inspect", {"core_epoch": epoch, "package_id": "steam_dlc"}
            )
            if args.approved_fallback:
                manager = owner()._runtime._independent_packages
                await eventually(
                    lambda: (p := manager.packages.get("steam_dlc")) is not None
                    and p.active
                    and p.fallback
                    and p.published
                )
                stable = []
                for _ in range(2):
                    plan = await admin(
                        "inspect", {"core_epoch": epoch, "package_id": "steam_dlc"}
                    )
                    assert (
                        plan["running_version"] == "0.3.0"
                        and plan["state"] == "fallback"
                    ), plan
                    stable.append(
                        {
                            k: plan[k]
                            for k in (
                                "state",
                                "running_version",
                                "running_digest",
                                "grant_revision",
                                "artifact_revision",
                            )
                        }
                    )
                assert stable[0] == stable[1]
                record(
                    "approved_signed_last_good_fallback_stable_before_fresh_unsigned_approval",
                    snapshots=stable,
                )
                result["trace_next_approval"] = True
            await admin(
                "package-approve",
                {
                    "core_epoch": epoch,
                    "package_id": "steam_dlc",
                    "plan_id": plan["plan_id"],
                    "expected_revision": plan["grant_revision"],
                },
            )
            plan = await admin(
                "inspect", {"core_epoch": epoch, "package_id": "steam_dlc"}
            )
            await admin(
                "artifact-approve",
                {
                    "core_epoch": epoch,
                    "package_id": "steam_dlc",
                    "plan_id": plan["plan_id"],
                    "expected_revision": plan["artifact_revision"],
                },
            )
            await admin("retry", {"core_epoch": epoch, "package_id": "steam_dlc"})
            replacement = host.star_context.get_registered_star(dlc_name).star_cls
            manager = owner()._runtime._independent_packages
            await eventually(
                lambda: replacement.connection_state == "connected"
                and (package := manager.packages.get("steam_dlc")) is not None
                and package.active
                and package.receipt.artifact_digest == unsigned_digest
            )
            plan = await admin(
                "inspect", {"core_epoch": epoch, "package_id": "steam_dlc"}
            )
            assert (
                plan["running_digest"] == unsigned_digest
                and plan["running_version"] == "0.3.1"
            )
            record(
                "unsigned_exact_artifact_approval_required_and_sufficient",
                running_version=plan["running_version"],
                running_digest=unsigned_digest,
            )
            result["status"] = "passed"
    finally:
        await host.stop()
        result["blocked_attempts"] = attempts
        result["network_connections_allowed_by_fixture"] = 0
        (root / "native-faults.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        runner.run(main())
    finally:
        runner.close()
