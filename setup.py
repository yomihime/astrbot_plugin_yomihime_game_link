"""Keep compiled build-tree residue out of the reproducible SDK wheel."""

from __future__ import annotations

from pathlib import Path
from shutil import rmtree

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py


class CleanBytecodeBuildPy(_build_py):
    """Remove bytecode caches left by compileall from setuptools build output."""

    def run(self) -> None:
        super().run()
        build_lib = Path(self.build_lib)
        if not build_lib.exists():
            return
        for cache_dir in build_lib.rglob("__pycache__"):
            rmtree(cache_dir)
        for bytecode in build_lib.rglob("*.pyc"):
            bytecode.unlink()


setup(cmdclass={"build_py": CleanBytecodeBuildPy})
