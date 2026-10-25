"""Public cards remain pure DTOs; Core owns admission and persistence."""

import json
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path

from ygl_test_subject.core.contracts.cards import (
    decode_card,
    encode_card,
    validate_card,
)
from ygl_test_subject.core.contracts.validation_boundary import validate_contract
from ygl_test_subject.core.invocation import Gateway
from ygl_test_subject.extensions.disk_manifest import parse_manifest
from ygl_test_subject.infrastructure.sqlite.repositories_subscriptions import (
    _dump,
    _load,
)

import yomihime_game_link_sdk as sdk
from yomihime_game_link_sdk import cards


class CardContractTests(unittest.TestCase):
    def card(self):
        return cards.Document(
            "Result",
            (
                cards.Table(
                    (cards.Column("Name"),),
                    (
                        (
                            cards.Cell(
                                cards.Inline((cards.Span("Value", color="#0070ff"),))
                            ),
                        ),
                    ),
                ),
            ),
            logo=cards.Asset("png", b"\x89PNG\r\n\x1a\nexample", "logo", False),
        )

    def test_sdk_imports_without_core_or_imaging(self):
        root = str(Path(__file__).resolve().parents[2])
        code = """import sys
sys.path.insert(0, sys.argv[1])
import yomihime_game_link_sdk as sdk
from yomihime_game_link_sdk import cards
assert sdk.DisplayDocument("x", "y", ()).card is None
assert cards.Document("x").title == "x"
assert not any(n == "PIL" or n.startswith(("PIL.", "presentation.", "core.", "astrbot.", "resvg_py")) for n in sys.modules)
"""
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-c", code, root],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_canonical_presentation_type_identity(self):
        from ygl_test_subject.presentation import layout_assets, layout_document

        self.assertIs(layout_document.Document, cards.Document)
        self.assertIs(layout_assets.Asset, cards.Asset)

    def test_gateway_preserves_detached_card_and_separate_facts(self):
        original = self.card()
        document = sdk.DisplayDocument(
            "Result", "subject", (sdk.TextBlock("fallback"),), card=original
        )
        facts = sdk.FactDocument({"value": 42})
        result = sdk.CapabilityResult(
            "sample", sdk.ResultStatus.SUCCESS, document, facts
        )
        checked = Gateway._valid_result(result, expected_privacy=sdk.Privacy.PUBLIC)
        self.assertEqual(checked.document.card, original)
        self.assertIsNot(checked.document.card, original)
        self.assertEqual(dict(checked.model_facts.facts), {"value": 42})
        self.assertEqual(checked.document.ordered_blocks[0].text, "fallback")

    def test_storage_roundtrip_preserves_card_bytes_and_privacy(self):
        document = sdk.DisplayDocument(
            "Result",
            "subject",
            (sdk.TextBlock("fallback"),),
            privacy=sdk.Privacy.PRIVATE,
            card=self.card(),
        )
        encoded = _dump(document)
        self.assertIn('"$type":"cards.Document.v1"', encoded)
        self.assertIn('"$card_type":"Document"', encoded)
        restored = _load(encoded)
        self.assertEqual(restored, document)
        self.assertIs(restored.privacy, sdk.Privacy.PRIVATE)
        self.assertIs(type(restored.card.logo.content), bytes)

    def test_existing_user_mapping_key_is_not_reinterpreted_as_card(self):
        original = {"$card": {"version": 2, "content": "ordinary user data"}}
        self.assertEqual(_load(_dump(original)), original)

    def test_old_current_document_without_card_reads_without_migration(self):
        document = sdk.DisplayDocument("Old", "subject", (sdk.TextBlock("fallback"),))
        encoded = json.loads(_dump(document))
        self.assertNotIn("card", encoded["fields"])
        self.assertEqual(_load(json.dumps(encoded)), document)

    def test_closed_card_graph_is_admitted_before_copy_or_persistence(self):
        forged = type("Forged", (cards.Text,), {})("no")
        cycle = cards.Section("cycle")
        object.__setattr__(cycle, "blocks", (cycle,))
        values = (
            replace(self.card(), blocks=[cards.Text("list")]),
            replace(self.card(), blocks=(forged,)),
            replace(self.card(), blocks=(cycle,)),
            replace(self.card(), width=True),
            replace(self.card(), theme="unknown"),
            replace(
                self.card(), title=cards.Inline((cards.Span("x", "#ffffff", "gold"),))
            ),
            replace(self.card(), logo=cards.Asset("png", "path.png")),
            replace(self.card(), blocks=(cards.Text("x" * 4097),)),
            replace(
                self.card(), blocks=(cards.Row((cards.Text("x"),), (float("nan"),)),)
            ),
        )
        for card in values:
            with self.subTest(card=type(card)):
                document = sdk.DisplayDocument("Bad", "subject", (), card=card)
                with self.assertRaises((TypeError, ValueError)):
                    validate_contract(document)
                with self.assertRaises((TypeError, ValueError)):
                    Gateway._rebuild_document(document)
                with self.assertRaises((TypeError, ValueError)):
                    _dump(document)

    def test_explicit_tagged_text_roundtrip_and_plain_strings_are_literal(self):
        tagged = cards.TaggedText("[purple]score[/purple] [gold glow]100.0%[/gold]")
        document = cards.Document("[literal title]", (cards.Text(tagged),))
        self.assertEqual(decode_card(encode_card(document)), document)
        self.assertEqual(
            Gateway._rebuild_document(
                sdk.DisplayDocument("x", "y", (), card=document)
            ).card,
            document,
        )
        for source in (
            "[unknown]x[/unknown]",
            "[purple]unclosed",
            "x" * 4097,
            "[b][gold glow]x[/b][/gold]",
        ):
            with self.subTest(source=source[:20]):
                with self.assertRaises(ValueError):
                    validate_card(
                        cards.Document("title", (cards.Text(cards.TaggedText(source)),))
                    )

    def test_unknown_storage_does_not_silently_drop_card(self):
        encoded = encode_card(self.card())
        for payload in (
            {**encoded, "version": 2},
            {**encoded, "version": True},
            {**encoded, "document": {"$card_type": "Unknown", "fields": {}}},
            {
                **encoded,
                "document": {"$card_type": "Document", "fields": {"title": "x"}},
            },
        ):
            with self.assertRaises(ValueError):
                decode_card(payload)
        self.assertEqual(validate_card(self.card()), self.card())

    def test_collage_roundtrip_and_closed_resource_visibility(self):
        image = cards.ResourceImage("library/570", alt="cover", required=False)
        original = cards.Document(
            "Library",
            (
                cards.WeightedCollage(
                    (
                        cards.CollageItem("570", 4, image, "Dota", "Game"),
                        cards.CollageItem("1091500", 1, None, "Cyberpunk", "Game"),
                    )
                ),
            ),
        )
        self.assertEqual(decode_card(encode_card(original)), original)
        self.assertEqual(validate_card(original), original)
        payload = encode_card(original)
        node = payload["document"]["fields"]["blocks"][0]["fields"]["items"][0]
        node["fields"]["image"]["fields"]["visibility"] = {
            "$card_visibility": "private"
        }
        with self.assertRaises(ValueError):
            decode_card(payload)

    def test_collage_rejects_invalid_weights_keys_and_nested_position(self):
        valid = cards.CollageItem("a", 1)
        invalid = (
            cards.WeightedCollage((valid, cards.CollageItem("a", 2))),
            cards.WeightedCollage((cards.CollageItem("b", 0),)),
            cards.WeightedCollage((cards.CollageItem("b", float("nan")),)),
            cards.WeightedCollage((cards.CollageItem("b", float("inf")),)),
        )
        for node in invalid:
            with self.subTest(node=node), self.assertRaises(ValueError):
                validate_card(cards.Document("Library", (node,)))
        with self.assertRaises(ValueError):
            validate_card(
                cards.Document(
                    "Library", (cards.Section("x", (cards.WeightedCollage((valid,)),)),)
                )
            )

    def test_collage_local_asset_count_does_not_raise_ordinary_limit(self):
        image = cards.Asset("png", b"\x89PNG\r\n\x1a\nexample", required=False)
        collage = cards.Document(
            "Library",
            (
                cards.WeightedCollage(
                    tuple(cards.CollageItem(str(i), 1, image) for i in range(200))
                ),
            ),
        )
        self.assertEqual(validate_card(collage), collage)
        self.assertEqual(decode_card(encode_card(collage)), collage)
        ordinary = cards.Document(
            "Ordinary", tuple(cards.Image(image) for _ in range(33))
        )
        with self.assertRaises(ValueError):
            validate_card(ordinary)

    def test_logo_reference_must_be_declared_static_resource(self):
        for value in ("https://example.test/logo.svg", "../logo.svg", "script.js"):
            with self.assertRaises(ValueError):
                validate_contract(sdk.ModuleDisplay("Name", logo_resource=value))
        manifest = json.loads(
            (
                Path(__file__).resolve().parents[2]
                / "examples/offline_sample/manifest.json"
            ).read_text()
        )
        module = manifest["modules"][0]
        module["display"] = {"default_name": "Name", "logo_resource": "assets/logo.svg"}
        with self.assertRaises(ValueError):
            parse_manifest(json.dumps(manifest).encode())
        module["resources"] = [{"path": "assets/logo.svg", "sha256": "0" * 64}]
        parsed = parse_manifest(json.dumps(manifest).encode())
        self.assertEqual(parsed.modules[0].display.logo_resource, "assets/logo.svg")
