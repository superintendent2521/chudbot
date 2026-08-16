import inspect
import unittest
from unittest.mock import AsyncMock, Mock

from chudbot.economy.store import (
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
            "statistics",
            "work",
            "settle_wager",
            "pay_reserved_wager",
            "credit_activity_reward",
            "credit_message_reward",
            "inventory",
            "equipment_uses",
            "equipment_availability",
            "inventory_is_private",
            "set_inventory_private",
            "add_inventory_items",
            "transfer_inventory_item",
            "consume_inventory_item",
            "use_inventory_equipment",
            "sell_inventory_item",
            "create_buy_order",
            "buy_orders",
            "expire_buy_orders",
            "fill_buy_order",
            "cancel_buy_order",
            "market_sales",
            "start_activity",
            "loan_status",
            "take_loan",
            "repay_loan",
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


class _ConnectionContext:
    def __init__(self, connection) -> None:
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class EconomyMarketTests(unittest.IsolatedAsyncioTestCase):
    async def test_consumable_is_removed_and_logged(self) -> None:
        store = PostgresEconomyStore.__new__(PostgresEconomyStore)
        connection = Mock()
        connection.execute = AsyncMock()
        store._connection = AsyncMock(return_value=_ConnectionContext(connection))
        store._fetchone = AsyncMock(return_value={"quantity": 2})
        store._log = Mock()

        result = await store.consume_inventory_item(
            1, 2, "flashlight", source="dumpster_equipment", now=1_000
        )

        self.assertEqual(result.status, "consumed")
        self.assertEqual(result.remaining, 1)
        connection.execute.assert_awaited_once()
        self.assertIn("UPDATE economy_inventory", connection.execute.await_args.args[0])
        store._log.assert_called_once()

    async def test_equipment_activation_consumes_one_item_and_stores_uses(self) -> None:
        store = PostgresEconomyStore.__new__(PostgresEconomyStore)
        connection = Mock()
        connection.execute = AsyncMock()
        store._connection = AsyncMock(return_value=_ConnectionContext(connection))
        store._ensure_accounts = AsyncMock()
        store._fetchone = AsyncMock(
            side_effect=[{"user_id": 2}, None, {"quantity": 2}]
        )
        store._log = Mock()

        result = await store.use_inventory_equipment(
            1, 2, "flashlight", 8, source="dumpster_equipment", now=1_000
        )

        self.assertEqual(result.status, "used")
        self.assertTrue(result.activated_new)
        self.assertEqual(result.uses_remaining, 7)
        statements = [call.args[0] for call in connection.execute.await_args_list]
        self.assertTrue(any("UPDATE economy_inventory" in sql for sql in statements))
        self.assertTrue(
            any("INSERT INTO economy_equipment_charges" in sql for sql in statements)
        )
        store._log.assert_called_once()

    async def test_active_equipment_spends_a_use_without_consuming_inventory(self) -> None:
        store = PostgresEconomyStore.__new__(PostgresEconomyStore)
        connection = Mock()
        connection.execute = AsyncMock()
        store._connection = AsyncMock(return_value=_ConnectionContext(connection))
        store._ensure_accounts = AsyncMock()
        store._fetchone = AsyncMock(
            side_effect=[{"user_id": 2}, {"uses_remaining": 4}]
        )
        store._log = Mock()

        result = await store.use_inventory_equipment(
            1, 2, "flashlight", 10, source="dumpster_equipment", now=1_000
        )

        self.assertEqual(result.status, "used")
        self.assertFalse(result.activated_new)
        self.assertEqual(result.uses_remaining, 3)
        connection.execute.assert_awaited_once()
        statement = connection.execute.await_args.args[0]
        self.assertIn("UPDATE economy_equipment_charges", statement)
        self.assertNotIn("economy_inventory SET", statement)

    async def test_expired_orders_refund_each_buyer_and_are_logged(self) -> None:
        store = PostgresEconomyStore.__new__(PostgresEconomyStore)
        connection = Mock()
        connection.execute = AsyncMock()
        store._connection = AsyncMock(
            return_value=_ConnectionContext(connection)
        )
        store._fetchall = AsyncMock(
            side_effect=[
                [
                    {"id": 10, "buyer_id": 2, "quantity_remaining": 3, "price_each": 5},
                    {"id": 11, "buyer_id": 2, "quantity_remaining": 1, "price_each": 7},
                    {"id": 12, "buyer_id": 4, "quantity_remaining": 2, "price_each": 9},
                ],
                [{"user_id": 2}, {"user_id": 4}],
            ]
        )
        store._ensure_accounts = AsyncMock()
        store._log = Mock()

        expired = await store.expire_buy_orders(1, now=1_000)

        self.assertEqual(expired, 3)
        self.assertEqual(connection.execute.await_count, 3)
        self.assertEqual(connection.execute.await_args_list[1].args[1], (22, 1, 2))
        self.assertEqual(connection.execute.await_args_list[2].args[1], (18, 1, 4))
        self.assertEqual(store._log.call_count, 2)


if __name__ == "__main__":
    unittest.main()
