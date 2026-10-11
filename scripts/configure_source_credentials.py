"""Configure FFLogs credentials for an installed FF14 module locally.

Run this while AstrBot is stopped. Credential values are requested through
hidden prompts and are persisted only through Core's encrypted configuration
and secret-receipt path.
"""

# Imports below the bootstrap deliberately run after the plugin-local SDK pin.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

# A packaged `python -m <plugin>.scripts.configure_source_credentials` starts
# outside the plugin directory. Pin imports to this package's own SDK before
# importing any host/Core code; never inherit a process-global SDK by accident.
_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_SDK_VERSION = "0.1.0a6"
_SDK_MODULE_ABI_VERSION = "2.0"
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))
try:
    _sdk = importlib.import_module("yomihime_game_link_sdk")
    _sdk_version = importlib.import_module("yomihime_game_link_sdk.version")
    _sdk_root = Path(_sdk.__file__).resolve().parent
    if (
        _sdk_root != (_PLUGIN_ROOT / "yomihime_game_link_sdk").resolve()
        or _sdk.__version__ != _SDK_VERSION
        or _sdk_version.MODULE_ABI_VERSION != _SDK_MODULE_ABI_VERSION
    ):
        raise RuntimeError("unsupported plugin-local SDK")
except Exception:
    raise RuntimeError("the pinned plugin-local SDK is unavailable") from None

from yomihime_game_link_sdk.declarations import ConfigUpdateMode
from yomihime_game_link_sdk.display import DisplayLimits

from ..adapters.astrbot.bundled import install_bundled_ff14
from ..adapters.astrbot.runtime import PLUGIN_NAME
from ..adapters.astrbot.trusted_assembly import assemble_reviewed
from ..core.contracts.administration import (
    AdminAuthorizationContext,
    AdminOperation,
    ConfigSummary,
)
from ..core.contracts.services import ConfigFieldUpdate, ConfigPatch, SecretMaterial
from ..core.contracts.validation_boundary import validate_contract
from ..extensions.factory_resolver import (
    ReviewedInventoryFactorySource,
    ReviewedSupportLease,
)
from ..infrastructure.http_transport import AioHttpTransport
from ..infrastructure.key_provider import EnvironmentKeyProvider
from ..infrastructure.secret_codec import AESGCMSecretCodec
from ..infrastructure.sqlite.database import SQLiteDatabase
from ..infrastructure.sqlite.repositories_admin_credentials import (
    AdminCredentialStatus,
)
from ..presentation.rendering import GenericDisplayRenderer, RenderingBounds
from ..services.admin_authorization import _matches
from ..services.core_runtime import CoreRuntime, CoreRuntimeCleanupPending
from .admin_credentials import _finish_cleanup, _read_hidden, local_maintenance_guard

MODULE_ID = "ff14/ff14"
_REALMS = ("cn", "global")
_SESSION_TTL_SECONDS = 5 * 60


@dataclass(frozen=True, slots=True, eq=False)
class _MaintenanceSession:
    """One unforgeable-by-value, short-lived local maintenance context."""

    adapter_id: str
    request_id: str
    session_id: str
    generation: int
    expires_at: float


class _SessionAuthority:
    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._session: _MaintenanceSession | None = None
        self._revoked = True

    def issue(self, generation: int) -> _MaintenanceSession:
        if type(generation) is not int or generation < 1:
            raise ValueError("admin generation is invalid")
        self._session = _MaintenanceSession(
            adapter_id="local-maintenance-cli",
            request_id=uuid4().hex,
            session_id=uuid4().hex,
            generation=generation,
            expires_at=self._clock() + _SESSION_TTL_SECONDS,
        )
        self._revoked = False
        return self._session

    def revoke(self) -> None:
        self._revoked = True
        self._session = None

    def validate(
        self,
        operation: AdminOperation,
        invocation: object,
        context: AdminAuthorizationContext,
        generation: int,
    ) -> bool:
        session = self._session
        return (
            not self._revoked
            and session is not None
            and context is session
            and invocation is None
            and operation
            in {AdminOperation.MODULE_SNAPSHOT, AdminOperation.UPDATE_CONFIG}
            and generation == session.generation
            and self._clock() < session.expires_at
        )


