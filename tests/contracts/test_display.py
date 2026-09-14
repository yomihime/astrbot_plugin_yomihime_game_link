import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from ygl_test_subject.api.display import (
    DisplayDocument,
    ImageBlock,
    MoneyValue,
    NumberValue,
    Privacy,
    TableBlock,
    TextBlock,
    TimeValue,
    UnknownBlock,
)
from ygl_test_subject.api.results import (
    CapabilityResult,
    ErrorCode,
    ErrorDetail,
    FactDocument,
    ResultStatus,
)


class DisplayContractTests(unittest.TestCase):
    def test_all_block_required_flags_and_fallback_are_typed(self):
        from ygl_test_subject.api.display import (
            CommandsBlock,
            FieldsBlock,
            GridItem,
            ItemGridBlock,
            LinksBlock,
            MetricsBlock,
            SeriesBlock,
        )

        factories = (
            lambda **kw: TextBlock("x", **kw),
            lambda **kw: FieldsBlock({"x": 1}, **kw),
            lambda **kw: MetricsBlock({"x": NumberValue(1)}, **kw),
            lambda **kw: TableBlock(("x",), ((1,),), **kw),
            lambda **kw: ItemGridBlock((GridItem("x"),), **kw),
            lambda **kw: ImageBlock("a", "alt", **kw),
            lambda **kw: SeriesBlock((), **kw),
            lambda **kw: LinksBlock((), **kw),
            lambda **kw: CommandsBlock((), **kw),
            lambda **kw: UnknownBlock("future", **kw),
        )
        for index, factory in enumerate(factories):
            for kwargs in ({"required": "yes"}, {"fallback_text": 1}):
                with (
                    self.subTest(block=index, kwargs=kwargs),
                    self.assertRaises((TypeError, ValueError)),
                ):
                    factory(**kwargs)
        with self.assertRaises(ValueError):
            ItemGridBlock(())
        self.assertEqual(ItemGridBlock((), fallback_text="no items").items, ())

    def test_nonfinite_values_and_malformed_times_are_rejected(self):
        from ygl_test_subject.api.display import FieldsBlock

        for value in (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")):
            for factory in (FieldsBlock, FactDocument):
                with (
                    self.subTest(factory=factory, value=value),
                    self.assertRaises(ValueError),
                ):
                    factory({"nested": [value]})
        for value in (date(2026, 1, 1), datetime(2026, 1, 1, tzinfo=timezone.utc)):
            with self.assertRaises(TypeError):
                TimeValue(value, timezone_name=1)

    def test_result_status_matrix_and_concrete_payload_types(self):
        document = DisplayDocument("t", "s", (TextBlock("candidate or result"),))
        for status in (
            ResultStatus.SUCCESS,
            ResultStatus.PARTIAL_SUCCESS,
            ResultStatus.NEEDS_SELECTION,
        ):
            self.assertIs(
                CapabilityResult("r", status, document=document).status, status
            )
            with self.assertRaises(ValueError):
                CapabilityResult("r", status)
        for changes in (
            {"status": 123},
            {"document": object()},
            {"model_facts": object()},
            {"error": object()},
            {"timestamps": ("yesterday",)},
            {"timestamps": (TimeValue(date(2026, 1, 1), "UTC"),)},
        ):
            with (
                self.subTest(changes=changes),
                self.assertRaises((TypeError, ValueError)),
            ):
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
        with self.assertRaises(ValueError):
            CapabilityResult(
                "r",
                ResultStatus.ERROR,
                error=ErrorDetail(ErrorCode.UNKNOWN, "failed"),
                model_facts=FactDocument({"x": 1}),
            )
        instant = TimeValue(datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(
            CapabilityResult(
                "r", ResultStatus.SUCCESS, document=document, timestamps=(instant,)
            ).timestamps,
            (instant,),
        )

    def test_document_and_nested_containers_are_immutable(self):
        fields = {"x": {"y": 1}}
        from ygl_test_subject.api.display import FieldsBlock

        block = FieldsBlock(fields)
        fields["x"]["y"] = 2
        self.assertEqual(block.fields["x"]["y"], 1)
        with self.assertRaises(TypeError):
            block.fields["x"] = 3

    def test_typed_values_and_timezone(self):
        self.assertEqual(MoneyValue(Decimal("1.2300"), "USD").value, Decimal("1.2300"))
        with self.assertRaises(ValueError):
            TimeValue(datetime(2026, 1, 1))
        self.assertEqual(NumberValue("1.20", "ratio").value, "1.20")

    def test_grid_table_and_plain_angle_text(self):
        doc = DisplayDocument(
            "title", "subject", (TextBlock("x < y"), TableBlock(("a",), ((1,),)))
        )
        self.assertEqual(doc.ordered_blocks[0].text, "x < y")

    def test_resource_and_unknown_block_validation(self):
        with self.assertRaises(ValueError):
            ImageBlock("../secret", "alt")
        with self.assertRaises(ValueError):
            UnknownBlock("future", required=True)
        with self.assertRaises(ValueError):
            UnknownBlock("future")

    def test_result_privacy_and_status_invariants(self):
        private = DisplayDocument(
            "t", "s", (TextBlock("private"),), privacy=Privacy.PRIVATE
        )
        with self.assertRaises(ValueError):
            CapabilityResult(
                "r",
                ResultStatus.SUCCESS,
                document=private,
                model_facts=FactDocument({"x": 1}),
                privacy=Privacy.PRIVATE,
            )
        with self.assertRaises(ValueError):
            CapabilityResult("r", ResultStatus.ERROR)
        result = CapabilityResult(
            "r", ResultStatus.ERROR, error=ErrorDetail(ErrorCode.NOT_FOUND, "missing")
        )
        self.assertEqual(result.error.code, ErrorCode.NOT_FOUND)

    def test_rejects_untrusted_values_and_visibility_leaks(self):
        from ygl_test_subject.api.display import GridItem, ItemGridBlock, SeriesBlock

        with self.assertRaises((TypeError, ValueError)):
            NumberValue(float("nan"))
        with self.assertRaises(TypeError):
            NumberValue(object())
        with self.assertRaises(TypeError):
            NumberValue(Decimal("1"), unit=object())
        with self.assertRaises(TypeError):
            __import__(
                "ygl_test_subject.api.results", fromlist=["FactDocument"]
            ).FactDocument({1: object()})
        with self.assertRaises(ValueError):
            DisplayDocument(
                "t",
                "s",
                (
                    ItemGridBlock(
                        (GridItem("x", asset_id="a", visibility=Privacy.PRIVATE),)
                    ),
                ),
            )
        with self.assertRaises((TypeError, ValueError)):
            SeriesBlock(((TimeValue(datetime.now(timezone.utc)), "bad"),))
        self.assertEqual(
            ErrorDetail(ErrorCode.UNKNOWN, "literal {text}").message, "literal {text}"
        )


if __name__ == "__main__":
    unittest.main()
