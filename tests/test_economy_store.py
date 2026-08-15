import inspect
import unittest

from economy_store import (
    MAX_SECURITY_LEVEL,
    PostgresEconomyStore,
    rob_success_chance,
    security_protection_percent,
    security_upgrade_cost,
)


class EconomySecurityTests(unittest.TestCase):
    def test_postgres_hot_paths_are_async(self) -> None:
        for method_name in (
            "balance",
            "leaderboard",
            "work",
            "settle_wager",
            "upgrade_security",
            "gift",
            "rob",
        ):
            self.assertTrue(
                inspect.iscoroutinefunction(getattr(PostgresEconomyStore, method_name))
            )

    def test_security_protection_compounds_by_five_percent_per_tier(self) -> None:
        self.assertEqual(security_protection_percent(0), 0.0)
        self.assertAlmostEqual(security_protection_percent(1), 5.0)
        self.assertAlmostEqual(security_protection_percent(2), 5.25)
        self.assertAlmostEqual(rob_success_chance(1), 0.40)
        self.assertAlmostEqual(rob_success_chance(2), 0.3975)
        self.assertAlmostEqual(
            security_protection_percent(MAX_SECURITY_LEVEL),
            12.634750975,
        )

    def test_security_level_is_clamped_to_valid_range(self) -> None:
        self.assertEqual(security_protection_percent(-1), 0.0)
        self.assertEqual(
            security_protection_percent(MAX_SECURITY_LEVEL + 1),
            security_protection_percent(MAX_SECURITY_LEVEL),
        )

    def test_upgrade_cost_increases_by_tier(self) -> None:
        self.assertEqual(security_upgrade_cost(1), 500)
        self.assertEqual(security_upgrade_cost(2), 2_000)
        self.assertEqual(security_upgrade_cost(MAX_SECURITY_LEVEL), 200_000)


if __name__ == "__main__":
    unittest.main()
