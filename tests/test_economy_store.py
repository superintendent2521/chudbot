import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from economy_store import (
    ROB_ACTIVITY_WINDOW_SECONDS,
    ROB_COOLDOWN_SECONDS,
    SQLiteEconomyStore,
    STARTING_BALANCE,
    WORK_COOLDOWN_SECONDS,
)


class EconomyStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteEconomyStore(Path(self.temp_dir.name) / "economy.db")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_new_account_receives_starting_balance(self) -> None:
        self.assertEqual(self.store.balance(1, 10, now=100), STARTING_BALANCE)

    def test_balances_are_scoped_to_guild(self) -> None:
        self.store.work(1, 10, 100, now=100)
        self.assertEqual(self.store.peek_balance(1, 10), STARTING_BALANCE + 100)
        self.assertIsNone(self.store.peek_balance(2, 10))

    def test_work_pays_and_enforces_cooldown(self) -> None:
        first = self.store.work(1, 10, 125, now=100)
        blocked = self.store.work(1, 10, 125, now=101)
        later = self.store.work(1, 10, 125, now=100 + WORK_COOLDOWN_SECONDS)
        self.assertEqual(first.balance, STARTING_BALANCE + 125)
        self.assertEqual(blocked.retry_after, WORK_COOLDOWN_SECONDS - 1)
        self.assertEqual(blocked.balance, first.balance)
        self.assertEqual(later.balance, STARTING_BALANCE + 250)

    def test_gambling_wins_and_loses_exact_wager(self) -> None:
        won = self.store.gamble(1, 10, 50, True, now=100)
        lost = self.store.gamble(1, 10, 75, False, now=101)
        self.assertTrue(won.accepted)
        self.assertEqual(won.balance, STARTING_BALANCE + 50)
        self.assertEqual(lost.balance, STARTING_BALANCE - 25)

    def test_gambling_cannot_overdraw(self) -> None:
        result = self.store.gamble(1, 10, STARTING_BALANCE + 1, True, now=100)
        self.assertFalse(result.accepted)
        self.assertEqual(result.balance, STARTING_BALANCE)

    def test_wager_supports_custom_payouts(self) -> None:
        jackpot = self.store.settle_wager(1, 10, 25, profit=250, now=100)
        loss = self.store.settle_wager(1, 10, 50, profit=-50, now=101)

        self.assertTrue(jackpot.accepted)
        self.assertEqual(jackpot.profit, 250)
        self.assertEqual(loss.balance, STARTING_BALANCE + 200)

    def test_wager_cannot_lose_more_than_the_stake(self) -> None:
        with self.assertRaises(ValueError):
            self.store.settle_wager(1, 10, 25, profit=-26, now=100)

    def test_non_positive_wager_is_rejected(self) -> None:
        result = self.store.gamble(1, 10, -10, True, now=100)

        self.assertFalse(result.accepted)
        self.assertEqual(result.balance, STARTING_BALANCE)

    def test_rob_requires_target_activity_within_fifteen_minutes(self) -> None:
        self.store.balance(1, 20, now=100)
        active = self.store.rob(
            1, 10, 20, succeeded=True, steal_percent=20, fine_percent=10, now=100 + ROB_ACTIVITY_WINDOW_SECONDS
        )
        self.assertEqual(active.status, "success")

        self.store.balance(1, 30, now=100)
        inactive = self.store.rob(
            1, 11, 30, succeeded=True, steal_percent=20, fine_percent=10, now=101 + ROB_ACTIVITY_WINDOW_SECONDS
        )
        self.assertEqual(inactive.status, "inactive")

    def test_successful_robbery_transfers_money_and_has_cooldown(self) -> None:
        self.store.balance(1, 20, now=100)
        result = self.store.rob(
            1, 10, 20, succeeded=True, steal_percent=20, fine_percent=10, now=101
        )
        self.assertEqual(result.status, "success")
        self.assertEqual(result.amount, 50)
        self.assertEqual(result.robber_balance, 300)
        self.assertEqual(result.target_balance, 200)

        blocked = self.store.rob(
            1, 10, 20, succeeded=True, steal_percent=20, fine_percent=10, now=102
        )
        self.assertEqual(blocked.status, "cooldown")
        self.assertEqual(blocked.retry_after, ROB_COOLDOWN_SECONDS - 1)

    def test_successful_robbery_has_no_coin_cap(self) -> None:
        self.store.work(1, 20, 10_000, now=100)

        result = self.store.rob(
            1, 10, 20, succeeded=True, steal_percent=20, fine_percent=10, now=101
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.amount, 2_050)
        self.assertEqual(result.target_balance, 8_200)

    def test_failed_robbery_pays_fine_to_target(self) -> None:
        self.store.balance(1, 20, now=100)
        result = self.store.rob(
            1, 10, 20, succeeded=False, steal_percent=20, fine_percent=10, now=101
        )
        self.assertEqual(result.status, "caught")
        self.assertEqual(result.amount, 25)
        self.assertEqual(result.robber_balance, 225)
        self.assertEqual(result.target_balance, 275)

    def test_viewing_someone_does_not_make_them_active(self) -> None:
        self.store.balance(1, 10, now=100)
        self.assertIsNone(self.store.peek_balance(1, 99))
        result = self.store.rob(
            1, 10, 99, succeeded=True, steal_percent=20, fine_percent=10, now=101
        )
        self.assertEqual(result.status, "inactive")

    def test_leaderboard_returns_top_ten_and_requesting_user_rank(self) -> None:
        for user_id in range(1, 13):
            self.store.work(1, user_id, user_id * 10, now=100)

        result = self.store.leaderboard(1, 2, now=200)

        self.assertEqual(len(result.entries), 10)
        self.assertEqual([entry.user_id for entry in result.entries], list(range(12, 2, -1)))
        self.assertEqual([entry.rank for entry in result.entries], list(range(1, 11)))
        self.assertEqual(result.user_rank, 11)
        self.assertEqual(result.user_balance, STARTING_BALANCE + 20)

    def test_leaderboard_is_guild_scoped_and_orders_ties_by_user_id(self) -> None:
        self.store.balance(1, 20, now=100)
        self.store.balance(1, 10, now=100)
        self.store.work(2, 99, 1_000, now=100)

        result = self.store.leaderboard(1, 20, now=200)

        self.assertEqual([entry.user_id for entry in result.entries], [10, 20])
        self.assertEqual(result.user_rank, 2)

    def test_concurrent_writes_remain_atomic(self) -> None:
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(
                executor.map(
                    lambda _: self.store.gamble(1, 10, 10, False, now=100),
                    range(20),
                )
            )

        self.assertTrue(all(result.accepted for result in results))
        self.assertEqual(self.store.peek_balance(1, 10), STARTING_BALANCE - 200)


if __name__ == "__main__":
    unittest.main()
