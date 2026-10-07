"""Generic assembly adapter; the Host selects a reviewed release explicitly."""

from ...services.trusted_assembly import LegacyAssemblySupport, assemble_reviewed

__all__ = ["LegacyAssemblySupport", "assemble_reviewed"]