class _NoMessagePort:
    async def send(self, *_args, **_kwargs):
        raise RuntimeError("operator maintenance cannot send messages")


def _new_core(
    data_dir: Path,
    extension_root: Path,
    authority: _SessionAuthority,
    assembly,
    inventory,
):
    transport = AioHttpTransport()
    core = CoreRuntime(
        database=SQLiteDatabase(data_dir / "runtime.sqlite3"),
        extension_root=extension_root,
        file_root=data_dir / "files",
        secret_root=data_dir / "secrets",
        secret_codec=AESGCMSecretCodec(EnvironmentKeyProvider("YGL_SECRET_KEY")),
        http_transport=transport,
        renderer=GenericDisplayRenderer(
            RenderingBounds(
                max_chars_per_page=3000,
                max_lines_per_page=100,
                max_fields_per_block=32,
                max_rows_per_block=50,
                max_asset_read_bytes=1,
                max_image_dimension=1,
                max_asset_reads=1,
                max_blocks_per_document=32,
                max_members_per_batch=8,
            )
        ),
        display_limits=validate_contract(DisplayLimits(2, 4096)),
        message_port=_NoMessagePort(),
        admin_context_validator=authority.validate,
        host_ingress_validator=lambda *_args: False,
        config_principal_id=PLUGIN_NAME,
        identity_namespace=PLUGIN_NAME,
        factory_source=ReviewedInventoryFactorySource(inventory, selected=True),
        source_credential_policies=assembly.credential_policies,
        trusted_bundled_manifests=assembly.manifests,
        trusted_subscription_gates=assembly.gates,
        module_config_validators=assembly.validators,
        managed_source_credentials=assembly.managed_credentials,
    )
    return core, transport


