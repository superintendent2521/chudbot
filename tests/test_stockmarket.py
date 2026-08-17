"""Unit tests for the player-controlled stock market engine."""

import random
import unittest

from chudbot.economy.stockmarket import (
    INDEX_BASE,
    MIN_PRICE,
    STARTING_BALANCE,
    StockMarket,
    execute_stock_trade,
    load_stock_market,
    run_stock_trade,
    save_stock_market,
)


def _market(seed: int = 0) -> StockMarket:
    return StockMarket(rng=random.Random(seed))


class StockMarketSmokeTests(unittest.TestCase):
    def test_four_expected_tickers_are_listed(self) -> None:
        market = _market()
        self.assertEqual(set(market.stocks), {"RKLB", "LMT", "SPCX", "GD"})
        self.assertEqual(market.stocks["RKLB"].name, "Rocket Lab")
        self.assertTrue(all(s.shares_outstanding > 0 for s in market.stocks.values()))

    def test_initially_full_float_is_available(self) -> None:
        market = _market()
        for symbol, stock in market.stocks.items():
            self.assertEqual(market.float_available(symbol), stock.shares_outstanding)

    def test_index_starts_at_base(self) -> None:
        self.assertEqual(_market().index(), INDEX_BASE)

    def test_view_lists_every_ticker(self) -> None:
        text = _market().view()
        for symbol in ("RKLB", "LMT", "SPCX", "GD"):
            self.assertIn(symbol, text)


class BuySellTests(unittest.TestCase):
    def test_buy_debits_cash_and_increases_position(self) -> None:
        market = _market()
        price = market.stocks["RKLB"].price
        result = market.buy(1, "rklb", 100)  # symbol is case-insensitive
        self.assertTrue(result.accepted)
        self.assertEqual(result.symbol, "RKLB")
        account = market._players[1]
        self.assertEqual(account.longs["RKLB"].shares, 100)
        self.assertAlmostEqual(account.cash, STARTING_BALANCE - 100 * price)
        self.assertAlmostEqual(account.longs["RKLB"].avg_cost, price)

    def test_buy_raises_price_and_reduces_float(self) -> None:
        market = _market()
        before = market.stocks["RKLB"].price
        available_before = market.float_available("RKLB")
        market.buy(1, "RKLB", 200)  # ~$840, within the $1,000 starting cash
        self.assertGreater(market.stocks["RKLB"].price, before)
        self.assertEqual(market.float_available("RKLB"), available_before - 200)

    def test_buy_rejected_when_cash_is_insufficient(self) -> None:
        market = _market()
        # 1,000 LMT shares fit in the float but cost $8,750 > $1,000 cash.
        result = market.buy(1, "LMT", 1_000)
        self.assertFalse(result.accepted)
        self.assertIn("insufficient funds", result.reason)

    def test_sell_credits_cash_and_reduces_position(self) -> None:
        market = _market()
        market.buy(1, "RKLB", 100)
        result = market.sell(1, "RKLB", 40)
        self.assertTrue(result.accepted)
        account = market._players[1]
        self.assertEqual(account.longs["RKLB"].shares, 60)
        self.assertEqual(account.sells, 1)

    def test_sell_rejected_when_over_owned(self) -> None:
        market = _market()
        result = market.sell(1, "RKLB", 10)
        self.assertFalse(result.accepted)
        self.assertIn("you only own", result.reason)

    def test_average_cost_weights_multiple_buys(self) -> None:
        market = _market()
        first_price = market.stocks["GD"].price  # transaction price of buy #1
        market.buy(1, "GD", 50)
        buy_two_price = market.stocks["GD"].price  # transaction price of buy #2
        market.buy(1, "GD", 50)
        account = market._players[1]
        expected = (50 * first_price + 50 * buy_two_price) / 100
        self.assertAlmostEqual(account.longs["GD"].avg_cost, expected)


class ShortCoverTests(unittest.TestCase):
    def test_short_credits_proceeds_and_records_position(self) -> None:
        market = _market()
        result = market.short(1, "LMT", 10)
        self.assertTrue(result.accepted)
        self.assertGreater(result.amount, 0)  # proceeds were credited
        account = market._players[1]
        self.assertEqual(account.shorts["LMT"].quantity, 10)
        self.assertEqual(account.short_opens, 1)

    def test_short_requires_margin(self) -> None:
        market = _market()
        # LMT is $8.75; the 50% margin on 1,000 shares ($4,375) exceeds cash.
        result = market.short(1, "LMT", 1_000)
        self.assertFalse(result.accepted)
        self.assertIn("margin", result.reason)

    def test_short_rejected_when_float_is_exhausted(self) -> None:
        market = _market()
        market.stocks["SPCX"].shares_outstanding = 5
        market.buy(1, "SPCX", 5)  # take the entire (tiny) float
        result = market.short(2, "SPCX", 1)
        self.assertFalse(result.accepted)
        self.assertIn("shares can be borrowed", result.reason)

    def test_cover_returns_shares_and_closes_position(self) -> None:
        market = _market()
        market.short(1, "SPCX", 20)
        result = market.cover(1, "SPCX", 20)
        self.assertTrue(result.accepted)
        account = market._players[1]
        self.assertEqual(account.covers, 1)
        self.assertNotIn("SPCX", account.shorts)

    def test_cover_rejected_when_nothing_is_short(self) -> None:
        market = _market()
        result = market.cover(1, "GD", 5)
        self.assertFalse(result.accepted)
        self.assertIn("shares short", result.reason)

    def test_neutral_short_round_trip_has_zero_realized_pnl(self) -> None:
        market = _market()
        market.short(1, "RKLB", 100)
        account = market._players[1]
        entry = account.shorts["RKLB"].avg_entry
        # Force the price back to the exact entry before covering so the
        # realized P&L is provably zero.
        market.stocks["RKLB"].price = entry
        result = market.cover(1, "RKLB", 100)
        self.assertTrue(result.accepted)
        self.assertAlmostEqual(account.realized_pnl, 0.0, places=6)


