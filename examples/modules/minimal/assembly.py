"""Reviewed Host support selected explicitly; never imported by manifest text."""

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class ReviewedSupport:
    module_id: str = "minimal/demo"
    validators: tuple = ()
    ordinary_config_fields: tuple = ("enabled",)
    metadata: object = None

    def with_metadata(self, metadata):
        return replace(self, metadata=metadata)

    @staticmethod
    def query_parameters(endpoint, body):
        if endpoint != "status" or body not in (b"{}", b""):
            raise ValueError("request_rejected")
        return {}

    @staticmethod
    def ordinary_snapshot(values, defaults=None):
        del defaults
        return {"enabled": values.get("enabled", True)}


SUPPORT = ReviewedSupport()
