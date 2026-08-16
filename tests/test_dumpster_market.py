"""Tests for unified salvage/crafted item resolution used by market commands."""

import unittest

from chudbot.commands.dumpster import (
    CRAFTED_ITEMS_BY_KEY,
    LOOT_BY_KEY,
    _automated_value_text,
    _order_item_name,
    _resolve_market_item,
)


class MarketItemResolutionTests(unittest.TestCase):
    def test_resolves_salvaged_item_by_key(self) -> None:
        item = _resolve_market_item("ball_valve")
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.key, "ball_valve")
        self.assertEqual(item.name, "Ball Valve")
        self.assertFalse(item.crafted)
        self.assertIsNotNone(item.fixed_value)

    def test_resolves_salvaged_item_by_display_name(self) -> None:
        self.assertEqual(
            _resolve_market_item("Ball Valve").key, _resolve_market_item("ball_valve").key
        )

    def test_resolves_crafted_item_by_key_and_name(self) -> None:
        crafted = _resolve_market_item("repair_kit")
        self.assertIsNotNone(crafted)
        assert crafted is not None
        self.assertTrue(crafted.crafted)
        self.assertEqual(crafted.name, CRAFTED_ITEMS_BY_KEY["repair_kit"].name)
        self.assertIsNone(crafted.fixed_value)
        self.assertIsNone(crafted.rarity)
        self.assertEqual(
            _resolve_market_item("Orbital Repair Kit").key,
            _resolve_market_item("repair_kit").key,
        )

    def test_every_crafted_item_resolves(self) -> None:
        for crafted in CRAFTED_ITEMS_BY_KEY.values():
            item = _resolve_market_item(crafted.key)
            self.assertIsNotNone(item)
            assert item is not None
            self.assertTrue(item.crafted)
            self.assertEqual(item.key, crafted.key)

    def test_unknown_item_returns_none(self) -> None:
        self.assertIsNone(_resolve_market_item("definitely_not_real"))

    def test_order_item_name_prefers_loot_then_crafted_then_raw_key(self) -> None:
        self.assertEqual(_order_item_name("ball_valve"), LOOT_BY_KEY["ball_valve"].name)
        self.assertEqual(
            _order_item_name("repair_kit"), CRAFTED_ITEMS_BY_KEY["repair_kit"].name
        )
        self.assertEqual(_order_item_name("made_up_key"), "Made Up Key")

    def test_automated_value_text_handles_missing_fixed_value(self) -> None:
        for key in LOOT_BY_KEY:
            item = _resolve_market_item(key)
            assert item is not None
            if item.fixed_value is not None:
                self.assertIn("coins", _automated_value_text(item))
        crafted = _resolve_market_item("repair_kit")
        assert crafted is not None
        self.assertIn("No automated", _automated_value_text(crafted))


if __name__ == "__main__":
    unittest.main()