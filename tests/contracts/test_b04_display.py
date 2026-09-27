"""Renderer-neutral display contract probes for B04-C."""

import inspect
import unittest
from typing import get_type_hints

from ygl_test_subject.api.display import (
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
    Privacy,
    TextBlock,
    UnknownBlock,
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
            DisplayDocument(
                "Public",
                "Subject",
                (ImageBlock("asset-1", "secret", visibility=Privacy.PRIVATE),),
            )
        with self.assertRaises(ValueError):
            DisplayDocument(
                "Public",
                "Subject",
                (
                    ItemGridBlock(
                        (
                            GridItem(
                                "secret", asset_id="asset-1", visibility=Privacy.PRIVATE
                            ),
                        )
                    ),
                ),
            )
        private = DisplayDocument(
            "Private", "Subject", (TextBlock("secret"),), privacy=Privacy.PRIVATE
        )
        self.assertEqual(private.privacy, Privacy.PRIVATE)

    def test_unknown_optional_fallback_and_required_unknown_policy(self):
        fallback = UnknownBlock("future-card", fallback_text="Summary")
        self.assertEqual(fallback.fallback_text, "Summary")
        with self.assertRaises(ValueError):
            UnknownBlock("future-card", required=True)
        with self.assertRaises(TypeError):
            DisplayDocument("Title", "Subject", (object(),))

    def test_output_and_limits_are_validated_and_have_no_product_default(self):
        self.assertEqual(
            DisplayOutput("Rendered", ("asset-1",)).resource_ids, ("asset-1",)
        )
        with self.assertRaises(ValueError):
            DisplayOutput("Rendered", ("../private",))
        with self.assertRaises(ValueError):
            DisplayOutput("Rendered", ("asset-1", "asset-1"))
        with self.assertRaises(ValueError):
            DisplayLimits(0, 100)
        self.assertEqual(DisplayLimits(2, 1024).max_pages, 2)
        self.assertIs(
            inspect.signature(DisplayRenderer.render).parameters["limits"].default,
            inspect.Parameter.empty,
        )

    def test_display_values_remain_renderer_neutral(self):
        doc = DisplayDocument(
            "Title", "Subject", (FieldsBlock({"status": "ready"}), TextBlock("Done"))
        )
        self.assertEqual(
            tuple(block.kind for block in doc.ordered_blocks), ("fields", "text")
        )

    def test_c03_batch_preserves_order_and_public_audience_cannot_promote_private(self):
        first = DisplayBatchMember(
            DigestMember("sub-1", 1, "event-1", 1),
            DisplayDocument("First", "Subject", (TextBlock("one"),)),
        )
        private = DisplayBatchMember(
            DigestMember("sub-2", 1, "event-2", 1),
            DisplayDocument(
                "Second", "Subject", (TextBlock("two"),), privacy=Privacy.PRIVATE
            ),
        )
        public_batch = DisplayBatch((first,), DisplayAudience.PUBLIC)
        self.assertEqual(public_batch.members, (first,))
        private_batch = DisplayBatch((first, private), DisplayAudience.PRIVATE)
        self.assertEqual(private_batch.members, (first, private))
        with self.assertRaises(ValueError):
            DisplayBatch((first, private), DisplayAudience.PUBLIC)
        with self.assertRaises(ValueError):
            DisplayBatch((first, first), DisplayAudience.PRIVATE)
        with self.assertRaises(TypeError):
            DisplayBatch((object(),), DisplayAudience.PRIVATE)


if __name__ == "__main__":
    unittest.main()
