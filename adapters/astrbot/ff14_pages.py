"""Deprecated import alias for the generic public adapter.

Old overview/settings routes are compatibility locators without config values.
"""

from .public_pages import PublicPages

FF14Pages = PublicPages
__all__ = ["FF14Pages"]