class EdgeCaseTests(unittest.TestCase):
    def test_unknown_ticker_raises(self) -> None:
        with self.assertRaises(KeyError):
            _market().buy(1, "NOPE", 1)

    def test_buy_rejected_beyond_float(self) -> None:
        market = _market()
        available = market.float_available("SPCX")
        result = market.buy(1, "SPCX", available + 1)
        self.assertFalse(result.accepted)
        self.assertGreaterEqual(market.float_available("SPCX"), 0)

    def test_price_never_drops_below_minimum(self) -> None:
        market = _market()
        for _ in range(500):
            if market.float_available("RKLB") > 0:
                market.short(2, "RKLB", 1)
        self.assertGreaterEqual(market.stocks["RKLB"].price, MIN_PRICE)

    def test_nonpositive_quantity_is_rejected(self) -> None:
        market = _market()
        self.assertFalse(market.buy(1, "RKLB", 0).accepted)
        self.assertFalse(market.sell(1, "RKLB", -5).accepted)
        self.assertFalse(market.short(1, "RKLB", 0).accepted)
        self.assertFalse(market.cover(1, "RKLB", -5).accepted)

    def test_statistics_reports_account_data(self) -> None:
        market = _market()
        market.buy(1, "RKLB", 100)
        text = market.statistics(1)
        self.assertIn("RKLB", text)
        self.assertIn("Net worth", text)


class FakeStore:
    """Minimal async stand-in for the PostgreSQL stock-market store."""

    def __init__(self) -> None:
        self.saved: tuple = ([], [], [])
        self.saved_guild: int = 0
        self.save_count: int = 0
        self.stock_logs: list[dict] = []

    def log_stock_trade(self, guild_id: int, user_id: int, **details) -> None:
        self.stock_logs.append({"guild_id": guild_id, "user_id": user_id, **details})

    async def load_stock_market(self, guild_id: int):
        if not self.saved or self.saved_guild != guild_id:
            return [], [], []
        return self.saved

    async def save_stock_market(
        self,
        guild_id: int,
        market_rows,
        account_rows,
        position_rows,
        *,
        now=None,
    ) -> None:
        self.saved = (list(market_rows), list(account_rows), list(position_rows))
        self.saved_guild = guild_id
        self.save_count += 1


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    def test_small_float_moves_for_a_single_player(self) -> None:
        # ~30-player sizing: one meaningful order visibly moves the ticker.
        market = _market()
        before = market.stocks["GD"].price
        market.buy(1, "GD", 150)  # ~$825, within starting cash; ~2.7% of float
        self.assertGreater(market.stocks["GD"].price, before * 1.01)

    def test_serialize_then_hydrate_round_trips_state(self) -> None:
        market = _market()
        market.buy(7, "RKLB", 120)
        market.short(7, "LMT", 40)
        account = market._players[7]
        account.cash = 123.45
        account.realized_pnl = 9.99

        rebuilt = StockMarket(rng=random.Random(1))
        rebuilt.hydrate(
            market.serialize_market(),
            market.serialize_accounts(),
            market.serialize_positions(),
        )

        self.assertEqual(rebuilt.stocks["RKLB"].price, market.stocks["RKLB"].price)
        self.assertEqual(rebuilt._players[7].cash, 123.45)
        self.assertEqual(rebuilt._players[7].realized_pnl, 9.99)
        self.assertEqual(rebuilt._players[7].longs["RKLB"].shares, 120)
        self.assertEqual(rebuilt._players[7].shorts["LMT"].quantity, 40)

    async def test_load_execute_save_round_trip_through_fake_store(self) -> None:
        store = FakeStore()
        market = await load_stock_market(store, 11)
        result, market = await execute_stock_trade(
            store, market, 11, 3, "buy", "SPCX", 100
        )
        self.assertTrue(result.accepted)
        self.assertEqual(store.save_count, 1)
        self.assertEqual(store.stock_logs[0]["action"], "buy")
        self.assertEqual(store.stock_logs[0]["symbol"], "SPCX")
        self.assertEqual(store.stock_logs[0]["quantity"], 100)
        self.assertEqual(store.stock_logs[0]["amount"], -700)

        # A fresh market hydrated from the persisted rows sees the same stake.
        reloaded = await load_stock_market(store, 11)
        self.assertEqual(reloaded._players[3].longs["SPCX"].shares, 100)

    async def test_reload_uses_persisted_wallet_balance(self) -> None:
        market = StockMarket()
        market.hydrate(
            [],
            [{"user_id": 3, "balance": 180049}],
            [],
        )
        self.assertEqual(market._players[3].cash, 180049)

    async def test_rejected_trade_is_not_persisted(self) -> None:
        store = FakeStore()
        market = await load_stock_market(store, 12)
        result, market = await execute_stock_trade(
            store, market, 12, 3, "sell", "GD", 50
        )
        self.assertFalse(result.accepted)
        self.assertEqual(store.save_count, 0)
        self.assertEqual(store.stock_logs, [])

    def test_run_stock_trade_dispatches_and_rejects_unknown_action(self) -> None:
        market = _market()
        self.assertTrue(run_stock_trade(market, 1, "buy", "RKLB", 10).accepted)
        with self.assertRaises(ValueError):
            run_stock_trade(market, 1, "bogus", "RKLB", 10)


if __name__ == "__main__":
    unittest.main()
