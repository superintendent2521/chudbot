import inspect
import unittest
from unittest.mock import AsyncMock, Mock

from chudbot.economy.store import (
    DUMPSTER_COOLDOWN_SECONDS,
    EquipmentUseResult,
    MAX_DUMPSTER_SPEED_TIER,
    MAX_SECURITY_LEVEL,
    PostgresEconomyStore,
    dumpster_cooldown_seconds,
    dumpster_speed_upgrade_cost,
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
            "craft_inventory_item",
            "transfer_inventory_item",
            "consume_inventory_item",
            "use_inventory_equipment",
            "restore_equipment_use",
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
            "upgrade_dumpster_speed",
            "gift",
            "rob",
            "load_stock_market",
            "save_stock_market",
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

    def test_dumpster_cooldown_decreases_by_tier(self) -> None:
        self.assertEqual(
            dumpster_cooldown_seconds(0), DUMPSTER_COOLDOWN_SECONDS
        )
        self.assertEqual(dumpster_cooldown_seconds(1), DUMPSTER_COOLDOWN_SECONDS - 30)
        self.assertEqual(dumpster_cooldown_seconds(2), DUMPSTER_COOLDOWN_SECONDS - 60)
        self.assertEqual(dumpster_cooldown_seconds(3), DUMPSTER_COOLDOWN_SECONDS - 90)
        self.assertEqual(dumpster_cooldown_seconds(4), DUMPSTER_COOLDOWN_SECONDS - 120)
        self.assertEqual(
            dumpster_cooldown_seconds(-5),
            DUMPSTER_COOLDOWN_SECONDS,
        )
        self.assertEqual(
            dumpster_cooldown_seconds(MAX_DUMPSTER_SPEED_TIER + 5),
            dumpster_cooldown_seconds(MAX_DUMPSTER_SPEED_TIER),
        )

    def test_dumpster_speed_upgrade_cost_increases_by_tier(self) -> None:
        self.assertEqual(dumpster_speed_upgrade_cost(1), 500)
        self.assertEqual(dumpster_speed_upgrade_cost(2), 2_000)
        self.assertEqual(dumpster_speed_upgrade_cost(3), 4_500)
        self.assertEqual(dumpster_speed_upgrade_cost(4), 8_000)


class _ConnectionContext:
    def __init__(self, connection) -> None:
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class EconomyMarketTests(unittest.IsolatedAsyncioTestCase):
    async def test_crafting_consumes_ingredients_and_adds_output_atomically(self) -> None:
        store = PostgresEconomyStore.__new__(PostgresEconomyStore)
        connection = Mock()
        connection.execute = AsyncMock()
        store._connection = AsyncMock(return_value=_ConnectionContext(connection))
        store._ensure_accounts = AsyncMock()
        store._fetchall = AsyncMock(
            return_value=[
                {"item_key": "scrap_wiring", "quantity": 5},
                {"item_key": "thermal_blanket", "quantity": 1},
            ]
        )
        store._fetchone = AsyncMock(return_value={"quantity": 2})
        store._log = Mock()

        result = await store.craft_inventory_item(
            1,
            2,
            "insulated_cable",
            1,
            {"scrap_wiring": 3, "thermal_blanket": 1},
            recipe_key="insulated_cable",
            now=1_000,
        )

        self.assertEqual(result.status, "crafted")
        self.assertEqual(result.output_total, 2)
        statements = [call.args[0] for call in connection.execute.await_args_list]
        self.assertTrue(any("UPDATE economy_inventory" in sql for sql in statements))
        self.assertTrue(any("DELETE FROM economy_inventory" in sql for sql in statements))
        self.assertIn("INSERT INTO economy_inventory", store._fetchone.await_args.args[1])
        store._log.assert_called_once()

    async def test_load_stock_market_fetches_three_tables(self) -> None:
        store = PostgresEconomyStore.__new__(PostgresEconomyStore)
        connection = Mock()
        store._connection = AsyncMock(return_value=_ConnectionContext(connection))
        store._fetchall = AsyncMock(
            side_effect=[
                [{"symbol": "RKLB", "price": 4.2}],
                [{"user_id": 2, "cash": 900.0}],
                [{"user_id": 2, "symbol": "RKLB", "long_qty": 25}],
            ]
        )

        market, accounts, positions = await store.load_stock_market(1)

        self.assertEqual(market[0]["symbol"], "RKLB")
        self.assertEqual(accounts[0]["cash"], 900.0)
        self.assertEqual(positions[0]["long_qty"], 25)
        self.assertEqual(store._fetchall.await_count, 3)

    async def test_save_stock_market_upserts_every_table(self) -> None:
        store = PostgresEconomyStore.__new__(PostgresEconomyStore)
        connection = Mock()
        connection.execute = AsyncMock()
        store._connection = AsyncMock(return_value=_ConnectionContext(connection))

        market_rows = [{
            "symbol": "GD", "name": "General Dynamics", "sector": "Defense",
            "shares_outstanding": 5500, "base_price": 5.5, "price": 5.6,
            "prev_close": 5.5, "open_price": 5.5, "session_high": 5.6,
            "session_low": 5.5, "volume": 100, "traded_value": 550.0,
        }]
        account_rows = [{
            "user_id": 2, "cash": 800.0, "realized_pnl": 0.0, "trades": 1,
            "buys": 1, "sells": 0, "short_opens": 0, "covers": 0,
            "volume_bought": 100, "volume_sold": 0,
        }]
        position_rows = [{
            "user_id": 2, "symbol": "GD", "long_qty": 100, "long_avg_cost": 5.5,
            "short_qty": 0, "short_avg_entry": 0.0,
        }]

        await store.save_stock_market(
            1, market_rows, account_rows, position_rows, now=1_000
        )

        statements = [call.args[0] for call in connection.execute.await_args_list]
        self.assertIn("INSERT INTO economy_stock_market", statements[0])
        self.assertIn("INSERT INTO economy_stock_accounts", statements[1])
        self.assertIn("INSERT INTO economy_stock_positions", statements[2])
        self.assertTrue(all("ON CONFLICT" in sql for sql in statements))
        self.assertEqual(connection.execute.await_count, 3)

    async def test_crafting_reports_every_missing_ingredient_without_mutation(self) -> None:
        store = PostgresEconomyStore.__new__(PostgresEconomyStore)
        connection = Mock()
        connection.execute = AsyncMock()
        store._connection = AsyncMock(return_value=_ConnectionContext(connection))
        store._ensure_accounts = AsyncMock()
        store._fetchall = AsyncMock(
            return_value=[{"item_key": "scrap_wiring", "quantity": 1}]
        )
        store._fetchone = AsyncMock()
        store._log = Mock()

        result = await store.craft_inventory_item(
            1,
            2,
            "insulated_cable",
            1,
            {"scrap_wiring": 3, "thermal_blanket": 1},
            recipe_key="insulated_cable",
            now=1_000,
        )

        self.assertEqual(result.status, "insufficient")
        self.assertEqual(
            {(entry.item_key, entry.quantity) for entry in result.missing},
            {("scrap_wiring", 2), ("thermal_blanket", 1)},
        )
        connection.execute.assert_not_awaited()
        store._fetchone.assert_not_awaited()
        store._log.assert_not_called()

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

    async def test_timeout_restores_an_active_equipment_use(self) -> None:
        store = PostgresEconomyStore.__new__(PostgresEconomyStore)
        connection = Mock()
        connection.execute = AsyncMock()
        store._connection = AsyncMock(return_value=_ConnectionContext(connection))
        store._ensure_accounts = AsyncMock()
        store._fetchone = AsyncMock(
            side_effect=[{"user_id": 2}, {"uses_remaining": 3}]
        )
        store._log = Mock()

        restored = await store.restore_equipment_use(
            1,
            2,
            "flashlight",
            EquipmentUseResult("used", 3, False, 4),
            source="dumpster_timeout",
            now=1_000,
        )

        self.assertTrue(restored)
        statement, parameters = connection.execute.await_args.args
        self.assertIn("UPDATE economy_equipment_charges", statement)
        self.assertEqual(parameters[0], 4)
        store._log.assert_called_once()

    async def test_timeout_returns_a_newly_activated_item_to_inventory(self) -> None:
        store = PostgresEconomyStore.__new__(PostgresEconomyStore)
        connection = Mock()
        connection.execute = AsyncMock()
        store._connection = AsyncMock(return_value=_ConnectionContext(connection))
        store._ensure_accounts = AsyncMock()
        store._fetchone = AsyncMock(
            side_effect=[{"user_id": 2}, {"uses_remaining": 7}]
        )
        store._log = Mock()

        restored = await store.restore_equipment_use(
            1,
            2,
            "flashlight",
            EquipmentUseResult("used", 7, True, 8),
            source="dumpster_timeout",
            now=1_000,
        )

        self.assertTrue(restored)
        statements = [call.args[0] for call in connection.execute.await_args_list]
        self.assertTrue(
            any("DELETE FROM economy_equipment_charges" in sql for sql in statements)
        )
        self.assertTrue(any("INSERT INTO economy_inventory" in sql for sql in statements))
        store._log.assert_called_once()

    async def test_timeout_recreates_a_spent_final_equipment_charge(self) -> None:
        store = PostgresEconomyStore.__new__(PostgresEconomyStore)
        connection = Mock()
        connection.execute = AsyncMock()
        store._connection = AsyncMock(return_value=_ConnectionContext(connection))
        store._ensure_accounts = AsyncMock()
        store._fetchone = AsyncMock(side_effect=[{"user_id": 2}, None])
        store._log = Mock()

        restored = await store.restore_equipment_use(
            1,
            2,
            "flashlight",
            EquipmentUseResult("used", 0, False, 1),
            source="dumpster_timeout",
            now=1_000,
        )

        self.assertTrue(restored)
        statement, _parameters = connection.execute.await_args.args
        self.assertIn("INSERT INTO economy_equipment_charges", statement)
        self.assertIn("VALUES (%s, %s, %s, 1, %s, %s)", statement)
        store._log.assert_called_once()

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
