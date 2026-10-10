"""B04-P renderer tests use deterministic ports, not a real image stack."""

import unittest

from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.presentation.rendering import (
    GenericDisplayRenderer,
    RenderingBounds,
    RenderingFailure,
)

from yomihime_game_link_sdk.display import (
    DigestMember,
    DisplayAudience,
    DisplayBatch,
    DisplayBatchMember,
    DisplayDocument,
    DisplayLimits,
    FieldsBlock,
    GridItem,
    ImageBlock,
    ItemGridBlock,
    Link,
    LinksBlock,
    MetricsBlock,
    NumberValue,
    Privacy,
    SeriesBlock,
    TableBlock,
    TextBlock,
    UnknownBlock,
)


class _Reader:
    """A fake authorization-aware reader with a fixed in-memory mapping."""

    def __init__(self, assets=None, *, denied=()):
        self.assets = assets or {"img:one": b"fake-image"}
        self.denied = set(denied)
        self.calls = []

    async def read(self, asset_id, *, audience, max_bytes):
        self.calls.append((asset_id, audience, max_bytes))
        if asset_id in self.denied or asset_id not in self.assets:
            raise PermissionError("private path and token details must stay hidden")
        return self.assets[asset_id]


class _ImageBackend:
    """A fake decoder that records the bounds and can deterministically fail."""

    def __init__(self, *, fail=False, image_dimension=32):
        self.fail = fail
        self.image_dimension = image_dimension
        self.calls = []

    async def validate(self, image_bytes, *, max_bytes, max_dimension):
        self.calls.append((len(image_bytes), max_bytes, max_dimension))
        if self.fail or self.image_dimension > max_dimension:
            raise ValueError("decoder stack and path must stay hidden")


def _bounds(**changes):
    values = {
        "max_chars_per_page": 500,
        "max_lines_per_page": 40,
        "max_fields_per_block": 8,
        "max_rows_per_block": 8,
        "max_asset_read_bytes": 32,
        "max_image_dimension": 128,
        "max_asset_reads": 8,
        "max_blocks_per_document": 64,
        "max_members_per_batch": 32,
    }
    values.update(changes)
    return RenderingBounds(**values)


def _limits(*, pages=2, image_bytes=16):
    return validate_contract(
        DisplayLimits(max_pages=pages, max_image_bytes=image_bytes)
    )


def _renderer(reader=None, backend=None, **bounds):
    return GenericDisplayRenderer(
        _bounds(**bounds), asset_reader=reader, image_backend=backend
    )


