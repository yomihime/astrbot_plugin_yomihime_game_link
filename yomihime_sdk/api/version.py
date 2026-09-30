"""Version of the locally importable public contract surface."""

# FF14-W1-P1 adds owner-scoped capability privacy to the public manifest contract.
CONTRACT_VERSION = "1.3.0"
CONTRACT_REVISION = "FF14-W1-P1"
B02_CONTRACT_VERSION = "1.0.0"
_B04_CONTRACT_VERSION = "1.1.0"
_FF14_W1_N1_CONTRACT_VERSION = "1.2.0"
COMPATIBLE_CONTRACT_VERSIONS = (
    B02_CONTRACT_VERSION,
    _B04_CONTRACT_VERSION,
    _FF14_W1_N1_CONTRACT_VERSION,
    CONTRACT_VERSION,
)
