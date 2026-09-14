# C00-display handoff

Implemented the renderer-neutral display and result contracts in `api/display.py` and `api/results.py` (standard library only), with focused contract tests in `tests/contracts/test_display.py`.

Public display names: `Privacy`, `NumberValue`, `MoneyValue`, `TimeValue`, `TextBlock`, `FieldsBlock`, `MetricsBlock`, `TableBlock`, `GridItem`, `ItemGridBlock`, `ImageBlock`, `SeriesBlock`, `Link`, `LinksBlock`, `CommandsBlock`, `UnknownBlock`, `DisplayDocument`, and `DisplayBlock`.

Public result names: `ResultStatus`, `ErrorCode`, `ErrorDetail`, `FactDocument`, `CapabilityResult`.

The contracts freeze dataclass instances and nested containers, preserve exact decimal values and currency, require timezone-aware times, restrict image references to resource IDs and links to HTTP(S), reject unsupported required blocks, require fallback text for unknown optional blocks, and enforce document/result privacy and status/error consistency. `CapabilityResult` defaults to `CONTRACT_VERSION` while accepting `result_id` and `status` as its first arguments.

Validation run:

* `python -m unittest tests.contracts.test_display -v` — 5 tests passed.
* `ruff check api/display.py api/results.py tests/contracts/test_display.py` — passed.

No git commit made. Rendering, resource authorization, and model fact policy enforcement beyond these value-level checks remain the responsibility of the host.
