"""Actual private Core composition across process death; not native Host."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ygl_test_subject.bootstrap.loader import ArtifactCache, CoreGeneration
from ygl_test_subject.extensions.runtime_bundle import RuntimeBundleStore

from tests.extensions.test_private_runtime_bundle import _release, _write
from tests.host import test_private_core_integration as fixture

PHASES = (
    "PREPARED",
    "OLD_FENCED",
    "OLD_QUIET",
    "COMMITTED_CLOSED",
    "PUBLISHED",
    "ACTIVE",
)


class _Runtime:
    make_dlc = fixture.PrivateCoreIntegrationTest.make_dlc

    async def start(self, root):
        self.root, self.registry = root, []
        self.context = SimpleNamespace(
            get_all_stars=lambda: self.registry,
            get_registered_star=lambda name: next(
                (m for m in self.registry if m.name == name), None
            ),
        )
        self.data = root / "data"
        self.data.mkdir(exist_ok=True)
        self.installation = root / "core"
        files = fixture.runtime_files()
        _write(self.installation, files)
        self.store = RuntimeBundleStore()
        self.bundle = self.store.capture(
            self.installation, _release(files, package_id="ygl_core"), kind="core"
        )
        self.generation = CoreGeneration(object(), self.context, self.data, self.bundle)
        assert (await self.generation.acquire())["state"] == "acquired"
        self.namespace = self.bundle.mount_core()
        module = importlib.import_module(self.namespace + ".adapters.astrbot.runtime")
        self.runtime = module.AstrBotRuntime(
            self.context,
            plugin_root=self.bundle.resource_root,
            data_dir=self.data,
            font_manager_factory=fixture._Font,
            bundled_selection=None,
            plain_factory=lambda text: text,
            chain_factory=lambda chain: list(chain),
            http_transport_factory=fixture._Transport,
        )
        self.generation.cache = ArtifactCache(self.data, store=self.store)
        self.generation.attach(self.runtime)
        await self.runtime.initialize()
        assert self.runtime.ready
        self.manager = self.runtime._independent_packages
        self.executor = self.runtime._core.database.executor

    async def snapshot(self):
        def read(unit):
            return {
                key: [dict(r) for r in unit.connection.execute(sql)]
                for key, sql in (
                    ("owners", "SELECT * FROM runtime_owners ORDER BY scope"),
                    ("journal", "SELECT * FROM runtime_transitions ORDER BY rowid"),
                    ("migrations", "SELECT * FROM schema_migrations ORDER BY version"),
                )
            }

        return await self.executor.run_internal_transaction(read)

    def existing(self, activated=True):
        root = self.root / "crash_1_0_1"
        metadata = SimpleNamespace(
            name="crash",
            author="Synthetic Author",
            activated=activated,
            star_cls=object(),
            root_dir_name=root.name,
            module=SimpleNamespace(__file__=str(root / "main.py")),
        )
        self.registry.append(metadata)
        return metadata

    async def stop(self):
        assert (await self.generation.terminate(timeout=5))["state"] == "quiet"


def _save(root, name, value):
    (root / name).write_text(json.dumps(value, indent=2))
    print(json.dumps({"event": name, **value}), flush=True)


async def _crash(root, phase):
    host = _Runtime()
    await host.start(root)
    first, metadata = await host.make_dlc("crash")
    await host.manager.replace(first)
    await host.executor.run_transaction(
        lambda unit: unit.connection.execute(
            "CREATE TABLE sl10_business_effects (value TEXT PRIMARY KEY)"
        ).rowcount
    )
    await host.executor.run_transaction(
        lambda unit: unit.connection.execute(
            "INSERT INTO sl10_business_effects VALUES ('baseline')"
        ).rowcount
    )
    host.registry.remove(metadata)
    second, _ = await host.make_dlc("crash", "1.0.1", 2)
    _save(root, "before.json", await host.snapshot())
    transaction = host.executor.run_internal_transaction

    async def after_commit(callback, **kwargs):
        result = await transaction(callback, **kwargs)
        rows = await transaction(
            lambda unit: [
                dict(r)
                for r in unit.connection.execute(
                    "SELECT * FROM runtime_transitions WHERE scope='package:crash' ORDER BY rowid DESC LIMIT 1"
                )
            ]
        )
        if rows and rows[0]["phase"] == phase and rows[0]["to_epoch"] == 2:
            # A second transaction observes the phase after the real COMMIT.
            host.executor.run_internal_transaction = transaction
            gates = importlib.import_module(
                host.namespace + ".services.runtime_effect_gate"
            )
            candidate = host.manager._prepared[id(second.instance)]
            with gates.invocation_guard(candidate.gate):
                try:
                    await host.executor.run_transaction(
                        lambda unit: unit.connection.execute(
                            "INSERT INTO sl10_business_effects VALUES ('candidate')"
                        ).rowcount
                    )
                except gates.EffectRejected:
                    pass
                else:
                    raise AssertionError(
                        "candidate gate opened before integrator publication"
                    )
            snapshot = await host.snapshot()
            snapshot.update(
                phase=phase,
                candidate_gate_closed=True,
                current=dict(host.manager.cache.current),
                last_good=dict(host.manager.cache.last_good),
            )
            _save(root, "crashed.json", snapshot)
            os._exit(73)
        return result

    host.executor.run_internal_transaction = after_commit
    await host.manager.replace(second)
    raise AssertionError("crash boundary never observed")


async def _recover(root, disabled=False):
    crashed = json.loads((root / "crashed.json").read_text())
    before = json.loads((root / "before.json").read_text())
    host = _Runtime()
    await host.start(root)
    restarted = await host.snapshot()
    assert restarted["migrations"] == before["migrations"]
    old_core = next(r for r in crashed["owners"] if r["scope"] == "core")
    core = next(r for r in restarted["owners"] if r["scope"] == "core")
    old = next(r for r in crashed["owners"] if r["scope"] == "package:crash")
    fenced = next(r for r in restarted["owners"] if r["scope"] == "package:crash")
    assert core["token"] != old_core["token"] and fenced["accepting"] == 0
    assert not host.manager.packages
    assert host.manager.cache.current == crashed["current"]
    assert host.manager.cache.last_good == crashed["last_good"]
    gates = importlib.import_module(host.namespace + ".services.runtime_effect_gate")
    stale = gates.RuntimeEffectGate(
        host.generation.kernel,
        host.generation.scope,
        core["token"],
        core["core_epoch"],
        package_id="crash",
        package_token=old["token"],
        package_epoch=old["package_epoch"],
        grant_revision=old["grant_revision"],
        authority_check=lambda: None,
        policy_assert=lambda unit: None,
    )
    called = []

    def effect(unit):
        unit.connection.execute(
            "INSERT INTO sl10_business_effects VALUES ('recovered')"
        )
        called.append("effect")

    initial_effects = await host.executor.run_internal_transaction(
        lambda unit: [
            r[0]
            for r in unit.connection.execute(
                "SELECT value FROM sl10_business_effects ORDER BY value"
            )
        ]
    )
    assert initial_effects == ["baseline"]
    with gates.invocation_guard(stale):
        try:
            await host.executor.run_transaction(effect)
        except gates.EffectRejected:
            pass
        else:
            raise AssertionError("abandoned owner authorized a business effect")
    assert not called
    stale.close()
    metadata = host.existing(activated=not disabled)
    if disabled:
        await host.manager.reconcile()
        assert not host.manager.discovery.scan() and not host.manager.packages
    else:
        result = await host.manager.replace(
            host.manager.discovery.for_instance(metadata.star_cls)
        )
        assert result["state"] == "active", result
        package = host.manager.packages["crash"]
        assert package.gate.identity.package_token != old["token"]
        package.gate.check()
        assert "crash/demo" in host.runtime._core.registry.snapshot().modules
        with gates.invocation_guard(package.gate):
            await host.executor.run_transaction(effect)
        assert called == ["effect"]
    effects = await host.executor.run_internal_transaction(
        lambda unit: [
            r[0]
            for r in unit.connection.execute(
                "SELECT value FROM sl10_business_effects ORDER BY value"
            )
        ]
    )
    assert effects == (["baseline"] if disabled else ["baseline", "recovered"])
    final = await host.snapshot()
    assert len(final["owners"]) == len({r["scope"] for r in final["owners"]}) == 2
    assert final["migrations"] == before["migrations"]
    assert sum(r["accepting"] for r in final["owners"]) == (1 if disabled else 2)
    pins = (
        *host.manager.cache.current.values(),
        *host.manager.cache.last_good.values(),
    )
    removed = host.manager.cache.gc()
    assert all(
        key not in removed and (host.manager.cache.root / key).is_dir() for key in pins
    )
    prior_ids = {r["id"] for r in crashed["journal"]}
    unfinished = [
        r
        for r in final["journal"]
        if r["id"] in prior_ids and r["phase"] != "FINALIZED"
    ]
    _save(
        root,
        "recovered.json",
        dict(
            phase=crashed["phase"],
            disabled=disabled,
            owners=final["owners"],
            stale_effects=0,
            accepted_effects=len(called),
            durable_business_effects=effects,
            migration_rows_unchanged=True,
            pins_retained=True,
            unfinished_prior_journal=unfinished,
        ),
    )
    await host.stop()


async def _delivery(root, operation, state):
    # Existing fixture composes real repositories/service; only its temp directory
    # constructor is redirected so two interpreters share disposable outbox data.
    from datetime import timedelta
    from unittest.mock import patch

    from tests.services import test_delivery as delivery

    case = delivery.DeliveryTests(
        "test_accepted_receipt_is_persisted_for_exact_subscriber"
    )
    with patch.object(
        delivery.tempfile,
        "TemporaryDirectory",
        return_value=SimpleNamespace(name=str(root), cleanup=lambda: None),
    ):
        case.setUp()
    if operation == "delivery-crash":
        await case.asyncSetUp()

        class Port:
            async def send(self, target, payload):
                with (root / "external-sends").open("a") as sink:
                    sink.write("send\n")
                    sink.flush()
                    os.fsync(sink.fileno())
                if state == "SENDING":
                    os._exit(74)  # external success before durable receipt
                status = (
                    delivery.MessageStatus.UNKNOWN
                    if state == "UNKNOWN"
                    else delivery.MessageStatus.ACCEPTED
                )
                return delivery.MessageReceipt(status, "controlled-sink")

        result = await case._service(port=Port()).dispatch_event(
            "event-1", 1, "sub-1", 1
        )
        assert result.state.value == state.lower()
        os._exit(74)
    case.now += timedelta(hours=2)
    await case.service.recover_startup()
    result = await case.service.dispatch_event("event-1", 1, "sub-1", 1)
    expected = "sent" if state == "SENT" else "unknown"
    assert result.state.value == expected
    assert not case.port.calls
    assert (root / "external-sends").read_text() == "send\n"
    _save(
        root,
        "delivery-recovered.json",
        dict(prior=state, recovered=expected, external_sends=1, resend_calls=0),
    )
    await case.db.executor.close()


async def _cleanup_retention(root, cooperative):
    host = _Runtime()
    await host.start(root)
    installation, _ = await host.make_dlc("crash")
    await host.manager.replace(installation)
    package = host.manager.packages["crash"]
    gates = importlib.import_module(host.namespace + ".services.runtime_effect_gate")
    entered, release = asyncio.Event(), asyncio.Event()

    async def retained_io():
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass

    task = asyncio.create_task(gates.run_effect(retained_io, guard=package.gate))
    await entered.wait()
    assert (await host.generation.terminate(timeout=0.03))["state"] == "parked"
    record = host.generation.kernel._record(host.generation.scope)
    cleanup = record.cleanup
    assert record.tickets and not cleanup.done()
    acquired = await host.generation.kernel.acquire_core(
        host.generation.scope, object(), 1, "b" * 64
    )
    assert acquired["state"] == "parked"
    assert record.cleanup is cleanup
    try:
        package.gate.check()
    except gates.EffectRejected:
        pass
    else:
        raise AssertionError("fenced package gate remained open")
    busy = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys,tests; from ygl_test_subject.bootstrap.artifact_cache import ArtifactCache; ArtifactCache(sys.argv[1])",
            str(host.data),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert busy.returncode != 0 and "data_dir_busy" in busy.stderr
    pins = dict(host.manager.cache.current)
    assert pins and all(
        (host.manager.cache.root / key).is_dir() for key in pins.values()
    )
    if cooperative:
        release.set()
        await asyncio.wait_for(task, 5)
        assert (await host.generation.terminate(timeout=5))["state"] == "quiet"
        assert cleanup.done()
        assert not record.tickets
        successor = await host.generation.kernel.acquire_core(
            host.generation.scope, object(), 1, "b" * 64
        )
        assert successor["state"] == "acquired"
        assert successor["owner_token"] != host.generation.receipt["owner_token"]
    else:
        assert (await host.generation.terminate(timeout=0.03))["state"] == "parked"
        assert record.cleanup is cleanup and record.tickets and not task.done()
    _save(
        root,
        "cleanup-retained.json",
        dict(
            cooperative=cooperative,
            successor=cooperative,
            exact_cleanup_retained=True,
            cache_busy_while_pending=True,
            pins_retained=True,
        ),
    )
    # Isolated uncooperative task intentionally cannot be shut down by cancellation.
    os._exit(0)


class ProcessRecoveryTests(unittest.TestCase):
    def worker(self, root, operation, phase=""):
        result = subprocess.run(
            [sys.executable, "-m", __name__, "--worker", operation, str(root), phase],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if os.environ.get("YGL_CRASH_EVIDENCE"):
            evidence = Path(os.environ["YGL_CRASH_EVIDENCE"])
            evidence.mkdir(parents=True, exist_ok=True)
            (evidence / f"{root.name}-{operation}.log").write_text(
                f"argv={result.args!r}\nexit={result.returncode}\n"
                + result.stdout
                + result.stderr
            )
        return result

    def test_process_death_at_every_package_commit_boundary(self):
        for phase in PHASES:
            with (
                self.subTest(phase=phase),
                tempfile.TemporaryDirectory(
                    prefix=f"sl10-{phase.lower()}-"
                ) as directory,
            ):
                root = Path(directory)
                result = self.worker(root, "crash", phase)
                self.assertEqual(result.returncode, 73, result.stdout + result.stderr)
                self.assertEqual(
                    json.loads((root / "crashed.json").read_text())["phase"], phase
                )
                result = self.worker(root, "recover")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_process_restart_never_resends_sent_unknown_or_receipt_lost(self):
        for state in ("SENT", "UNKNOWN", "SENDING"):
            with (
                self.subTest(state=state),
                tempfile.TemporaryDirectory(prefix="sl10-outbox-") as directory,
            ):
                root = Path(directory)
                result = self.worker(root, "delivery-crash", state)
                self.assertEqual(result.returncode, 74, result.stdout + result.stderr)
                result = self.worker(root, "delivery-recover", state)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_isolated_cooperative_and_uncooperative_cleanup(self):
        for mode in ("cooperative", "uncooperative"):
            with (
                self.subTest(mode=mode),
                tempfile.TemporaryDirectory(prefix="sl10-cleanup-") as directory,
            ):
                result = self.worker(Path(directory), mode)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_disabled_installation_does_not_revive_from_active_or_cache(self):
        with tempfile.TemporaryDirectory(prefix="sl10-disabled-") as directory:
            root = Path(directory)
            result = self.worker(root, "crash", "ACTIVE")
            self.assertEqual(result.returncode, 73, result.stdout + result.stderr)
            result = self.worker(root, "disabled")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        operation, directory, phase = sys.argv[2:5]
        if operation.startswith("delivery-"):
            asyncio.run(_delivery(Path(directory), operation, phase))
        elif operation in ("cooperative", "uncooperative"):
            asyncio.run(_cleanup_retention(Path(directory), operation == "cooperative"))
        elif operation == "crash":
            asyncio.run(_crash(Path(directory), phase))
        else:
            asyncio.run(_recover(Path(directory), operation == "disabled"))
    else:
        unittest.main()
