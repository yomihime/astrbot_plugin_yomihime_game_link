import unittest
from datetime import datetime, timezone
from decimal import MAX_EMAX, MIN_ETINY, Decimal

from ygl_test_subject.api.display import (
    CommandsBlock,
    DisplayDocument,
    FieldsBlock,
    GridItem,
    ImageBlock,
    ItemGridBlock,
    Link,
    LinksBlock,
    MetricsBlock,
    MoneyValue,
    NumberValue,
    Privacy,
    SeriesBlock,
    TableBlock,
    TextBlock,
    TimeValue,
    UnknownBlock,
)
from ygl_test_subject.api.results import CapabilityResult, ResultStatus
from ygl_test_subject.presentation.text import TextPresenter


class TextPresenterTests(unittest.TestCase):
    def test_tx01_renders_every_block_in_document_order(self):
        instant = TimeValue(datetime(2026, 9, 20, 12, 30, tzinfo=timezone.utc), "UTC")
        document = DisplayDocument(
            "Overview",
            "record-1",
            (
                TextBlock("plain text"),
                FieldsBlock({"status": "ready"}),
                MetricsBlock({"score": NumberValue("12.50", "points", 2)}),
                TableBlock(("name", "value"), (("row", 1),)),
                ItemGridBlock((GridItem("item", "shown"),)),
                ImageBlock("asset-1", "an image"),
                SeriesBlock(((instant, NumberValue("2.5", "units")),)),
                LinksBlock((Link("docs", "https://example.test/docs"),)),
                CommandsBlock(("/ygl sample help",)),
                UnknownBlock("future", fallback_text="future fallback"),
            ),
        )

        rendered = TextPresenter().render(document, max_chars=2000)

        self.assertIn("标题: Overview", rendered)
        self.assertIn("对象: record-1", rendered)
        for expected in (
            "plain text",
            "status: ready",
            "score: 12.50 points",
            "name | value",
            "row | 1",
            "item: shown",
            "an image",
            "2026-09-20T12:30:00+00:00 [UTC]",
            "docs: https://example.test/docs",
            "/ygl sample help",
            "future fallback",
        ):
            self.assertIn(expected, rendered)
        positions = [
            rendered.index(value) for value in ("plain text", "status: ready", "score:")
        ]
        self.assertEqual(positions, sorted(positions))

    def test_tx02_preserves_decimal_currency_unit_and_timezone(self):
        document = DisplayDocument(
            "Exact",
            "subject",
            (
                MetricsBlock(
                    {
                        "amount": MoneyValue(Decimal("1.2300"), "USD"),
                        "ratio": NumberValue(Decimal("0.100000"), "ratio"),
                        "rounded": NumberValue(Decimal("2.5"), "units", 3),
                    }
                ),
                FieldsBlock(
                    {
                        "at": TimeValue(
                            datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc), "UTC"
                        )
                    }
                ),
            ),
        )

        rendered = TextPresenter().render(document, max_chars=1000)

        self.assertIn("amount: 1.2300 USD", rendered)
        self.assertIn("ratio: 0.100000 ratio", rendered)
        self.assertIn("rounded: 2.500 units", rendered)
        self.assertIn("2026-01-02T03:04:00+00:00 [UTC]", rendered)
        self.assertNotIn("0.100000000000000", rendered)

    def test_tx02_large_exponents_and_rounding_carry_remain_exact(self):
        document = DisplayDocument(
            "Large",
            "subject",
            (
                MetricsBlock(
                    {
                        "large": NumberValue(Decimal("1E+100"), "u", 0),
                        "huge": NumberValue(Decimal("1E+1000000"), "u", 0),
                        "small": NumberValue(Decimal("1.234E-100"), "u", 2),
                        "carry": NumberValue(Decimal("9.99"), "u", 1),
                    }
                ),
            ),
        )

        rendered = TextPresenter().render(document, max_chars=300)

        self.assertIn("large: " + "1" + "0" * 100 + " u", rendered)
        self.assertIn("huge: 1E+1000000 u", rendered)
        self.assertIn("small: 0.00 u", rendered)
        self.assertIn("carry: 10.0 u", rendered)
        self.assertLessEqual(len(rendered), 300)

    def test_tx02_decimal_tuple_boundary_matrix_is_bounded_and_rounded_first(self):
        positive_carry = Decimal("9" * 300 + ".999")
        negative_carry = Decimal("-" + "9" * 300 + ".999")
        presenter = TextPresenter()

        for value, prefix in (
            (positive_carry, "carry: 1.000"),
            (negative_carry, "carry: -1.000"),
        ):
            with self.subTest(value="negative" if value.is_signed() else "positive"):
                document = DisplayDocument(
                    "Carry",
                    "subject",
                    (MetricsBlock({"carry": NumberValue(value, "u", 2)}),),
                )
                rendered = presenter.render(document, max_chars=100)
                self.assertTrue(rendered.endswith("[内容已截断]"))
                self.assertLessEqual(len(rendered), 100)
                self.assertIn(prefix, rendered)
                self.assertNotIn("9.999", rendered)

        half_even = (("2.345", "2.34"), ("2.355", "2.36"))
        non_tie = (("2.3451", "2.35"), ("2.3449", "2.34"))
        for value, expected in (
            half_even
            + non_tie
            + (
                ("-2.345", "-2.34"),
                ("-2.355", "-2.36"),
                ("-2.3451", "-2.35"),
                ("-2.3449", "-2.34"),
            )
        ):
            with self.subTest(value=value):
                document = DisplayDocument(
                    "Half even",
                    "subject",
                    (MetricsBlock({"value": NumberValue(Decimal(value), "u", 2)}),),
                )
                self.assertIn(
                    f"value: {expected} u", presenter.render(document, max_chars=200)
                )

        boundaries = (
            NumberValue(Decimal((0, (1,), MAX_EMAX)), "u", 0),
            NumberValue(Decimal((0, (9, 9), MAX_EMAX - 1)), "u", 0),
            NumberValue(Decimal((0, (1,), MIN_ETINY)), "u", 2),
            NumberValue(Decimal("1"), "u", 1_000_000),
            NumberValue(Decimal("1E-1000000"), "u"),
        )
        rendered = presenter.render(
            DisplayDocument(
                "Boundaries",
                "subject",
                (MetricsBlock({str(i): v for i, v in enumerate(boundaries)}),),
            ),
            max_chars=200,
        )
        self.assertLessEqual(len(rendered), 200)
        self.assertIn("1E+", rendered)
        self.assertIn("1E-1000000 u", rendered)
        self.assertIn("0.00 u", rendered)

        zero_document = DisplayDocument(
            "Zero",
            "subject",
            (
                MetricsBlock(
                    {
                        "scaled": NumberValue(Decimal("-0.000"), "u", 2),
                        "original": NumberValue(Decimal("-0.000"), "u"),
                    }
                ),
            ),
        )
        zero_text = presenter.render(zero_document, max_chars=200)
        self.assertIn("scaled: -0.00 u", zero_text)
        self.assertIn("original: -0.000 u", zero_text)

        table = DisplayDocument(
            "Nested",
            "subject",
            (
                TableBlock(
                    ("raw", "money"),
                    (
                        (
                            Decimal("1E-1000000"),
                            MoneyValue(Decimal("1E+1000000"), "USD", 2),
                        ),
                    ),
                ),
            ),
        )
        nested = presenter.render(table, max_chars=200)
        self.assertLessEqual(len(nested), 200)
        self.assertIn("1E-1000000", nested)
        self.assertIn("1E+1000000 USD", nested)

    def test_tx03_image_uses_alt_and_optional_unknown_uses_fallback(self):
        document = DisplayDocument(
            "Media",
            "subject",
            (
                ImageBlock("private-asset", "fallback image text"),
                UnknownBlock("new_kind", fallback_text="compatible fallback"),
            ),
        )

        rendered = TextPresenter().render(document, max_chars=500)

        self.assertIn("fallback image text", rendered)
        self.assertNotIn("private-asset", rendered)
        self.assertIn("compatible fallback", rendered)
        with self.assertRaises(ValueError):
            UnknownBlock("required_future", required=True)

    def test_tx04_text_and_recursive_values_are_literal(self):
        document = DisplayDocument(
            "Literal {title}",
            "<subject>",
            (
                TextBlock("value {not_a_template} <b>literal</b>"),
                FieldsBlock({"nested": {"x": ["{value}", "<tag>"]}}),
            ),
        )

        rendered = TextPresenter().render(document, max_chars=500)

        self.assertIn("value {not_a_template} <b>literal</b>", rendered)
        self.assertIn("{value}", rendered)
        self.assertIn("<tag>", rendered)
        self.assertNotIn("MappingProxyType", rendered)
        self.assertNotIn("'value'", rendered)

    def test_tx05_budget_validation_and_truncation_do_not_mutate_document(self):
        document = DisplayDocument("Long title", "subject", (TextBlock("0123456789"),))
        original = document.ordered_blocks
        presenter = TextPresenter()

        with self.assertRaises(TypeError):
            presenter.render(document, max_chars=True)
        for budget in (0, -1):
            with self.assertRaises(ValueError):
                presenter.render(document, max_chars=budget)

        notice = "[内容已截断]"
        with self.assertRaises(ValueError):
            presenter.render(document, max_chars=len(notice) - 1)
        rendered = presenter.render(document, max_chars=len(notice) + 3)
        self.assertLessEqual(len(rendered), len(notice) + 3)
        self.assertTrue(rendered.endswith(notice))
        self.assertEqual(document.ordered_blocks, original)
        self.assertNotIn(notice, presenter.render(document, max_chars=500))

    def test_tx06_private_document_can_render_without_model_facts_or_delivery(self):
        document = DisplayDocument(
            "Private",
            "owner-record",
            (TextBlock("private content"),),
            privacy=Privacy.PRIVATE,
        )

        rendered = TextPresenter().render(document, max_chars=200)
        result = CapabilityResult(
            "private-result",
            ResultStatus.SUCCESS,
            document=document,
            privacy=Privacy.PRIVATE,
        )

        self.assertIn("private content", rendered)
        self.assertIsNone(result.model_facts)
        self.assertEqual(result.document, document)


if __name__ == "__main__":
    unittest.main()
