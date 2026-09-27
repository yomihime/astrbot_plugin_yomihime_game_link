"""Safe, scope-derived file storage for registered resources."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ..api.storage import OwnerScope, ResourceMetadata
from ..core.ports import FileStage


def _bounded(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(char in value for char in ("/", "\\", "\n", "\r"))
        or ".." in value
    ):
        raise ValueError(f"{field} is invalid")
    return value


def _scope_token(scope: OwnerScope) -> str:
    scope = OwnerScope.validate(scope)
    grant = (
        "" if scope.grant is None else f":{scope.grant.grant_id}:{scope.grant.revision}"
    )
    raw = f"{scope.kind.value}:{scope.user_id or ''}{grant}".encode()
    return hashlib.sha256(raw).hexdigest()


class LocalSafeFileStore:
    """Store bytes below one caller-provided root with no caller path input."""

    def __init__(self, root: str | Path):
        if not isinstance(root, (str, Path)) or not str(root).strip():
            raise ValueError("file root must be non-empty")
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._staging = self.root / ".staging"
        self._staging.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _asset(asset_id: str) -> str:
        _bounded(asset_id, "asset_id")
        if not asset_id.startswith("asset_"):
            raise ValueError("asset id is invalid")
        return asset_id

    def _final(self, asset_id: str, scope: OwnerScope) -> Path:
        asset_id = self._asset(asset_id)
        scope = OwnerScope.validate(scope)
        return self.root / scope.kind.value / _scope_token(scope) / asset_id

    def _temp(self, operation_id: str, asset_id: str) -> Path:
        _bounded(operation_id, "operation_id")
        self._asset(asset_id)
        digest = hashlib.sha256(operation_id.encode()).hexdigest()
        return self._staging / f"{digest}_{asset_id}.tmp"

    def _pending(self, asset_id: str, scope: OwnerScope) -> Path:
        return self._final(asset_id, scope).with_suffix(".pending")

    def _orphan(self, asset_id: str, scope: OwnerScope) -> Path:
        return self._final(asset_id, scope).with_suffix(".orphan")

    async def stage(
        self, operation_id: str, content: bytes, scope: OwnerScope
    ) -> FileStage:
        _bounded(operation_id, "operation_id")
        if not isinstance(content, bytes):
            raise TypeError("resource content must be bytes")
        scope = OwnerScope.validate(scope)
        digest = hashlib.sha256(
            f"{_scope_token(scope)}\0".encode() + content
        ).hexdigest()
        asset_id = f"asset_{digest}"
        path = self._temp(operation_id, asset_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != content:
                raise FileExistsError("staging operation already has different content")
        else:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            descriptor = os.open(path, flags, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
            except Exception:
                try:
                    path.unlink()
                except OSError:
                    pass
                raise
        return FileStage(operation_id, asset_id, scope, len(content))

    async def commit(
        self, stage: FileStage, metadata: ResourceMetadata
    ) -> ResourceMetadata:
        if not isinstance(stage, FileStage):
            raise TypeError("stage must be FileStage")
        metadata = ResourceMetadata(
            metadata.asset_id,
            metadata.media_type,
            metadata.scope,
            metadata.size_bytes,
            metadata.expires_at,
            metadata.temporary,
            metadata.revision,
        )
        if metadata.asset_id != stage.asset_id or metadata.scope != stage.scope:
            raise ValueError("file stage metadata does not match")
        if metadata.size_bytes != stage.size_bytes:
            raise ValueError("resource size does not match staged bytes")
        source = self._temp(stage.operation_id, stage.asset_id)
        if not source.is_file():
            raise FileNotFoundError("staged resource is unavailable")
        target = self._final(stage.asset_id, stage.scope)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != source.read_bytes():
                raise FileExistsError("asset id already contains different bytes")
            source.unlink()
        else:
            # The marker is durable before the rename.  A process crash after
            # commit and before SQLite registration therefore remains
            # discoverable after restart.
            self._pending(stage.asset_id, stage.scope).write_text(
                "index pending", encoding="utf-8"
            )
            os.replace(source, target)
        return metadata

    async def confirm(self, metadata: ResourceMetadata) -> None:
        """Clear the commit marker after the corresponding index commit."""
        metadata = ResourceMetadata(
            metadata.asset_id,
            metadata.media_type,
            metadata.scope,
            metadata.size_bytes,
            metadata.expires_at,
            metadata.temporary,
            metadata.revision,
        )
        marker = self._pending(metadata.asset_id, metadata.scope)
        try:
            marker.unlink()
        except FileNotFoundError:
            return

    async def read(self, asset_id: str, scope: OwnerScope) -> bytes:
        path = self._final(asset_id, scope)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise FileNotFoundError("resource file is unavailable") from exc

    async def discard(self, stage: FileStage) -> None:
        if not isinstance(stage, FileStage):
            raise TypeError("stage must be FileStage")
        try:
            self._temp(stage.operation_id, stage.asset_id).unlink()
        except FileNotFoundError:
            return

    async def mark_orphan(self, stage: FileStage, reason: str) -> None:
        if not isinstance(stage, FileStage):
            raise TypeError("stage must be FileStage")
        _bounded(reason, "orphan reason")
        source = self._temp(stage.operation_id, stage.asset_id)
        if source.exists():
            source.with_suffix(".orphan").write_text("orphan", encoding="utf-8")
            return
        pending = self._pending(stage.asset_id, stage.scope)
        target = self._final(stage.asset_id, stage.scope)
        if pending.exists() and target.exists():
            pending.replace(self._orphan(stage.asset_id, stage.scope))

    async def recover_orphans(self) -> tuple[str, ...]:
        staged = tuple(path.name for path in self._staging.glob("*.orphan"))
        committed = tuple(
            path.name
            for path in self.root.rglob("*.orphan")
            if path.parent != self._staging
        )
        pending = tuple(
            path.name
            for path in self.root.rglob("*.pending")
            if path.parent != self._staging
        )
        return tuple(sorted(staged + committed + pending))


SafeFileStore = LocalSafeFileStore
SQLiteFileStore = LocalSafeFileStore

__all__ = ["LocalSafeFileStore", "SQLiteFileStore", "SafeFileStore"]
