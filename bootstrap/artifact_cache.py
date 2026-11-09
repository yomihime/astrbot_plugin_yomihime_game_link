"""Durable verified artifact storage; callers still authorize every returned lease.

Pins preserve bytes only, never permission to start a disabled/uninstalled package.
Disk paths are never executed: load captures a fresh immutable RuntimeBundleLease.
Native Windows path checks do not promise hostile reparse-race protection.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path

from ..extensions.file_identity import (
    FileIdentityUnavailable,
    handle_identity,
    stable_identity,
)
from ..extensions.hot_update_protocol import parse_release
from ..extensions.runtime_bundle import RuntimeBundleError, RuntimeBundleStore
from ..infrastructure.runtime_owner_lock import RuntimeOwnerLock
from .coordinator import get_coordinator

_RELEASE_LIMIT = 2 * 1024 * 1024
_SIGNATURE_LIMIT = 64 * 1024
_PINS_LIMIT = 64 * 1024
_PACKAGE = re.compile(r"[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*\Z")
_CORE_STREAM = re.compile(r"ygl_core:[0-9a-f]{64}:(?:stable|preview)\Z")


class _CoreRecovery:
    """Internal exact-pin recovery authority, captured before a failed start."""

    def __init__(self, root, stream, plugin_id, migrations, pins):
        self.root = root
        self.stream = stream
        self.plugin_id = plugin_id
        self.migrations = migrations
        self.pins = pins


def _plain(path, *, directory=False):
    info = path.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x400
        or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        or (not directory and info.st_nlink != 1)
    ):
        raise RuntimeBundleError("cache_path_rejected")
    return info


def _read(path, maximum):
    before = _plain(path)
    if before.st_size > maximum:
        raise RuntimeBundleError("cache_metadata_budget_exceeded")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        data = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())

    try:
        changed = (
            stable_identity(before) != stable_identity(opened)
            or handle_identity(opened) != handle_identity(after)
            or stable_identity(_plain(path)) != stable_identity(before)
        )
    except FileIdentityUnavailable:
        raise RuntimeBundleError("cache_identity_unavailable") from None
    if changed:
        raise RuntimeBundleError("cache_changed")
    if len(data) > maximum:
        raise RuntimeBundleError("cache_metadata_budget_exceeded")
    return data


def _sync_dir(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _write(path, content):
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _locked(method):
    @functools.wraps(method)
    def call(self, *args, **kwargs):
        with self.kernel.lock:
            self._ensure_open()
            return method(self, *args, **kwargs)

    return call


class ArtifactCache:
    """Keep current/previous good artifacts within a separate durable disk budget.

    Each successful capture/load owns one candidate hold. Publish consumes one
    own hold; discard consumes an unused/rejected hold after bundle release.
    Instances share pins, holds and a plain memory-budget ledger through the
    reload-surviving coordinator, but use their own current store implementation.
    Call close only after the instance's runtime is quiet and leases released.
    """

    def __init__(
        self,
        data_root,
        *,
        store=None,
        max_disk_bytes=512 * 1024 * 1024,
        max_entries=256,
        max_pin_entries=128,
    ):
        if any(
            type(n) is not int or n < 1
            for n in (max_disk_bytes, max_entries, max_pin_entries)
        ):
            raise ValueError("invalid cache limits")
        data_root = Path(data_root).absolute()
        # Reject existing links before resolve can hide them.
        for part in (data_root, *data_root.parents):
            if part.exists() or part.is_symlink():
                _plain(part, directory=True)
        self.root = data_root.resolve() / "runtime-artifacts-v1"
        self.root.mkdir(parents=True, exist_ok=True)
        _plain(self.root, directory=True)
        self.max_disk_bytes = max_disk_bytes
        self.max_entries = max_entries
        self.max_pin_entries = max_pin_entries
        self.kernel = get_coordinator()
        self._closed = True
        self._holds = {}
        self._core_admissions = {}
        self._scope = str(self.root)
        with self.kernel.lock:
            states = getattr(self.kernel, "artifact_cache_states", None)
            if states is None:
                states = self.kernel.artifact_cache_states = {}
            if type(states) is not dict:
                raise RuntimeBundleError("cache_registry_invalid")
            state = states.get(self._scope)
            if state is None:
                if len(states) >= 128:
                    raise RuntimeBundleError("cache_registry_budget_exceeded")
                file_lock = RuntimeOwnerLock(self.root)
                try:
                    current, previous, heads = self._load_pins()
                    owned_store = store or RuntimeBundleStore()
                    budget = owned_store.budget_state
                except BaseException:
                    file_lock.close()
                    raise
                state = {
                    "current": current,
                    "last_good": previous,
                    "release_heads": heads,
                    "ledger_present": (self.root / "pins.json").exists(),
                    "holds": {},
                    "refs": 0,
                    "file_lock": file_lock,
                    "budget": budget,
                }
                states[self._scope] = state
            else:
                if (
                    state.get("ledger_present")
                    and not (self.root / "pins.json").exists()
                ):
                    raise RuntimeBundleError("cache_pins_missing")
                current, previous, heads = self._load_pins()
                if (
                    current != state["current"]
                    or previous != state["last_good"]
                    or heads != state["release_heads"]
                ):
                    raise RuntimeBundleError("cache_pins_changed")
                owned_store = store or RuntimeBundleStore(shared_budget=state["budget"])
            if store is not None and store.budget_state is not state["budget"]:
                raise RuntimeBundleError("cache_store_conflict")
            self.store = owned_store
            if state["refs"] >= 128:
                raise RuntimeBundleError("cache_registry_budget_exceeded")
            state["refs"] += 1
            self._state = state
            self._closed = False

    def _ensure_open(self):
        if self._closed:
            raise RuntimeBundleError("cache_closed")

    @property
    def current(self):
        return self._state["current"]

    @property
    def last_good(self):
        return self._state["last_good"]

    @property
    def release_heads(self):
        # Callers must not be able to lower the shared watermark in memory.
        return {
            stream: dict(head) for stream, head in self._state["release_heads"].items()
        }

    @property
    def candidates(self):
        return set(self._state["holds"])

    def _hold(self, key):
        held = self._state["holds"]
        if key not in held and len(held) >= self.max_entries:
            raise RuntimeBundleError("cache_candidate_budget_exceeded")
        self._holds[key] = self._holds.get(key, 0) + 1
        held[key] = held.get(key, 0) + 1

    def _drop_hold(self, key):
        count = self._holds.get(key, 0)
        if not count:
            return
        if count == 1:
            del self._holds[key]
        else:
            self._holds[key] = count - 1
        held = self._state["holds"]
        if held[key] == 1:
            del held[key]
        else:
            held[key] -= 1

    def close(self):
        """Return own holds; final close requires all shared runtime leases released.

        A rejected final close keeps the registry and OS lock, and can be retried
        after actual runtime quiet and lease.release(). No destructor releases it.
        """
        with self.kernel.lock:
            if self._closed:
                return
            if self._state["refs"] == 1 and any(
                self.store.reserved_bytes(kind) for kind in ("core", "dlc")
            ):
                raise RuntimeBundleError("cache_bundles_retained")
            for key, count in tuple(self._holds.items()):
                for _ in range(count):
                    self._drop_hold(key)
            self._state["refs"] -= 1
            self._closed = True
            self._core_admissions.clear()
            if not self._state["refs"] and not self._state["holds"]:
                self._state["file_lock"].close()
                del self.kernel.artifact_cache_states[self._scope]

    @staticmethod
    def _key(raw_release):
        return hashlib.sha256(raw_release).hexdigest()

    def _path(self, key):
        if type(key) is not str or re.fullmatch(r"[0-9a-f]{64}", key) is None:
            raise RuntimeBundleError("cache_key_rejected")
        _plain(self.root, directory=True)
        result = self.root / key
        if result.exists() or result.is_symlink():
            _plain(result, directory=True)
        return result

    def _check_pins(self, current, last_good):
        if type(current) is not dict or type(last_good) is not dict:
            raise ValueError("pin shape")
        if len(set(current) | set(last_good)) > self.max_pin_entries:
            raise ValueError("pin limit")
        for mapping in (current, last_good):
            for package, key in mapping.items():
                if (
                    type(package) is not str
                    or len(package) > 128
                    or not _PACKAGE.fullmatch(package)
                ):
                    raise ValueError("pin identity")
                target = self._path(key)
                raw = _read(target / "release.json", _RELEASE_LIMIT)
                if self._key(raw) != key or parse_release(raw)["package_id"] != package:
                    raise ValueError("pin artifact identity")
        if not set(last_good) <= set(current):
            raise ValueError("orphan previous pin")

    def _load_pins(self):
        path = self.root / "pins.json"
        if not path.exists() and not path.is_symlink():
            # Missing history is not a fresh installation when Core bytes remain.
            # r15 is unreleased: older Core cache metadata needs explicit maintenance,
            # rather than silently inventing a lower sequence watermark.
            self._check_core_history({}, legacy=True)
            return {}, {}, {}
        try:

            def pairs(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise ValueError("duplicate pin key")
                    result[key] = value
                return result

            doc = json.loads(_read(path, _PINS_LIMIT), object_pairs_hook=pairs)
            if type(doc) is not dict or set(doc) not in (
                {"current", "last_good"},
                {"current", "last_good", "release_heads"},
            ):
                raise ValueError("pin schema")
            self._check_pins(doc["current"], doc["last_good"])
            heads = doc.get("release_heads", {})
            self._check_heads(heads)
            self._check_core_history(
                heads,
                (doc["current"].get("ygl_core"), doc["last_good"].get("ygl_core")),
                legacy="release_heads" not in doc,
            )
            return doc["current"], doc["last_good"], heads
        except (OSError, ValueError, TypeError) as exc:
            if getattr(exc, "code", None) == "cache_core_head_migration_required":
                raise
            raise RuntimeBundleError("cache_pins_invalid") from exc

    def _check_core_history(self, heads, core_pins=(), *, legacy=False):
        # Committed metadata defines accepted history. An uncommitted candidate
        # directory cannot poison a valid ledger after an atomic write failure.
        # Legacy/missing metadata still needs an explicit maintenance boundary.
        known_core = {head["digest"] for head in heads.values()} | set(core_pins)
        for entry in self._entries():
            if re.fullmatch(r"[0-9a-f]{64}", entry.name) is None:
                continue
            if not legacy and entry.name not in known_core:
                continue
            try:
                raw, _ = self._metadata(entry)
                doc = parse_release(raw)
            except (OSError, ValueError):
                if entry.name in known_core:
                    raise
                # An unrelated, unpinned DLC orphan is checked when loaded;
                # it cannot supply any Core release-head history.
                continue
            if doc["package_id"] != "ygl_core":
                continue
            key_bytes = _read(entry / "release-key.json", 16384)
            key_row = next(
                (r for r in doc["files"] if r["path"] == "release-key.json"), None
            )
            if (
                self._key(raw) != entry.name
                or key_row is None
                or len(key_bytes) != key_row["size"]
                or hashlib.sha256(key_bytes).hexdigest() != key_row["sha256"]
            ):
                raise ValueError("Core history identity")
            public = json.loads(key_bytes)
            stream = f"ygl_core:{public['key_id']}:{doc['channel']}"
            head = heads.get(stream)
            if head is None:
                raise RuntimeBundleError("cache_core_head_migration_required")
            if doc["release_sequence"] > head["release_sequence"] or (
                doc["release_sequence"] == head["release_sequence"]
                and entry.name != head["digest"]
            ):
                raise ValueError("Core history exceeds release head")

    def _check_heads(self, heads):
        if type(heads) is not dict or len(heads) > self.max_pin_entries:
            raise ValueError("release head limit or shape")
        for stream, head in heads.items():
            if (
                type(stream) is not str
                or _CORE_STREAM.fullmatch(stream) is None
                or type(head) is not dict
                or set(head) != {"release_sequence", "digest"}
                or type(head["release_sequence"]) is not int
                or not 0 <= head["release_sequence"] <= 9007199254740991
                or type(head["digest"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", head["digest"]) is None
            ):
                raise ValueError("release head identity")

    def _assert_pins_unchanged(self):
        if self._state.get("ledger_present") and not (self.root / "pins.json").exists():
            raise RuntimeBundleError("cache_pins_missing")
        current, previous, heads = self._load_pins()
        if (
            current != self.current
            or previous != self.last_good
            or heads != self._state["release_heads"]
        ):
            raise RuntimeBundleError("cache_pins_changed")

    def _initialize_core_ledger(self):
        path = self.root / "pins.json"
        if path.exists() and "release_heads" in json.loads(_read(path, _PINS_LIMIT)):
            return
        # Commit recognizable empty new-format metadata before the first Core
        # artifact can reach disk. A failed first head update is then retryable,
        # while a genuinely missing historical ledger remains fail-closed.
        self._save_pins(dict(self.current), dict(self.last_good), self.release_heads)

    @staticmethod
    def _core_identity(bundle):
        # This method is used only after validate_core_release has authenticated
        # the inventory-covered key. It does not enroll a DLC publisher or grant.
        public = json.loads(bundle.read_resource("release-key.json"))
        doc = bundle.release_doc
        stream = f"{doc['package_id']}:{public['key_id']}:{doc['channel']}"
        if _CORE_STREAM.fullmatch(stream) is None:
            raise RuntimeBundleError("core_identity_mismatch")
        migrations = tuple(
            sorted(
                (row["path"], row["size"], row["sha256"])
                for row in doc["files"]
                if row["path"].startswith("infrastructure/sqlite/migrations/")
                and row["path"].endswith(".sql")
            )
        )
        return stream, dict(doc["plugin_id"]), migrations

    def _core_head_check(self, bundle, key):
        stream, _, _ = self._core_identity(bundle)
        head = self._state["release_heads"].get(stream)
        sequence = bundle.release_doc["release_sequence"]
        if head is not None and (
            sequence < head["release_sequence"]
            or (sequence == head["release_sequence"] and key != head["digest"])
        ):
            raise RuntimeBundleError("core_release_sequence_rejected")
        return stream, sequence

    def _accept_core(self, bundle, key):
        stream, sequence = self._core_head_check(bundle, key)
        heads = self.release_heads
        head = {"release_sequence": sequence, "digest": key}
        if heads.get(stream) != head:
            heads[stream] = head
            self._check_heads(heads)  # Never evict a historical stream to fit.
            self._save_pins(dict(self.current), dict(self.last_good), heads)
        self._core_admissions[id(bundle)] = (bundle, key, False)

    @_locked
    def check_core_admission(self, bundle, *, fallback=None):
        self._assert_pins_unchanged()
        admission = self._core_admissions.get(id(bundle))
        if admission is None or admission[0] is not bundle or bundle._released:
            raise RuntimeBundleError("core_admission_missing")
        _, key, recovery = admission
        if fallback is not None and fallback != (recovery is not False):
            raise RuntimeBundleError("core_admission_mode_mismatch")
        if recovery is False:
            self._core_head_check(bundle, key)
        else:
            self._check_core_recovery(key, bundle, recovery)
        return key

    @_locked
    def core_recovery_plan(self, bundle):
        self.check_core_admission(bundle)
        stream, plugin_id, migrations = self._core_identity(bundle)
        return _CoreRecovery(
            self._scope,
            stream,
            plugin_id,
            migrations,
            tuple(
                dict.fromkeys(
                    (self.current.get("ygl_core"), self.last_good.get("ygl_core"))
                )
            ),
        )

    def _check_core_recovery(self, key, bundle, recovery):
        if (
            not isinstance(recovery, _CoreRecovery)
            or recovery.root != self._scope
            or key is None
            or key not in recovery.pins
            or key not in (self.current.get("ygl_core"), self.last_good.get("ygl_core"))
            or self._core_identity(bundle)
            != (recovery.stream, recovery.plugin_id, recovery.migrations)
            or recovery.stream not in self._state["release_heads"]
        ):
            raise RuntimeBundleError("core_fallback_rejected")

    async def load_core_fallback(self, key, *, recovery, validate):
        """Only the thin entrypoint's failed-start branch uses this exact pin path."""
        return await self._load(key, kind="core", validate=validate, recovery=recovery)

    def _metadata(self, root):
        _plain(root, directory=True)
        raw = _read(root / "release.json", _RELEASE_LIMIT)
        signature_path = root / "release.sig.json"
        signature = (
            _read(signature_path, _SIGNATURE_LIMIT)
            if signature_path.exists() or signature_path.is_symlink()
            else None
        )
        return raw, signature

    def _verify_existing(self, root, raw, signature, files):
        cached_raw, cached_signature = self._metadata(root)
        if cached_raw != raw or cached_signature != signature:
            raise RuntimeBundleError("cache_release_changed")
        expected = dict(files)
        expected["release.json"] = raw
        if signature is not None:
            expected["release.sig.json"] = signature
        observed = set()
        count = 0
        for parent, dirs, names in os.walk(root, followlinks=False):
            count += len(dirs) + len(names)
            if count > 8192 or len(Path(parent).relative_to(root).parts) > 24:
                raise RuntimeBundleError("cache_tree_budget_exceeded")
            for name in dirs:
                _plain(Path(parent) / name, directory=True)
            for name in names:
                path = Path(parent) / name
                relative = path.relative_to(root).as_posix()
                if relative not in expected:
                    raise RuntimeBundleError("cache_inventory_changed")
                content = _read(path, len(expected[relative]))
                if content != expected[relative]:
                    raise RuntimeBundleError("cache_payload_changed")
                observed.add(relative)
        if observed != set(expected):
            raise RuntimeBundleError("cache_inventory_changed")

    def _entries(self):
        # Bound the directory listing even when local disk state is corrupted.
        result = []
        with os.scandir(self.root) as entries:
            for entry in entries:
                if len(result) >= self.max_entries + 16:
                    raise RuntimeBundleError("cache_tree_budget_exceeded")
                result.append(Path(entry.path))
        return result

    def _usage(self):
        total, entries = 0, 0
        for entry in self._entries():
            if not entry.is_dir() or entry.is_symlink():
                total += _plain(entry).st_size
                continue
            _plain(entry, directory=True)
            entries += 1
            count = 0
            for parent, dirs, names in os.walk(entry, followlinks=False):
                count += len(dirs) + len(names)
                if count > 8192 or len(Path(parent).relative_to(entry).parts) > 24:
                    raise RuntimeBundleError("cache_tree_budget_exceeded")
                for name in dirs:
                    _plain(Path(parent) / name, directory=True)
                for name in names:
                    total += _plain(Path(parent) / name).st_size
        return total, entries

    async def capture(self, installation_root, *, kind, validate):
        with self.kernel.lock:
            self._ensure_open()
            self._assert_pins_unchanged()
            raw, signature = self._metadata(Path(installation_root))
            release = parse_release(raw)
            key = self._key(raw)
            self._hold(key)
            try:
                bundle = self.store.capture(Path(installation_root), release, kind=kind)
            except BaseException:
                self._drop_hold(key)
                raise
        try:
            receipt = await validate(raw, signature, bundle)
            if receipt is None or receipt is False:
                raise RuntimeBundleError("artifact_untrusted")
            if kind == "core":
                from .loader import validate_core_release

                validate_core_release(raw, signature, bundle)
            with self.kernel.lock:
                self._ensure_open()
                self._assert_pins_unchanged()
                if kind == "core":
                    stream, sequence = self._core_head_check(bundle, key)
                    heads = self.release_heads
                    heads[stream] = {"release_sequence": sequence, "digest": key}
                    self._check_heads(heads)
                    self._initialize_core_ledger()
                self._persist(key, raw, signature, bundle)
                if kind == "core":
                    self._accept_core(bundle, key)
            return bundle, receipt, key
        except BaseException:
            bundle.release()
            with self.kernel.lock:
                if not self._closed:
                    self._drop_hold(key)
            raise

    def _persist(self, key, raw, signature, bundle):
        target = self._path(key)
        if target.exists():
            self._verify_existing(target, raw, signature, bundle.files)
            return
        required = bundle.total_bytes + len(raw) + len(signature or b"")
        used, entries = self._usage()
        if used + required > self.max_disk_bytes or entries + 1 > self.max_entries:
            self.gc()
            used, entries = self._usage()
        if used + required > self.max_disk_bytes or entries + 1 > self.max_entries:
            raise RuntimeBundleError("cache_disk_budget_exceeded")
        staging = Path(tempfile.mkdtemp(prefix=".candidate-", dir=self.root))
        try:
            for name, content in bundle.files.items():
                path = staging / name
                path.parent.mkdir(parents=True, exist_ok=True)
                _write(path, content)
            _write(staging / "release.json", raw)
            if signature is not None:
                _write(staging / "release.sig.json", signature)
            for parent, _, _ in os.walk(staging, topdown=False):
                _sync_dir(parent)
            os.replace(staging, target)
            _sync_dir(self.root)
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    async def load(self, key, *, kind, validate):
        return await self._load(key, kind=kind, validate=validate)

    async def _load(self, key, *, kind, validate, recovery=None):
        with self.kernel.lock:
            self._ensure_open()
            self._assert_pins_unchanged()
            root = self._path(key)
            raw, signature = self._metadata(root)
            if self._key(raw) != key:
                raise RuntimeBundleError("cache_release_changed")
            self._hold(key)
            try:
                bundle = self.store.capture(root, parse_release(raw), kind=kind)
            except BaseException:
                self._drop_hold(key)
                raise
        try:
            self._verify_existing(root, raw, signature, bundle.files)
            receipt = await validate(raw, signature, bundle)
            if receipt is None or receipt is False:
                raise RuntimeBundleError("artifact_untrusted")
            if kind == "core":
                from .loader import validate_core_release

                validate_core_release(raw, signature, bundle)
            with self.kernel.lock:
                self._ensure_open()
                self._assert_pins_unchanged()
                if kind == "core":
                    if recovery is None:
                        self._accept_core(bundle, key)
                    else:
                        self._check_core_recovery(key, bundle, recovery)
                        self._core_admissions[id(bundle)] = (bundle, key, recovery)
            return bundle, receipt, key
        except BaseException:
            bundle.release()
            with self.kernel.lock:
                if not self._closed:
                    self._drop_hold(key)
            raise

    @_locked
    def publish(self, package_id, key):
        self._assert_pins_unchanged()
        if package_id == "ygl_core":
            admitted = [
                row
                for row in self._core_admissions.values()
                if row[1] == key and row[2] is False and not row[0]._released
            ]
            if not admitted:
                raise RuntimeBundleError("core_admission_missing")
            self.check_core_admission(admitted[0][0])
        current, previous = dict(self.current), dict(self.last_good)
        old = current.get(package_id)
        if old is not None and old != key:
            previous[package_id] = old
        current[package_id] = key
        self._check_pins(current, previous)
        self._save_pins(current, previous, self.release_heads)
        self._drop_hold(key)

    def _save_pins(self, current, previous, heads):
        self._check_heads(heads)
        payload = json.dumps(
            {"current": current, "last_good": previous, "release_heads": heads},
            sort_keys=True,
        ).encode()
        if len(payload) > _PINS_LIMIT:
            raise RuntimeBundleError("cache_metadata_budget_exceeded")
        used, _ = self._usage()
        pin_path = self.root / "pins.json"
        if pin_path.exists() or pin_path.is_symlink():
            _plain(pin_path)
        # Atomic replacement temporarily keeps both the old and new pin bytes.
        if used + len(payload) > self.max_disk_bytes:
            raise RuntimeBundleError("cache_disk_budget_exceeded")
        fd, name = tempfile.mkstemp(prefix=".pins-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.root / "pins.json")
            # Once replace succeeds the visible state changed even if fsync fails.
            self._state["current"], self._state["last_good"] = current, previous
            self._state["release_heads"] = heads
            self._state["ledger_present"] = True
            _sync_dir(self.root)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @_locked
    def discard(self, key):
        self._path(key)
        self._drop_hold(key)

    @_locked
    def gc(self, *, retained=()):
        retained = set(retained)
        for key in retained:
            self._path(key)
        keep = (
            set(self.current.values())
            | set(self.last_good.values())
            | self.candidates
            | retained
        )
        removed = []
        for item in self._entries():
            if item.name in keep or re.fullmatch(r"[0-9a-f]{64}", item.name) is None:
                continue
            _plain(item, directory=True)
            # Inspect before removal; never follow cache links into business data.
            count = 0
            for parent, dirs, names in os.walk(item, followlinks=False):
                count += len(dirs) + len(names)
                if count > 8192 or len(Path(parent).relative_to(item).parts) > 24:
                    raise RuntimeBundleError("cache_tree_budget_exceeded")
                for name in dirs:
                    _plain(Path(parent) / name, directory=True)
                for name in names:
                    _plain(Path(parent) / name)
            shutil.rmtree(item)
            removed.append(item.name)
        return tuple(removed)
