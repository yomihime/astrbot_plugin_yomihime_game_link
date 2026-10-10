import inspect
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import get_type_hints

from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.examples.contracts import example_result

from yomihime_game_link_sdk.display import (
    DigestMember,
    DisplayAudience,
    DisplayBatch,
    DisplayBatchMember,
    DisplayDocument,
    DisplayLimits,
    DisplayOutput,
    DisplayRenderer,
    FieldsBlock,
    GridItem,
    ImageBlock,
    ItemGridBlock,
    MoneyValue,
    NumberValue,
    Privacy,
    TableBlock,
    TextBlock,
    TimeValue,
    UnknownBlock,
)
from yomihime_game_link_sdk.results import (
    CapabilityResult,
    ErrorCode,
    ErrorDetail,
    FactDocument,
    ResultStatus,
)


class DisplayContractTests(unittest.TestCase):
    def test_all_block_required_flags_and_fallback_are_typed(self):
        from yomihime_game_link_sdk.display import (
            CommandsBlock,
            FieldsBlock,
            GridItem,
            ItemGridBlock,
            LinksBlock,
            MetricsBlock,
            SeriesBlock,
        )

        factories = (
            lambda **kw: validate_contract(TextBlock("x", **kw)),
            lambda **kw: validate_contract(FieldsBlock({"x": 1}, **kw)),
            lambda **kw: validate_contract(
                MetricsBlock({"x": validate_contract(NumberValue(1))}, **kw)
            ),
            lambda **kw: validate_contract(TableBlock(("x",), ((1,),), **kw)),
            lambda **kw: validate_contract(
                ItemGridBlock((validate_contract(GridItem("x")),), **kw)
            ),
            lambda **kw: validate_contract(ImageBlock("a", "alt", **kw)),
            lambda **kw: validate_contract(SeriesBlock((), **kw)),
            lambda **kw: validate_contract(LinksBlock((), **kw)),
            lambda **kw: validate_contract(CommandsBlock((), **kw)),
            lambda **kw: validate_contract(UnknownBlock("future", **kw)),
        )
        for index, factory in enumerate(factories):
            for kwargs in ({"required": "yes"}, {"fallback_text": 1}):
                with (
                    self.subTest(block=index, kwargs=kwargs),
                    self.assertRaises((TypeError, ValueError)),
                ):
                    factory(**kwargs)
        with self.assertRaises(ValueError):
            validate_contract(ItemGridBlock(()))
        self.assertEqual(
            validate_contract(ItemGridBlock((), fallback_text="no items")).items, ()
        )

    def test_nonfinite_values_and_malformed_times_are_rejected(self):
        from yomihime_game_link_sdk.display import FieldsBlock

        for value in (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")):
            for factory in (FieldsBlock, FactDocument):
                with (
                    self.subTest(factory=factory, value=value),
                    self.assertRaises(ValueError),
                ):
                    validate_contract(factory({"nested": [value]}))
        for value in (date(2026, 1, 1), datetime(2026, 1, 1, tzinfo=timezone.utc)):
            with self.assertRaises(TypeError):
                validate_contract(TimeValue(value, timezone_name=1))

    def test_result_status_matrix_and_concrete_payload_types(self):
        document = validate_contract(
            DisplayDocument(
                "t", "s", (validate_contract(TextBlock("candidate or result")),)
            )
        )
        for status in (
            ResultStatus.SUCCESS,
            ResultStatus.PARTIAL_SUCCESS,
            ResultStatus.NEEDS_SELECTION,
        ):
            self.assertIs(
                validate_contract(
                    CapabilityResult("r", status, document=document)
                ).status,
                status,
            )
            with self.assertRaises(ValueError):
                validate_contract(CapabilityResult("r", status))
        for changes in (
            {"status": 123},
            {"document": object()},
            {"model_facts": object()},
            {"error": object()},
            {"timestamps": ("yesterday",)},
            {"timestamps": (validate_contract(TimeValue(date(2026, 1, 1), "UTC")),)},
        ):
            with (
                self.subTest(changes=changes),
                self.assertRaises((TypeError, ValueError)),
            ):
                validate_contract(
                    CapabilityResult(
                        **(
                            {
                                "result_id": "r",
                                "status": ResultStatus.SUCCESS,
                                "document": document,
                            }
                            | changes
                        )
                    )
                )
        with self.assertRaises(ValueError):
            validate_contract(
                CapabilityResult(
                    "r",
                    ResultStatus.ERROR,
                    error=validate_contract(ErrorDetail(ErrorCode.UNKNOWN, "failed")),
                    model_facts=validate_contract(FactDocument({"x": 1})),
                )
            )
        instant = validate_contract(
            TimeValue(datetime(2026, 1, 1, tzinfo=timezone.utc))
        )
        self.assertEqual(
            validate_contract(
                CapabilityResult(
                    "r", ResultStatus.SUCCESS, document=document, timestamps=(instant,)
                )
            ).timestamps,
            (instant,),
        )

    def test_document_and_nested_containers_are_immutable(self):
        fields = {"x": {"y": 1}}
        from yomihime_game_link_sdk.display import FieldsBlock

        block = validate_contract(FieldsBlock(fields))
        fields["x"]["y"] = 2
        self.assertEqual(block.fields["x"]["y"], 1)
        with self.assertRaises(TypeError):
            block.fields["x"] = 3

    def test_typed_values_and_timezone(self):
        self.assertEqual(
            validate_contract(MoneyValue(Decimal("1.2300"), "USD")).value,
            Decimal("1.2300"),
        )
        with self.assertRaises(ValueError):
            validate_contract(TimeValue(datetime(2026, 1, 1)))
        self.assertEqual(validate_contract(NumberValue("1.20", "ratio")).value, "1.20")

    def test_grid_table_and_plain_angle_text(self):
        doc = validate_contract(
            DisplayDocument(
                "title",
                "subject",
                (
                    validate_contract(TextBlock("x < y")),
                    validate_contract(TableBlock(("a",), ((1,),))),
                ),
            )
        )
        self.assertEqual(doc.ordered_blocks[0].text, "x < y")

    def test_resource_and_unknown_block_validation(self):
        with self.assertRaises(ValueError):
            validate_contract(ImageBlock("../secret", "alt"))
        with self.assertRaises(ValueError):
            validate_contract(UnknownBlock("future", required=True))
        with self.assertRaises(ValueError):
            validate_contract(UnknownBlock("future"))

    def test_result_privacy_and_status_invariants(self):
        private = validate_contract(
            DisplayDocument(
                "t",
                "s",
                (validate_contract(TextBlock("private")),),
                privacy=Privacy.PRIVATE,
            )
        )
        with self.assertRaises(ValueError):
            validate_contract(
                CapabilityResult(
                    "r",
                    ResultStatus.SUCCESS,
                    document=private,
                    model_facts=validate_contract(FactDocument({"x": 1})),
                    privacy=Privacy.PRIVATE,
                )
            )
        with self.assertRaises(ValueError):
            validate_contract(CapabilityResult("r", ResultStatus.ERROR))
        result = validate_contract(
            CapabilityResult(
                "r",
                ResultStatus.ERROR,
                error=validate_contract(ErrorDetail(ErrorCode.NOT_FOUND, "missing")),
            )
        )
        self.assertEqual(result.error.code, ErrorCode.NOT_FOUND)

    def test_rejects_untrusted_values_and_visibility_leaks(self):
        from yomihime_game_link_sdk.display import GridItem, ItemGridBlock, SeriesBlock

        with self.assertRaises((TypeError, ValueError)):
            validate_contract(NumberValue(float("nan")))
        with self.assertRaises(TypeError):
            validate_contract(NumberValue(object()))
        with self.assertRaises(TypeError):
            validate_contract(NumberValue(Decimal("1"), unit=object()))
        with self.assertRaises(TypeError):
            validate_contract(
                __import__(
                    "yomihime_game_link_sdk.results", fromlist=["FactDocument"]
                ).FactDocument({1: object()})
            )
        with self.assertRaises(ValueError):
            validate_contract(
                DisplayDocument(
                    "t",
                    "s",
                    (
                        validate_contract(
                            ItemGridBlock(
                                (
                                    validate_contract(
                                        GridItem(
                                            "x",
                                            asset_id="a",
                                            visibility=Privacy.PRIVATE,
                                        )
                                    ),
                                )
                            )
                        ),
                    ),
                )
            )
        with self.assertRaises((TypeError, ValueError)):
            validate_contract(
                SeriesBlock(
                    ((validate_contract(TimeValue(datetime.now(timezone.utc))), "bad"),)
                )
            )
        self.assertEqual(
            validate_contract(ErrorDetail(ErrorCode.UNKNOWN, "literal {text}")).message,
            "literal {text}",
        )


class B04DisplayContractTests(unittest.TestCase):
    def test_renderer_accepts_only_generic_document_and_explicit_limits(self):
        self.assertEqual(
            tuple(inspect.signature(DisplayRenderer.render).parameters),
            ("self", "document", "limits", "audience"),
        )
        hints = get_type_hints(DisplayRenderer.render)
        self.assertIs(hints["document"], DisplayDocument)
        self.assertIs(hints["limits"], DisplayLimits)
        self.assertIs(hints["audience"], DisplayAudience)
        self.assertEqual(
            inspect.signature(DisplayRenderer.render).parameters["audience"].default,
            DisplayAudience.PUBLIC,
        )
        self.assertEqual(
            tuple(inspect.signature(DisplayRenderer.render_batch).parameters),
            ("self", "batch", "limits"),
        )
        self.assertIs(
            get_type_hints(DisplayRenderer.render_batch)["batch"], DisplayBatch
        )
        self.assertNotIn(
            "module_id", inspect.signature(DisplayRenderer.render).parameters
        )

    def test_public_document_cannot_expose_private_assets_or_grid_values(self):
        with self.assertRaises(ValueError):
            validate_contract(
                DisplayDocument(
                    "Public",
                    "Subject",
                    (
                        validate_contract(
                            ImageBlock("asset-1", "secret", visibility=Privacy.PRIVATE)
                        ),
                    ),
                )
            )
        with self.assertRaises(ValueError):
            validate_contract(
                DisplayDocument(
                    "Public",
                    "Subject",
                    (
                        validate_contract(
                            ItemGridBlock(
                                (
                                    validate_contract(
                                        GridItem(
                                            "secret",
                                            asset_id="asset-1",
                                            visibility=Privacy.PRIVATE,
                                        )
                                    ),
                                )
                            )
                        ),
                    ),
                )
            )
        private = validate_contract(
            DisplayDocument(
                "Private",
                "Subject",
                (validate_contract(TextBlock("secret")),),
                privacy=Privacy.PRIVATE,
            )
        )
        self.assertEqual(private.privacy, Privacy.PRIVATE)

    def test_unknown_optional_fallback_and_required_unknown_policy(self):
        fallback = validate_contract(
            UnknownBlock("future-card", fallback_text="Summary")
        )
        self.assertEqual(fallback.fallback_text, "Summary")
        with self.assertRaises(ValueError):
            validate_contract(UnknownBlock("future-card", required=True))
        with self.assertRaises(TypeError):
            validate_contract(DisplayDocument("Title", "Subject", (object(),)))

    def test_output_and_limits_are_validated_and_have_no_product_default(self):
        self.assertEqual(
            validate_contract(DisplayOutput("Rendered", ("asset-1",))).resource_ids,
            ("asset-1",),
        )
        with self.assertRaises(ValueError):
            validate_contract(DisplayOutput("Rendered", ("../private",)))
        with self.assertRaises(ValueError):
            validate_contract(DisplayOutput("Rendered", ("asset-1", "asset-1")))
        with self.assertRaises(ValueError):
            validate_contract(DisplayLimits(0, 100))
        self.assertEqual(validate_contract(DisplayLimits(2, 1024)).max_pages, 2)
        self.assertIs(
            inspect.signature(DisplayRenderer.render).parameters["limits"].default,
            inspect.Parameter.empty,
        )

    def test_display_values_remain_renderer_neutral(self):
        doc = validate_contract(
            DisplayDocument(
                "Title",
                "Subject",
                (
                    validate_contract(FieldsBlock({"status": "ready"})),
                    validate_contract(TextBlock("Done")),
                ),
            )
        )
        self.assertEqual(
            tuple(block.kind for block in doc.ordered_blocks), ("fields", "text")
        )

    def test_c03_batch_preserves_order_and_public_audience_cannot_promote_private(self):
        first = validate_contract(
            DisplayBatchMember(
                validate_contract(DigestMember("sub-1", 1, "event-1", 1)),
                validate_contract(
                    DisplayDocument(
                        "First", "Subject", (validate_contract(TextBlock("one")),)
                    )
                ),
            )
        )
        private = validate_contract(
            DisplayBatchMember(
                validate_contract(DigestMember("sub-2", 1, "event-2", 1)),
                validate_contract(
                    DisplayDocument(
                        "Second",
                        "Subject",
                        (validate_contract(TextBlock("two")),),
                        privacy=Privacy.PRIVATE,
                    )
                ),
            )
        )
        public_batch = validate_contract(DisplayBatch((first,), DisplayAudience.PUBLIC))
        self.assertEqual(public_batch.members, (first,))
        private_batch = validate_contract(
            DisplayBatch((first, private), DisplayAudience.PRIVATE)
        )
        self.assertEqual(private_batch.members, (first, private))
        with self.assertRaises(ValueError):
            validate_contract(DisplayBatch((first, private), DisplayAudience.PUBLIC))
        with self.assertRaises(ValueError):
            validate_contract(DisplayBatch((first, first), DisplayAudience.PRIVATE))
        with self.assertRaises(TypeError):
            validate_contract(DisplayBatch((object(),), DisplayAudience.PRIVATE))


class ExampleResultContractTests(unittest.TestCase):
    def test_example_result_uses_public_types_and_truthful_source(self):
        result = example_result()
        self.assertIsInstance(result, CapabilityResult)
        self.assertIsInstance(result.document, DisplayDocument)
        self.assertEqual(result.document.sources, ("offline example",))


if __name__ == "__main__":
    unittest.main()
