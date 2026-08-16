import random
import unittest

from spaceflight_dumpster import (
    LOCATIONS,
    LOOT,
    hazard_chance,
    lose_half,
    resolve_loot,
    roll_loot,
)


class SpaceflightDumpsterTests(unittest.TestCase):
    def test_normal_search_finds_one_and_deep_search_finds_two(self) -> None:
        rng = random.Random(42)

        self.assertEqual(len(roll_loot(LOCATIONS[0], deep=False, rng=rng)), 1)
        self.assertEqual(len(roll_loot(LOCATIONS[0], deep=True, rng=rng)), 2)

    def test_all_locations_have_a_weight_for_every_item(self) -> None:
        for location in LOCATIONS:
            self.assertEqual(len(location.weights), len(LOOT))
            self.assertTrue(all(weight > 0 for weight in location.weights))

    def test_deep_search_adds_eighteen_percent_hazard_risk(self) -> None:
        for location in LOCATIONS:
            self.assertAlmostEqual(
                hazard_chance(location, deep=True),
                hazard_chance(location, deep=False) + 0.18,
            )

    def test_hazard_loses_half_the_haul_rounded_up(self) -> None:
        kept, lost = lose_half(
            {"ball_valve": 2, "mission_patch": 1},
            rng=random.Random(1),
        )

        self.assertEqual(sum(kept.values()), 1)
        self.assertEqual(sum(lost.values()), 2)
        self.assertEqual(sum(kept.values()) + sum(lost.values()), 3)

    def test_every_item_has_a_positive_automated_sale_price(self) -> None:
        self.assertTrue(all(item.coin_value > 0 for item in LOOT))

    def test_item_resolution_accepts_keys_and_display_names(self) -> None:
        self.assertEqual(resolve_loot("ball_valve"), resolve_loot("Ball Valve"))
        self.assertEqual(resolve_loot("s36_copv").key, "S36_COPV")
        self.assertIsNone(resolve_loot("not real"))


if __name__ == "__main__":
    unittest.main()