async def _configure(
    data_dir: Path,
    action: str,
    realm: str,
    *,
    current_credential: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    clear_confirmation: str | None = None,
    plugin_root: Path = _PLUGIN_ROOT,
    prompt=getpass.getpass,
    label_sink=None,
) -> ConfigSummary:
    if action not in {"set", "clear"} or realm not in _REALMS:
        raise ValueError("unsupported operation")
    database_path = data_dir / "runtime.sqlite3"
    with local_maintenance_guard(database_path, plugin_root=plugin_root):
        if not data_dir.is_dir():
            raise ValueError("existing data directory is required")
        if not database_path.is_file():
            raise ValueError("existing Core database is required")

        installation = install_bundled_ff14(plugin_root, data_dir)
        if not installation.trusted:
            raise ValueError("trusted FF14 package is unavailable")
        support_lease = ReviewedSupportLease(
            installation.inventory.files, selected=True
        )
        try:
            assembly = assemble_reviewed(
                installation.inventory,
                principal_id=PLUGIN_NAME,
                support=support_lease.support,
                selected=True,
            )
            credential_alias, realm_label = assembly.credential_realms[realm]
        except BaseException:
            support_lease.release()
            raise
        authority = _SessionAuthority()
        try:
            core, transport = _new_core(
                data_dir,
                installation.extension_root,
                authority,
                assembly,
                installation.inventory,
            )
        except BaseException:
            support_lease.release()
            raise
        session: _MaintenanceSession | None = None
        try:
            await core.database.executor.initialize()
            state = await core.admin_credential_repository.current()
            if current_credential is None:
                current_credential = _read_hidden(
                    "Current independent admin credential (hidden): ", prompt=prompt
                )
            if state.status is not AdminCredentialStatus.ACTIVE or not _matches(
                current_credential, state
            ):
                raise PermissionError("operator authentication failed")
            session = authority.issue(state.generation)

            if action == "set":
                EnvironmentKeyProvider("YGL_SECRET_KEY").get_key()
                if client_id is None:
                    client_id = _read_hidden(
                        "FFLogs client ID (hidden): ", prompt=prompt
                    )
                if client_secret is None:
                    client_secret = _read_hidden(
                        "FFLogs client secret (hidden): ", prompt=prompt
                    )
                _validate_client_material(client_id, client_secret)
                payload = json.dumps(
                    {
                        "schema": 1,
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                update = ConfigFieldUpdate(
                    credential_alias,
                    ConfigUpdateMode.REPLACE,
                    secret=SecretMaterial(payload),
                )
            else:
                if clear_confirmation is None:
                    clear_confirmation = _read_hidden(
                        f"Type CLEAR to remove the {realm_label} FFLogs credential: ",
                        prompt=prompt,
                    )
                if clear_confirmation != "CLEAR":
                    raise ValueError("clear confirmation did not match")
                update = ConfigFieldUpdate(credential_alias, ConfigUpdateMode.CLEAR)

            core.extension_runtime.scan(installation.extension_root)
            failures = await core.admin_operations.recover_discovered_configuration()
            if any(failure.module_id == MODULE_ID for failure in failures):
                raise RuntimeError("module configuration recovery failed")
            candidate = core.extension_runtime.candidate("ff14")
            package_manifest = (
                candidate.package.manifest if candidate is not None else None
            )
            module_manifest = next(
                (
                    module
                    for module in getattr(package_manifest, "modules", ())
                    if module.module_id == "ff14"
                ),
                None,
            )
            if module_manifest is None:
                raise RuntimeError("FF14 manifest was not discovered")
            snapshot = await core.admin_facade.module_snapshot(
                None, MODULE_ID, authorization=session
            )
            if (
                snapshot.status.module_id != MODULE_ID
                or credential_alias not in snapshot.config.sensitive_fields
            ):
                raise RuntimeError("FFLogs credential field is not declared sensitive")
            updated = await core.admin_facade.update_config(
                None,
                MODULE_ID,
                ConfigPatch(
                    snapshot.config.revision,
                    (update,),
                    declared_fields=tuple(module_manifest.config_fields),
                ),
                authorization=session,
            )
            if credential_alias not in updated.sensitive_fields:
                raise RuntimeError("FFLogs credential field was not redacted")
            field_state = updated.fields.get(credential_alias)
            if action == "set" and field_state != "configured":
                raise RuntimeError("FFLogs credential was not stored")
            if action == "clear" and field_state == "configured":
                raise RuntimeError("FFLogs credential remains configured")
            if label_sink is not None:
                label_sink(realm_label)
            return updated
        finally:
            authority.revoke()
            transport_close_complete = False

            async def close_resources():
                closed = await core.close()
                if closed is not True:
                    raise RuntimeError("Core runtime did not close cleanly")

            async def close_transport():
                nonlocal transport_close_complete
                await transport.close()
                # Transport.closed is set before session.close awaits; only
                # a successfully completed close also proves that drain.
                transport_close_complete = True
                support_lease.release()

            await _finish_cleanup(
                close_resources,
                confirmed_closed=lambda: (
                    core.closed and transport.closed and transport_close_complete
                ),
                pending=(CoreRuntimeCleanupPending,),
                after=close_transport,
            )


def _validate_client_material(client_id: str, client_secret: str) -> None:
    for label, value, maximum in (
        ("client ID", client_id, 512),
        ("client secret", client_secret, 512),
    ):
        if (
            type(value) is not str
            or not value
            or len(value) > maximum
            or value != value.strip()
            or any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F for char in value)
        ):
            raise ValueError(f"{label} is invalid")


class _Parser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(
            2, "FFLogs credential CLI: invalid arguments; values are not accepted\n"
        )


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        required=True,
        type=Path,
        help="Existing AstrBot plugin data directory",
    )
    parser.add_argument(
        "--realm",
        required=True,
        choices=tuple(_REALMS),
        help="FFLogs realm: cn or global",
    )
    parser.add_argument("action", choices=("set", "clear"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        labels = []
        updated = asyncio.run(
            _configure(args.data_dir, args.action, args.realm, label_sink=labels.append)
        )
    except Exception:
        print("FFLogs 凭据操作失败；配置未确认完成。", file=sys.stderr)
        return 1
    label = labels[0] if labels else args.realm
    verb = "已更新" if args.action == "set" else "已清除"
    print(f"FFLogs {label}凭据{verb}，配置版本 {updated.revision}。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
