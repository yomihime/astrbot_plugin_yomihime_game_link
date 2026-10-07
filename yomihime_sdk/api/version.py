"""Version of the locally importable public contract surface."""

# UI-B0 adds an explicit opt-in for public, read-only web invocations.
CONTRACT_VERSION = "1.6.0"
CONTRACT_REVISION = "R5-LLM-TOOLS"
B02_CONTRACT_VERSION = "1.0.0"
_B04_CONTRACT_VERSION = "1.1.0"
_FF14_W1_N1_CONTRACT_VERSION = "1.2.0"
_FF14_W1_P1_CONTRACT_VERSION = "1.3.0"
COMPATIBLE_CONTRACT_VERSIONS = (
    B02_CONTRACT_VERSION,
    _B04_CONTRACT_VERSION,
    _FF14_W1_N1_CONTRACT_VERSION,
    _FF14_W1_P1_CONTRACT_VERSION,
    "1.4.0",
    "1.5.0",
    CONTRACT_VERSION,
)
