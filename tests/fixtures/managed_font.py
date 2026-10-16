"""Explicit offline font manager for Host fixtures; never opens a network socket."""

from pathlib import Path

from ygl_test_subject.presentation.bundled_font import FONT_FILENAME
from ygl_test_subject.services.managed_font import ManagedFont


class OfflineManagedFont(ManagedFont):
    def __init__(self, root):
        super().__init__(root)
        self._reference = (
            Path(__file__).resolve().parents[2]
            / "presentation/assets/fonts"
            / FONT_FILENAME
        ).read_bytes()
        # Some exact-event fixtures bypass initialize and inject a ready Core.
        self._provider = self._canonical(self._reference)
        self._source = "canonical"

    async def _download(self):
        self._install(self._reference)
