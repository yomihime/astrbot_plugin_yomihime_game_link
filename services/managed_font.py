"""Core-owned pinned font resource, separate from plugin installation files."""

import asyncio
import os
import stat
import tempfile
from contextlib import suppress
from hashlib import sha256
from pathlib import Path

from ..presentation.bundled_font import (
    FONT_FILENAME,
    FONT_SHA256,
    FONT_SIZE,
    BundledFontProvider,
)
from ..presentation.card_bitmap import BitmapUnavailable
from ..presentation.system_font import SystemFontProvider

FONT_URL = (
    "https://raw.githubusercontent.com/notofonts/noto-cjk/"
    "523d033d6cb47f4a80c58a35753646f5c3608a78/"
    "Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf"
)
FONT_URLS = (
    "https://mirrors.tuna.tsinghua.edu.cn/CTAN/fonts/notocjksc/"
    "NotoSansCJKsc-Regular.otf",
    "https://cdn.jsdelivr.net/gh/notofonts/noto-cjk@"
    "523d033d6cb47f4a80c58a35753646f5c3608a78/"
    "Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf",
    FONT_URL,
)
DOWNLOAD_TOTAL_SECONDS = 45
DOWNLOAD_SOURCE_SECONDS = 15


class _DownloadFailure(ValueError):
    """Only fixed safe codes cross the downloader's error boundary."""


class _FontParseFailure(RuntimeError):
    """Validated bytes could not be loaded as a usable pinned font face."""


class ManagedFont:
    def __init__(self, root: Path):
        self._root = Path(root)
        self._path = self._root / FONT_FILENAME
        self._provider = None
        self._task = None
        self._closed = True
        self._revision = 0
        self._generation = 0
        self._upload_lock = asyncio.Lock()
        self._state, self._source, self._error = "stopped", "none", None

    def provider(self):
        if self._provider is None:
            raise BitmapUnavailable("font_unavailable")
        return self._provider

    def status(self):
        return dict(
            revision=self._revision,
            state=self._state,
            source=self._source,
            error=self._error,
            filename=FONT_FILENAME,
            size=FONT_SIZE,
            sha256=FONT_SHA256,
            download_url=FONT_URL,
        )

    def _change(self, state, error=None):
        self._state, self._error = state, error
        self._revision += 1

    def start(self):
        if not self._closed:
            return
        self._closed = False
        self._generation += 1
        self._change("checking")
        self._task = asyncio.create_task(self._initialize(), name="ygl:managed-font")

    async def _initialize(self):
        try:
            try:
                content = self._read()
                self._provider = self._canonical(content)
                self._source = "canonical"
                self._change("ready")
                return
            except Exception:
                pass
            try:
                self._provider = SystemFontProvider()
                self._source = "system"
            except BitmapUnavailable:
                self._provider, self._source = None, "none"
            await self._download()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._change("unavailable", "resource_unavailable")

    def _safe_root(self, *, create=False):
        for directory in (self._root.parent, self._root):
            if directory.is_symlink():
                raise ValueError("invalid_resource_directory")
            if create:
                directory.mkdir(parents=True, exist_ok=True)
            if not directory.is_dir():
                raise ValueError("invalid_resource_directory")

    def _read(self):
        self._safe_root()
        if not stat.S_ISREG(self._path.lstat().st_mode):
            raise ValueError("invalid_resource_file")
        with self._path.open("rb") as stream:
            content = stream.read(FONT_SIZE + 1)
        self._verify(content)
        return content

    @staticmethod
    def _verify(content):
        if len(content) != FONT_SIZE or sha256(content).hexdigest() != FONT_SHA256:
            raise ValueError("font_integrity_failed")

    @staticmethod
    def _canonical(content):
        ManagedFont._verify(content)
        try:
            provider = object.__new__(BundledFontProvider)
            provider._font_bytes = content
            provider.load(16).getmask("字体 Ag")
        except Exception:
            # Missing drawing dependencies and face/glyph parsing happen before
            # any directory or temporary file is created. Never call them write
            # failures or expose the parser's paths/errors to status consumers.
            raise _FontParseFailure("font_parse_failed") from None
        return provider

    def _install(self, content):
        provider = self._canonical(content)
        self._safe_root(create=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self._root, prefix=".font-", delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self._provider, self._source = provider, "canonical"
        self._change("ready")

    async def _download(self):
        self._change("downloading")
        error = "download_connection"
        try:
            import aiohttp

            deadline = asyncio.get_running_loop().time() + DOWNLOAD_TOTAL_SECONDS
            # Fixed HTTPS allowlist only; no redirects, proxies or decompression.
            # Each source gets at most one third of the round, leaving time for
            # both backups even when the primary consumes its entire budget.
            async with asyncio.timeout_at(deadline):
                async with aiohttp.ClientSession(
                    trust_env=False, auto_decompress=False
                ) as session:
                    for url in FONT_URLS:
                        remaining = deadline - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            raise TimeoutError
                        budget = min(DOWNLOAD_SOURCE_SECONDS, remaining)
                        timeout = aiohttp.ClientTimeout(
                            total=budget,
                            connect=min(5, budget),
                            sock_read=min(5, budget),
                        )
                        try:
                            # The explicit timeout also bounds non-aiohttp test
                            # transports and response context teardown.
                            async with asyncio.timeout(budget):
                                content = await self._download_source(
                                    session, url, timeout
                                )
                        except TimeoutError:
                            error = "download_timeout"
                            continue
                        except _DownloadFailure as exc:
                            error = str(exc)
                            continue
                        except (aiohttp.ClientError, OSError):
                            error = "download_connection"
                            continue
                        if self._closed:
                            return
                        try:
                            self._install(content)
                        except _FontParseFailure:
                            error = "download_fontparse"
                            break
                        except Exception:
                            # The same validated bytes from another source will
                            # not fix a local publication failure.
                            error = "download_write"
                            break
                        return
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            error = "download_timeout"
        except Exception:
            error = "download_connection"
        if not self._closed:
            self._change("unavailable", error)

    @staticmethod
    async def _download_source(session, url, timeout):
        async with session.get(url, timeout=timeout, allow_redirects=False) as response:
            if (
                response.status != 200
                or response.headers.get("Content-Encoding", "identity") != "identity"
            ):
                raise _DownloadFailure("download_http")
            if response.content_length not in (None, FONT_SIZE):
                raise _DownloadFailure("download_length")
            content = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                if len(content) + len(chunk) > FONT_SIZE:
                    raise _DownloadFailure("download_length")
                content.extend(chunk)
            if len(content) != FONT_SIZE:
                raise _DownloadFailure("download_length")
            if sha256(content).hexdigest() != FONT_SHA256:
                raise _DownloadFailure("download_digest")
            return bytes(content)

    def retry(self):
        if self._closed:
            raise ValueError("font_manager_closed")
        if self._source != "canonical" and (self._task is None or self._task.done()):
            self._change("downloading")
            self._task = asyncio.create_task(self._download(), name="ygl:managed-font")
        return self.status()

    async def upload(self, content, check):
        generation = self._generation
        self._verify(content)
        async with self._upload_lock:
            if self._closed or generation != self._generation:
                raise ValueError("font_manager_closed")
            # Join the only background writer before publication. Recheck the
            # Host capability and lifecycle after the last await before commit.
            if self._task is not None and not self._task.done():
                self._task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._task
            check()
            if self._closed or generation != self._generation:
                raise ValueError("font_manager_closed")
            self._install(content)
            return self.status()

    async def close(self):
        self._closed = True
        self._generation += 1
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        self._provider, self._source = None, "none"
        self._change("stopped")
