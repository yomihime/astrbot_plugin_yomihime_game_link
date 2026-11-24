"""Trusted exchange of an opaque confirmation for a nonce-bound JWT access token."""

import asyncio
import json
import re
import secrets
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

import aiohttp
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..core.contracts.services import Grant, GrantStatus
from ..core.ports import SecretOwner
from .player_source import PlayerReadPolicy


@dataclass(frozen=True, slots=True)
class PlayerTicketIssuer:
    """Configured by trusted Host composition; the module sees only one-time codes."""

    policy: PlayerReadPolicy
    issuer: str
    endpoint: str
    client_id: str
    scope: str
    public_key: bytes
    tls: ssl.SSLContext | None = None

    def __post_init__(self):
        origin, url = urlsplit(self.issuer), urlsplit(self.endpoint)
        if (
            origin.scheme != "https"
            or not origin.hostname
            or not re.fullmatch(r"[a-z][a-z0-9.-]*[.][a-z]{2,63}", origin.hostname)
            or origin.path not in ("", "/")
            or origin.username is not None
            or origin.password is not None
            or origin.query
            or origin.fragment
            or (url.scheme, url.netloc) != (origin.scheme, origin.netloc)
            or not re.fullmatch(r"/[A-Za-z0-9_/-]+", url.path)
            or url.query
            or url.fragment
            or len(self.public_key) != 32
            or not all(
                re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", x)
                for x in (self.scope, self.client_id)
            )
        ):
            raise ValueError("invalid player issuer configuration")
        if self.tls is not None and (
            not self.tls.check_hostname or self.tls.verify_mode != ssl.CERT_REQUIRED
        ):
            raise ValueError("issuer requires verified HTTPS")

    def verifier(self, store, grants):
        return BoundPlayerIssuer(self, store, grants)


class BoundPlayerIssuer:
    def __init__(self, config, store, grants):
        # The base host does not require a ticket verifier.
        from jwt import decode

        self.config, self.store, self.policy = config, store, config.policy
        self._grants = grants
        self._decode = decode

    async def __call__(self, session, confirmation):
        cfg = self.config
        if (
            session.module_id != self.policy.module_id
            or type(confirmation) is not str
            or not re.fullmatch(r"[A-Za-z0-9_-]{20,160}", confirmation)
        ):
            raise ValueError("invalid player confirmation")
        async with aiohttp.ClientSession(
            cookie_jar=aiohttp.DummyCookieJar(),
            trust_env=False,
            auto_decompress=False,
            timeout=aiohttp.ClientTimeout(total=5),
        ) as client:
            async with client.post(
                cfg.endpoint,
                data={
                    "code": confirmation,
                    "nonce": session.session_id,
                    "client_id": cfg.client_id,
                },
                ssl=cfg.tls,
                allow_redirects=False,
            ) as response:
                if (
                    response.status != 200
                    or response.content_type != "application/json"
                ):
                    raise ValueError("player verification rejected")
                data = bytearray()
                async for chunk in response.content.iter_chunked(4096):
                    if len(data) + len(chunk) > 16384:
                        raise ValueError("player verification rejected")
                    data.extend(chunk)
        body = json.loads(bytes(data))
        token = body["access_token"]
        if (
            body.get("token_type") != "Bearer"
            or type(token) is not str
            or len(token) > 2048
            or not re.fullmatch(
                r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token
            )
        ):
            raise ValueError("player verification rejected")
        audience = "https://" + self.policy.host
        claims = self._decode(
            token,
            Ed25519PublicKey.from_public_bytes(cfg.public_key),
            algorithms=["EdDSA"],
            issuer=cfg.issuer,
            audience=audience,
            options={
                "require": [
                    "iss",
                    "aud",
                    "exp",
                    "iat",
                    "sub",
                    "nonce",
                    "client_id",
                    "scope",
                ],
                "strict_aud": True,
            },
        )
        if (
            claims["nonce"] != session.session_id
            or claims["client_id"] != cfg.client_id
            or claims["scope"] != cfg.scope
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", claims["sub"])
            or type(claims["exp"]) is not int
            or type(claims["iat"]) is not int
            or not 0 < claims["exp"] - claims["iat"] <= 3600
        ):
            raise ValueError("player verification rejected")
        owner = SecretOwner(
            session.principal_id,
            session.module_id,
            self.policy.field,
            f"{session.session_id}.{secrets.token_hex(16)}",
        )
        return await self._stage_grant(owner, claims, token)

    async def _stage_grant(self, owner, claims, token):
        try:
            receipt = await self.store.put(token.encode("ascii"), owner=owner)
            return Grant(
                secrets.token_hex(16),
                1,
                owner.principal_id,
                owner.module_id,
                claims["sub"],
                (self.policy.scope,),
                receipt.secret_ref,
                GrantStatus.ACTIVE,
                datetime.fromtimestamp(claims["exp"], UTC),
            )
        except BaseException as failure:
            await self.recover_failed_attempt(owner, failure)
            raise

    async def recover_failed_attempt(self, owner, failure):
        """Drain compensating work for only this issuance, even on repeated cancellation."""

        async def recover():
            # A new private operation ID is allocated for every exchange. No other
            # confirmation can obtain its receipt or commit a future grant using it.
            transitions = await self.store.pending(owner)
            if not transitions:
                return
            rows = await self._grants.list_for(owner.principal_id, owner.module_id)
            for transition in transitions:
                if not any(row.secret_ref == transition.secret_ref for row in rows):
                    await self.store.recover(transition, owner=owner)

        cleanup = asyncio.create_task(recover())
        cancelled = None
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError as exc:
                cancelled = exc
            except Exception:
                break
        try:
            cleanup.result()
        except Exception:
            # The original ledger keeps the exact private owner on storage errors.
            if isinstance(failure, asyncio.CancelledError) or cancelled is not None:
                cancellation = cancelled or failure
                cancellation.add_note("Player credential cleanup requires recovery.")
                raise cancellation from None
            raise
        if cancelled is not None:
            raise cancelled