class GenericDisplayRendererTests(unittest.IsolatedAsyncioTestCase):
    async def test_visible_field_row_and_nested_limits_have_explicit_notice(self):
        documents = (
            validate_contract(
                DisplayDocument(
                    "Fields", "s", (validate_contract(FieldsBlock({"a": 1, "b": 2})),)
                )
            ),
            validate_contract(
                DisplayDocument(
                    "Nested",
                    "s",
                    (validate_contract(FieldsBlock({"nested": {"a": 1, "b": 2}})),),
                )
            ),
            validate_contract(
                DisplayDocument(
                    "Rows",
                    "s",
                    (validate_contract(TableBlock(("column",), ((1,), (2,)))),),
                )
            ),
            validate_contract(
                DisplayDocument(
                    "Grid",
                    "s",
                    (
                        validate_contract(
                            ItemGridBlock(
                                (
                                    validate_contract(GridItem("a", 1)),
                                    validate_contract(GridItem("b", 2)),
                                )
                            )
                        ),
                    ),
                )
            ),
        )
        for document in documents:
            output = await _renderer(
                max_fields_per_block=1, max_rows_per_block=1
            ).render(document, limits=_limits())
            self.assertIn("内容已截断", output.text)

    async def test_natural_projection_keeps_sources_and_marks_budget_truncation(self):
        document = validate_contract(
            DisplayDocument(
                "Overview",
                "subject",
                (
                    validate_contract(TextBlock("plain")),
                    validate_contract(FieldsBlock({"state": "ready"})),
                ),
                sources=("Universalis", "fixture"),
            )
        )
        output = await _renderer().render(document, limits=_limits())
        self.assertEqual(output.text.splitlines()[:2], ["Overview", "subject"])
        self.assertIn("Universalis", output.text)
        self.assertIn("fixture", output.text)
        self.assertNotIn("文本: plain", output.text)
        self.assertNotIn("字段:", output.text)
        bounded = await _renderer(max_chars_per_page=30).render(
            document, limits=_limits(pages=1)
        )
        self.assertLessEqual(len(bounded.text), 30)
        self.assertIn("截断", bounded.text)

    async def test_p01_renders_all_generic_public_block_kinds_in_order(self):
        document = validate_contract(
            DisplayDocument(
                "Overview",
                "subject-1",
                (
                    validate_contract(TextBlock("plain")),
                    validate_contract(FieldsBlock({"state": "ready"})),
                    validate_contract(
                        MetricsBlock(
                            {"score": validate_contract(NumberValue("12.5", "points"))}
                        )
                    ),
                    validate_contract(TableBlock(("name", "value"), (("row", 1),))),
                    validate_contract(
                        ItemGridBlock((validate_contract(GridItem("item", "shown")),))
                    ),
                    validate_contract(SeriesBlock((), fallback_text="series empty")),
                    validate_contract(
                        LinksBlock(
                            (
                                validate_contract(
                                    Link("docs", "https://example.test/docs")
                                ),
                            )
                        )
                    ),
                ),
            )
        )

        output = await _renderer().render(document, limits=_limits())

        for expected in (
            "plain",
            "state: ready",
            "score: 12.5 points",
            "name | value",
            "row | 1",
            "item: shown",
            "series empty",
            "docs: https://example.test/docs",
        ):
            self.assertIn(expected, output.text)
        self.assertLess(output.text.index("plain"), output.text.index("state"))

    async def test_p02_image_backend_success_returns_only_reader_checked_ref(self):
        reader, backend = _Reader(), _ImageBackend()
        document = validate_contract(
            DisplayDocument(
                "Image", "s", (validate_contract(ImageBlock("img:one", "portrait")),)
            )
        )

        output = await _renderer(reader, backend).render(document, limits=_limits())

        self.assertIn("portrait", output.text)
        self.assertEqual(output.resource_ids, ("img:one",))
        self.assertEqual(reader.calls, [("img:one", DisplayAudience.PUBLIC, 16)])
        self.assertEqual(backend.calls, [(10, 16, 128)])

    async def test_p03_optional_failure_degrades_and_required_uses_fallback(self):
        reader = _Reader(denied={"img:missing"})
        document = validate_contract(
            DisplayDocument(
                "Fallback",
                "s",
                (
                    validate_contract(
                        ImageBlock("img:missing", "optional", required=False)
                    ),
                    validate_contract(
                        ImageBlock(
                            "img:missing",
                            "required",
                            required=True,
                            fallback_text="chart unavailable",
                        )
                    ),
                ),
            )
        )

        output = await _renderer(reader, _ImageBackend()).render(
            document, limits=_limits()
        )

        self.assertIn("图片不可用: optional", output.text)
        self.assertIn("chart unavailable", output.text)
        self.assertEqual(output.resource_ids, ())

    async def test_p03_required_without_fallback_is_controlled_failure(self):
        document = validate_contract(
            DisplayDocument(
                "Required",
                "s",
                (
                    validate_contract(TextBlock("ok")),
                    validate_contract(UnknownBlock("future", fallback_text="x")),
                ),
            )
        )
        object.__setattr__(document.ordered_blocks[-1], "required", True)
        object.__setattr__(document.ordered_blocks[-1], "fallback_text", None)
        with self.assertRaises(RenderingFailure) as raised:
            await _renderer().render(document, limits=_limits())
        self.assertEqual(str(raised.exception), "display_render_failed")

    async def test_p04_unknown_blocks_and_unsupported_versions_fail_closed(self):
        document = validate_contract(
            DisplayDocument(
                "Unknown",
                "s",
                (
                    validate_contract(
                        UnknownBlock("future", fallback_text="future fallback")
                    ),
                ),
            )
        )
        output = await _renderer().render(document, limits=_limits())
        self.assertIn("future fallback", output.text)

        malformed = object.__new__(DisplayDocument)
        object.__setattr__(malformed, "title", "x")
        object.__setattr__(malformed, "subject", "y")
        object.__setattr__(malformed, "ordered_blocks", (object(),))
        object.__setattr__(malformed, "privacy", Privacy.PUBLIC)
        object.__setattr__(malformed, "schema_version", "unknown")
        version_output = await _renderer().render(malformed, limits=_limits())
        self.assertIn("不支持的显示版本", version_output.text)

        future_document = validate_contract(
            DisplayDocument(
                "Future",
                "s",
                (validate_contract(TextBlock("future-private-format")),),
            )
        )
        object.__setattr__(future_document, "schema_version", "future-version")
        batch = DisplayBatch(
            (
                DisplayBatchMember(
                    DigestMember("sub-future", 1, "event-future", 1), future_document
                ),
            ),
            DisplayAudience.PUBLIC,
        )
        batch_output = await _renderer().render_batch(batch, _limits())
        self.assertIn("不支持的显示版本", batch_output.text)
        self.assertNotIn("future-private-format", batch_output.text)

    async def test_p05_private_document_and_blocks_never_enter_public_output(self):
        private_document = validate_contract(
            DisplayDocument(
                "Private",
                "s",
                (validate_contract(TextBlock("secret")),),
                privacy=Privacy.PRIVATE,
            )
        )
        with self.assertRaises(RenderingFailure):
            await _renderer().render(private_document, limits=_limits())

        private_output = await _renderer().render(
            private_document,
            limits=_limits(),
            audience=DisplayAudience.PRIVATE,
        )
        self.assertIn("secret", private_output.text)

        reader = _Reader({"img:private": b"private-image"})
        private_asset_document = validate_contract(
            DisplayDocument(
                "Private asset",
                "s",
                (
                    validate_contract(
                        ItemGridBlock(
                            (
                                validate_contract(
                                    GridItem(
                                        "private item",
                                        asset_id="img:private",
                                        visibility=Privacy.PRIVATE,
                                    )
                                ),
                            )
                        )
                    ),
                ),
                privacy=Privacy.PRIVATE,
            )
        )
        with self.assertRaises(RenderingFailure):
            await _renderer(reader, _ImageBackend()).render(
                private_asset_document, limits=_limits()
            )
        self.assertEqual(reader.calls, [])
        private_asset_output = await _renderer(reader, _ImageBackend()).render(
            private_asset_document,
            limits=_limits(),
            audience=DisplayAudience.PRIVATE,
        )
        self.assertEqual(private_asset_output.resource_ids, ("img:private",))
        self.assertEqual(reader.calls[0][1], DisplayAudience.PRIVATE)

    async def test_p06_asset_identifiers_never_bypass_reader_authorization(self):
        reader = _Reader(denied={"public:unknown"})
        document = validate_contract(
            DisplayDocument(
                "Assets",
                "s",
                (
                    validate_contract(
                        ImageBlock("public:unknown", "unknown", fallback_text="hidden")
                    ),
                    validate_contract(ImageBlock("img:one", "allowed")),
                ),
            )
        )
        output = await _renderer(reader, _ImageBackend()).render(
            document, limits=_limits()
        )
        self.assertEqual(
            [call[0] for call in reader.calls], ["public:unknown", "img:one"]
        )
        self.assertEqual(output.resource_ids, ("img:one",))

    async def test_p07_character_page_row_field_image_and_read_limits_apply(self):
        too_big_reader = _Reader({"img:big": b"x" * 20})
        document = validate_contract(
            DisplayDocument(
                "Bounded",
                "s",
                (
                    validate_contract(FieldsBlock({"one": 1, "two": 2, "three": 3})),
                    validate_contract(TableBlock(("a", "b"), ((1, 2), (3, 4), (5, 6)))),
                    validate_contract(
                        ImageBlock("img:big", "large", fallback_text="large omitted")
                    ),
                    validate_contract(TextBlock("z" * 200)),
                ),
            )
        )
        backend = _ImageBackend()
        output = await _renderer(
            too_big_reader,
            backend,
            max_chars_per_page=60,
            max_lines_per_page=8,
            max_fields_per_block=1,
            max_rows_per_block=1,
            max_asset_read_bytes=10,
        ).render(document, limits=_limits(pages=1, image_bytes=8))

        self.assertLessEqual(len(output.text), 60)
        self.assertLessEqual(output.text.count("\n") + 1, 8)
        self.assertIn("one: 1", output.text)
        self.assertNotIn("two: 2", output.text)
        self.assertNotIn("3 | 4", output.text)
        self.assertIn("large omitted", output.text)
        self.assertEqual(too_big_reader.calls[0][2], 8)
        self.assertEqual(backend.calls, [])
        self.assertEqual(output.resource_ids, ())
        dimension_reader = _Reader({"img:wide": b"small-image"})
        dimension_backend = _ImageBackend(image_dimension=256)
        dimension_document = validate_contract(
            DisplayDocument(
                "Wide image",
                "s",
                (
                    validate_contract(
                        ImageBlock(
                            "img:wide", "wide", fallback_text="dimension omitted"
                        )
                    ),
                ),
            )
        )
        dimension_output = await _renderer(
            dimension_reader, dimension_backend, max_image_dimension=128
        ).render(dimension_document, limits=_limits())
        self.assertIn("dimension omitted", dimension_output.text)
        self.assertEqual(dimension_output.resource_ids, ())
        self.assertEqual(dimension_backend.calls, [(11, 16, 128)])
        multiline = await _renderer().render(
            validate_contract(
                DisplayDocument(
                    "Line", "s", (validate_contract(TextBlock("first\nsecond")),)
                )
            ),
            limits=_limits(),
        )
        self.assertIn("first second", multiline.text)
        self.assertEqual(multiline.text.count("\n"), 2)
        with self.assertRaises(ValueError):
            _bounds(max_asset_reads=0)

    async def test_p07_truncated_image_lines_do_not_return_media_references(self):
        reader = _Reader({"img:one": b"one", "img:two": b"two"})
        backend = _ImageBackend()
        document = validate_contract(
            DisplayDocument(
                "T",
                "S",
                (
                    validate_contract(TextBlock("x" * 50)),
                    validate_contract(ImageBlock("img:one", "later image")),
                    validate_contract(
                        ItemGridBlock(
                            (
                                validate_contract(
                                    GridItem("later grid item", asset_id="img:two")
                                ),
                            )
                        )
                    ),
                ),
            )
        )

        output = await _renderer(
            reader, backend, max_chars_per_page=24, max_lines_per_page=8
        ).render(document, limits=_limits(pages=1))

        self.assertLessEqual(len(output.text), 24)
        self.assertIn("内容已截断", output.text)
        self.assertNotIn("later image", output.text)
        self.assertNotIn("later grid item", output.text)
        self.assertEqual(output.resource_ids, ())
        self.assertEqual(len(reader.calls), 2)

    async def test_p07_truncation_marker_counts_inside_character_and_line_budgets(self):
        document = validate_contract(
            DisplayDocument(
                "T",
                "S",
                (
                    validate_contract(TextBlock("x")),
                    validate_contract(TextBlock("y" * 30)),
                ),
            )
        )
        output = await _renderer(max_chars_per_page=20, max_lines_per_page=4).render(
            document, limits=_limits(pages=1)
        )
        self.assertLessEqual(len(output.text), 20)
        self.assertLessEqual(output.text.count("\n") + 1, 4)
        self.assertIn("内容已截断", output.text)

        tiny_renderer = _renderer(max_chars_per_page=4, max_lines_per_page=1)
        tiny_output = await tiny_renderer.render(document, limits=_limits(pages=1))
        repeated_output = await tiny_renderer.render(document, limits=_limits(pages=1))
        self.assertEqual(tiny_output.text, "[截断]")
        self.assertEqual(tiny_output.text, repeated_output.text)
        self.assertLessEqual(len(tiny_output.text), 4)

    async def test_p08_missing_asset_and_decode_errors_do_not_leak_details(self):
        reader = _Reader()
        document = validate_contract(
            DisplayDocument(
                "Image",
                "s",
                (
                    validate_contract(
                        ImageBlock("img:one", "alt", fallback_text="fallback")
                    ),
                ),
            )
        )
        output = await _renderer(reader, _ImageBackend(fail=True)).render(
            document, limits=_limits()
        )
        self.assertIn("fallback", output.text)
        for secret in ("decoder stack", "fake-image", "img:one", "path"):
            self.assertNotIn(secret, output.text)
        required = validate_contract(
            DisplayDocument(
                "Required",
                "s",
                (validate_contract(ImageBlock("img:missing", "x", required=True)),),
            )
        )
        with self.assertRaises(RenderingFailure) as raised:
            await _renderer(_Reader(denied={"img:missing"}), _ImageBackend()).render(
                required, limits=_limits()
            )
        self.assertEqual(str(raised.exception), "display_render_failed")

    async def test_p09_same_structure_preserves_declared_sources(self):
        first = validate_contract(
            DisplayDocument(
                "Same",
                "s",
                (validate_contract(TextBlock("value")),),
                sources=("alpha",),
            )
        )
        second = validate_contract(
            DisplayDocument(
                "Same", "s", (validate_contract(TextBlock("value")),), sources=("beta",)
            )
        )
        renderer = _renderer()
        left, right = (
            await renderer.render(first, limits=_limits()),
            await renderer.render(second, limits=_limits()),
        )
        self.assertIn("来源：alpha", left.text)
        self.assertIn("来源：beta", right.text)
        self.assertEqual(left.text.replace("alpha", "beta"), right.text)
        self.assertEqual(left.resource_ids, right.resource_ids)

    async def test_p10_text_only_backend_is_supported(self):
        document = validate_contract(
            DisplayDocument(
                "Text",
                "s",
                (
                    validate_contract(TextBlock("works")),
                    validate_contract(ImageBlock("img:one", "alt")),
                ),
            )
        )
        output = await _renderer().render(document, limits=_limits())
        self.assertIn("works", output.text)
        self.assertIn("图片不可用: alt", output.text)
        self.assertEqual(output.resource_ids, ())

    async def test_p11_batch_is_bounded_ordered_and_member_traceable(self):
        first_member = validate_contract(DigestMember("sub-a", 1, "event-a", 1))
        second_member = validate_contract(DigestMember("sub-b", 2, "event-b", 3))
        batch = validate_contract(
            DisplayBatch(
                (
                    validate_contract(
                        DisplayBatchMember(
                            first_member,
                            validate_contract(
                                DisplayDocument(
                                    "A", "a", (validate_contract(TextBlock("first")),)
                                )
                            ),
                        )
                    ),
                    validate_contract(
                        DisplayBatchMember(
                            second_member,
                            validate_contract(
                                DisplayDocument(
                                    "B", "b", (validate_contract(TextBlock("second")),)
                                )
                            ),
                        )
                    ),
                ),
                DisplayAudience.PUBLIC,
            )
        )
        public_members = batch.members

        output = await _renderer(max_chars_per_page=200).render_batch(
            batch, _limits(pages=1)
        )

        self.assertEqual(batch.members, public_members)
        self.assertIs(batch.members[0].member, first_member)
        self.assertIs(batch.members[1].member, second_member)
        self.assertLess(output.text.index(" 1\n"), output.text.index("first"))
        self.assertLess(output.text.index("A"), output.text.index("first"))
        self.assertLess(output.text.index("first"), output.text.index("second"))
        self.assertLess(output.text.index("B"), output.text.index("second"))
        self.assertLess(output.text.index(" 2\n"), output.text.index("second"))
        for internal_value in ("sub-a", "event-a", "v1", "sub-b", "event-b", "v3"):
            self.assertNotIn(internal_value, output.text)
        self.assertLessEqual(len(output.text), 200)

        private_members = (
            validate_contract(
                DisplayBatchMember(
                    first_member,
                    validate_contract(
                        DisplayDocument(
                            "Private A",
                            "a",
                            (validate_contract(TextBlock("private first")),),
                            privacy=Privacy.PRIVATE,
                        )
                    ),
                )
            ),
            validate_contract(
                DisplayBatchMember(
                    second_member,
                    validate_contract(
                        DisplayDocument(
                            "Private B",
                            "b",
                            (validate_contract(TextBlock("private second")),),
                            privacy=Privacy.PRIVATE,
                        )
                    ),
                )
            ),
        )
        private_batch = validate_contract(
            DisplayBatch(private_members, DisplayAudience.PRIVATE)
        )
        private_output = await _renderer().render_batch(private_batch, _limits())

        self.assertEqual(private_batch.members, private_members)
        self.assertIs(private_batch.members[0].member, first_member)
        self.assertIs(private_batch.members[1].member, second_member)
        self.assertLess(
            private_output.text.index("Private A"),
            private_output.text.index("private first"),
        )
        self.assertLess(
            private_output.text.index("private first"),
            private_output.text.index("private second"),
        )
        self.assertLess(
            private_output.text.index("Private B"),
            private_output.text.index("private second"),
        )
        for internal_value in ("sub-a", "event-a", "v1", "sub-b", "event-b", "v3"):
            self.assertNotIn(internal_value, private_output.text)

    async def test_p12_filtered_members_and_member_visibility_are_not_promoted(self):
        public_member = validate_contract(
            DisplayBatchMember(
                validate_contract(DigestMember("public-sub", 1, "public-event", 1)),
                validate_contract(
                    DisplayDocument(
                        "Public", "s", (validate_contract(TextBlock("public body")),)
                    )
                ),
            )
        )
        private_member = validate_contract(
            DisplayBatchMember(
                validate_contract(DigestMember("private-sub", 1, "private-event", 1)),
                validate_contract(
                    DisplayDocument(
                        "Private",
                        "s",
                        (validate_contract(TextBlock("private body")),),
                        privacy=Privacy.PRIVATE,
                    )
                ),
            )
        )
        with self.assertRaises(ValueError):
            validate_contract(
                DisplayBatch((public_member, private_member), DisplayAudience.PUBLIC)
            )

        # The upstream D recheck's filtered sequence contains no private member.
        filtered_batch = validate_contract(
            DisplayBatch((public_member,), DisplayAudience.PUBLIC)
        )
        output = await _renderer().render_batch(filtered_batch, _limits())
        self.assertIn("public body", output.text)
        self.assertNotIn("private body", output.text)


if __name__ == "__main__":
    unittest.main()
