"""Keep compiled build-tree residue out of the reproducible SDK wheel."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import tempfile
import zipfile
from pathlib import Path
from shutil import rmtree

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py
from wheel.bdist_wheel import bdist_wheel as _bdist_wheel


def _wheel_payloads(
    entries: list[zipfile.ZipInfo], archive: zipfile.ZipFile
) -> tuple[dict[str, bytes], str, bytes]:
    names = [entry.filename for entry in entries]
    if len(names) != len(set(names)):
        raise ValueError("SDK wheel contains duplicate entry names")
    metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
    record_names = [name for name in names if name.endswith(".dist-info/RECORD")]
    if len(metadata_names) != 1 or len(record_names) != 1:
        raise ValueError("SDK wheel must contain one METADATA and one RECORD")

    metadata_name = metadata_names[0]
    record_name = record_names[0]
    payloads = {name: archive.read(name) for name in names if name != record_name}
    # Keep the carriage-return byte value-constructed because setuptools' PEP 517
    # backend rewrites a literal backslash-r/backslash-n sequence in setup.py.
    crlf = bytes((13, 10))
    metadata = payloads[metadata_name].replace(crlf, b"\n").replace(b"\n", crlf)
    payloads[metadata_name] = metadata

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for name in names:
        if name == record_name:
            writer.writerow((name, "", ""))
            continue
        data = payloads[name]
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest())
        writer.writerow(
            (name, f"sha256={digest.decode('ascii').rstrip('=')}", str(len(data)))
        )
    return payloads, record_name, output.getvalue().encode("utf-8")


def normalize_wheel_archive(wheel_path: Path) -> bool:
    """Normalize wheel metadata and ZIP attributes without changing payloads.

    The SDK wheel is intentionally pinned to its reviewed Windows-built bytes.
    Setuptools emits host-specific ZIP attributes and line endings, so canonical
    equivalents are written here before the existing digest check is applied.
    """
    wheel_path = Path(wheel_path)
    temporary_path: Path | None = None
    try:
        with zipfile.ZipFile(wheel_path, "r") as source:
            entries = source.infolist()
            payloads, record_name, record_data = _wheel_payloads(entries, source)
            canonical_payloads = {**payloads, record_name: record_data}
            already_canonical = all(
                entry.create_system == 0
                and entry.external_attr
                == ((0o100664 if entry.filename == record_name else 0o100666) << 16)
                for entry in entries
            ) and all(
                source.read(entry.filename) == canonical_payloads[entry.filename]
                for entry in entries
            )
            if already_canonical:
                return False

            wheel_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                prefix=f"{wheel_path.name}.",
                suffix=".normalized",
                dir=wheel_path.parent,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)

            with zipfile.ZipFile(temporary_path, "w") as destination:
                destination.comment = source.comment
                for entry in entries:
                    normalized = zipfile.ZipInfo(entry.filename, entry.date_time)
                    normalized.compress_type = entry.compress_type
                    normalized.comment = entry.comment
                    normalized.extra = entry.extra
                    normalized.internal_attr = entry.internal_attr
                    normalized.create_system = 0
                    mode = 0o100664 if entry.filename == record_name else 0o100666
                    normalized.external_attr = mode << 16
                    destination.writestr(normalized, canonical_payloads[entry.filename])
        temporary_path.replace(wheel_path)
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    return True


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


class ReproducibleBdistWheel(_bdist_wheel):
    """Apply the reviewed cross-platform wheel representation after building."""

    def run(self) -> None:
        super().run()
        python_tag, abi_tag, platform_tag = self.get_tag()
        wheel_name = f"{self.wheel_dist_name}-{python_tag}-{abi_tag}-{platform_tag}.whl"
        normalize_wheel_archive(Path(self.dist_dir) / wheel_name)


if __name__ == "__main__":
    setup(
        cmdclass={
            "bdist_wheel": ReproducibleBdistWheel,
            "build_py": CleanBytecodeBuildPy,
        }
    )
