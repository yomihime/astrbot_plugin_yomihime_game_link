"""The AstrBot ordinary Dashboard request contract gate, without JWT parsing."""

from __future__ import annotations

import json
import re
from time import time

from ...core.contracts.administration import AdminAuthorizationDenied
from .web_public import normalize_origin

BODY_LIMIT = 8192


def verified_dashboard_request(request, exact_path):
    """Call the real request's bound DashboardServer verifier; never read its key."""
    if request.method != "POST" or request.path != exact_path:
        raise AdminAuthorizationDenied
    try:
        raw = request._request
        # Management follows the actual Host request origin. Public-query
        # configuration is independent and may be unavailable during recovery.
        origin = normalize_origin(f"{raw.url.scheme}://{raw.url.netloc}")
    except Exception:
        raise AdminAuthorizationDenied from None
    headers = request.headers
    auth = headers.getlist("authorization")
    origins = headers.getlist("origin")
    if len(auth) != 1 or len(origins) != 1 or headers.getlist("x-api-key"):
        raise AdminAuthorizationDenied
    if normalize_origin(origins[0]) != origin:
        raise AdminAuthorizationDenied
    match = re.fullmatch(
        r"Bearer ([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)", auth[0]
    )
    if match is None or len(match[1]) > 8192:
        raise AdminAuthorizationDenied
    try:
        server = raw.app.state.dashboard_app_adapter._dashboard_server
        verifier = getattr(server, "_validate_dashboard_token", None)
        if not callable(verifier) or getattr(verifier, "__self__", None) is not server:
            raise AdminAuthorizationDenied
        payload, error = verifier(match[1], exact_path)
        if (
            error
            or type(payload) is not dict
            or "token_type" in payload
            or type(payload.get("exp")) is not int
            or payload["exp"] <= time()
            or type(payload.get("username")) is not str
            or not payload["username"].strip()
            or payload["username"] != request.username
            or raw.state.dashboard_g.username != request.username
            or raw.method != request.method
            or raw.url.path != exact_path
        ):
            raise AdminAuthorizationDenied
        return raw, payload["username"], min(float(payload["exp"]), time() + 30)
    except AdminAuthorizationDenied:
        raise
    except Exception:
        raise AdminAuthorizationDenied from None


async def bounded_body(request):
    headers = request.headers
    if headers.getlist("content-type") not in (
        ["application/json"],
        ["application/json; charset=utf-8"],
    ):
        raise ValueError("invalid management body")
    lengths = headers.getlist("content-length")
    if lengths and (
        len(lengths) != 1 or not lengths[0].isdigit() or int(lengths[0]) > BODY_LIMIT
    ):
        raise ValueError("invalid management body")
    body = bytearray()
    async for chunk in request._request.stream():
        body.extend(chunk)
        if len(body) > BODY_LIMIT:
            raise ValueError("management body is too large")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate management field")
            result[key] = value
        return result

    data = json.loads(
        body,
        object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid number")),
    )
    if type(data) is not dict:
        raise ValueError("management body requires an object")
    return data


async def bounded_font_upload(request):
    """Strict browser FormData single-file envelope with bounded allocation.

    The filename is ignored and is never interpreted as a filesystem path.
    Authentication must be established before calling this reader.
    """
    from ...presentation.bundled_font import FONT_SIZE

    types = request.headers.getlist("content-type")
    if len(types) != 1:
        raise ValueError("invalid upload")
    match = re.fullmatch(
        r"multipart/form-data; boundary=([A-Za-z0-9_-]{1,70})", types[0]
    )
    if match is None:
        raise ValueError("invalid upload")
    boundary = match[1].encode("ascii")
    limit = FONT_SIZE + 8192
    lengths = request.headers.getlist("content-length")
    if lengths and (
        len(lengths) != 1 or not lengths[0].isdigit() or int(lengths[0]) > limit
    ):
        raise ValueError("invalid upload")
    body = bytearray()
    async for chunk in request._request.stream():
        if len(body) + len(chunk) > limit:
            raise ValueError("invalid upload")
        body.extend(chunk)
    prefix, suffix = b"--" + boundary + b"\r\n", b"\r\n--" + boundary + b"--\r\n"
    if not body.startswith(prefix) or not body.endswith(suffix):
        raise ValueError("invalid upload")
    header_end = body.find(b"\r\n\r\n", len(prefix), len(prefix) + 4096)
    if header_end < 0:
        raise ValueError("invalid upload")
    headers = bytes(body[len(prefix) : header_end]).decode("ascii")
    header_match = re.fullmatch(
        r'Content-Disposition: form-data; name="file"; filename="[^"\r\n]{1,255}"'
        r"\r\nContent-Type: (?:application/octet-stream|font/otf|application/vnd.ms-opentype|application/x-font-opentype)",
        headers,
        re.IGNORECASE,
    )
    if header_match is None:
        raise ValueError("invalid upload")
    content = bytes(body[header_end + 4 : -len(suffix)])
    if len(content) != FONT_SIZE:
        raise ValueError("invalid upload")
    return {"content": content}
