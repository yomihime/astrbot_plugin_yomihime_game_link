"""Deterministic upload/download ownership and failure recovery; no sockets."""

import asyncio
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from ygl_test_subject.services import managed_font as fonts

from tests.services import test_managed_font as fixture


class UploadRecoveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ManagedFontTests.asyncSetUp
    asyncTearDown = fixture.ManagedFontTests.asyncTearDown

    async def failed_upload(self, kind, source="system"):
        entered = asyncio.Event()

        class Blocked(fixture.DownloadResponse):
            async def __aenter__(self):
                entered.set()
                await asyncio.Event().wait()

        self.manager._closed = False
        if source == "canonical":
            self.manager._install(self.content)
        else:
            self.manager._provider = object() if source == "system" else None
            self.manager._source = source
            self.root.mkdir(parents=True)
            self.manager._path.write_bytes(self.content)
        provider = self.manager._provider
        session = fixture.DownloadSession([Blocked(), *[OSError("offline")] * 3])

        def check():
            if kind == "authorization":
                raise PermissionError("synthetic denial")

        with patch("aiohttp.ClientSession", session):
            self.manager._task = asyncio.create_task(self.manager._download())
            await entered.wait()
            with ExitStack() as faults:
                if kind == "parse":
                    faults.enter_context(
                        patch.object(
                            fonts.BundledFontProvider,
                            "load",
                            side_effect=OSError("private parser path"),
                        )
                    )
                elif kind == "write":
                    faults.enter_context(
                        patch.object(
                            fonts.os,
                            "replace",
                            side_effect=OSError("private write path"),
                        )
                    )
                with self.assertRaises((PermissionError, RuntimeError, OSError)):
                    await self.manager.upload(self.content, check)
            self.assertTrue(self.manager._task.done())
            self.assertEqual(
                self.manager.status()["state"],
                "ready" if source == "canonical" else "unavailable",
            )
            self.assertEqual(self.manager.status()["source"], source)
            self.assertIs(self.manager._provider, provider)
            self.assertEqual(self.manager._path.read_bytes(), self.content)
            self.assertFalse(list(self.root.glob(".font-*")))
            self.assertNotIn("private", repr(self.manager.status()))
            if source != "canonical":
                previous = self.manager._task
                self.manager.retry()
                self.assertIsNot(self.manager._task, previous)
                await self.manager._task
                self.assertEqual(self.manager.status()["state"], "unavailable")
                self.assertEqual(len(session.calls), 4)

    async def test_authorization_failure_recovers_after_cancelling_download(self):
        await self.failed_upload("authorization")

    async def test_parse_failure_recovers_after_cancelling_download(self):
        await self.failed_upload("parse")

    async def test_write_failure_recovers_after_cancelling_download(self):
        await self.failed_upload("write")

    async def test_failed_upload_keeps_existing_canonical_provider_ready(self):
        await self.failed_upload("write", "canonical")

    async def test_failed_upload_without_provider_retains_text_fallback(self):
        await self.failed_upload("parse", "none")

    async def test_upload_caller_cancellation_during_download_join_is_not_swallowed(
        self,
    ):
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Blocked(fixture.DownloadResponse):
            async def __aenter__(self):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaning.set()
                    await release.wait()

        self.manager._closed = False
        checks = []
        with patch("aiohttp.ClientSession", fixture.DownloadSession([Blocked()])):
            self.manager._task = asyncio.create_task(self.manager._download())
            await entered.wait()
            upload = asyncio.create_task(
                self.manager.upload(self.content, lambda: checks.append(True))
            )
            try:
                await cleaning.wait()
                upload.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await upload
                self.assertEqual(checks, [])
                self.assertFalse(self.manager._path.exists())
                self.assertEqual(self.manager.status()["state"], "unavailable")
            finally:
                release.set()
                await asyncio.gather(upload, return_exceptions=True)

    async def test_retry_cannot_start_a_new_writer_before_upload_publication(self):
        entered = asyncio.Event()

        class Blocked(fixture.DownloadResponse):
            async def __aenter__(self):
                entered.set()
                await asyncio.Event().wait()

        self.manager._closed = False
        session = fixture.DownloadSession(
            [Blocked(), fixture.DownloadResponse(self.content)]
        )
        with patch("aiohttp.ClientSession", session):
            task = self.manager._task = asyncio.create_task(self.manager._download())
            await entered.wait()
            task.add_done_callback(lambda _: self.manager.retry())
            result = await self.manager.upload(self.content, lambda: None)
            await asyncio.sleep(0)
            await asyncio.gather(self.manager._task, return_exceptions=True)
        self.assertEqual(result["state"], "ready")
        self.assertIs(self.manager._task, task)
        self.assertEqual(len(session.calls), 1)

    async def test_close_and_restart_fence_old_download_and_old_close_finalizer(self):
        entered, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Late(fixture.DownloadResponse):
            async def __aenter__(self):
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelling.set()
                    await release.wait()
                return self

        self.manager._closed = False
        replacement = object()

        async def initialize():
            self.manager._provider, self.manager._source = replacement, "system"
            self.manager._change("unavailable", "download_connection")

        with patch(
            "aiohttp.ClientSession", fixture.DownloadSession([Late(self.content)])
        ):
            self.manager._task = asyncio.create_task(self.manager._download())
            await entered.wait()
            closing = asyncio.create_task(self.manager.close())
            try:
                await cancelling.wait()
                with patch.object(self.manager, "_initialize", initialize):
                    self.manager.start()
                    current = self.manager._task
                    await current
                release.set()
                await closing
                self.assertIs(self.manager._task, current)
                self.assertIs(self.manager.provider(), replacement)
                self.assertEqual(self.manager.status()["source"], "system")
                self.assertFalse(self.manager._path.exists())
            finally:
                release.set()
                await closing

    async def test_queued_uploads_still_serialize_within_one_lifecycle(self):
        self.manager._closed = False
        await self.manager._upload_lock.acquire()
        checks = []
        tasks = [
            asyncio.create_task(
                self.manager.upload(self.content, lambda i=i: checks.append(i))
            )
            for i in (1, 2)
        ]
        self.manager._upload_lock.release()
        results = await asyncio.gather(*tasks)
        self.assertEqual(checks, [1, 2])
        self.assertTrue(all(result["state"] == "ready" for result in results))
