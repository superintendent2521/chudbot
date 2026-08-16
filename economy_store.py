"""Asynchronous, transaction-safe PostgreSQL storage for the guild economy."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Literal, Optional

from economy_logging import EconomyLogRecord, EconomyLogWriter


STARTING_BALANCE = 250
WORK_COOLDOWN_SECONDS = 3 * 60
ROB_COOLDOWN_SECONDS = 10 * 60
ROB_ACTIVITY_WINDOW_SECONDS = 15 * 60
LOAN_TERM_SECONDS = 60 * 60
MAX_LOAN_AMOUNT = 5_000
FISH_COOLDOWN_SECONDS = 5 * 60
MEMORY_COOLDOWN_SECONDS = 5 * 60
BOUNTY_COOLDOWN_SECONDS = 10 * 60
DEFAULT_POSTGRES_URL = "postgresql://postgres:postgres@postgres/economy"
BASE_ROB_SUCCESS_PERCENT = 45.0
MAX_SECURITY_LEVEL = 20
BASE_SECURITY_PROTECTION_PERCENT = 5.0
SECURITY_PROTECTION_GROWTH = 1.05


def security_protection_percent(level: int) -> float:
    """Return the percentage-point robbery penalty for a security tier."""
    level = min(MAX_SECURITY_LEVEL, max(0, int(level)))
    if level == 0:
        return 0.0
    return BASE_SECURITY_PROTECTION_PERCENT * SECURITY_PROTECTION_GROWTH ** (level - 1)


def rob_success_chance(level: int) -> float:
    """Return a robber's success probability against the given security tier."""
    protection = security_protection_percent(level)
    return max(0.0, (BASE_ROB_SUCCESS_PERCENT - protection) / 100.0)


def security_upgrade_cost(level: int) -> int:
    """Return the cost of purchasing a specific security tier."""
    level = min(MAX_SECURITY_LEVEL, max(1, int(level)))
    return 500 * level * level


@dataclass(frozen=True)
class WorkResult:
    earned: int
    balance: int
    retry_after: int = 0
    gross_earned: int = 0
    garnished: int = 0
    loan_remaining: int = 0


@dataclass(frozen=True)
class GambleResult:
    accepted: bool
    won: bool
    amount: int
    balance: int


@dataclass(frozen=True)
class WagerResult:
    accepted: bool
    amount: int
    profit: int
    balance: int


@dataclass(frozen=True)
class GiftResult:
    accepted: bool
    amount: int
    giver_balance: int
    recipient_balance: Optional[int] = None


@dataclass(frozen=True)
class RobResult:
    status: Literal["success", "caught", "inactive", "broke", "cooldown"]
    amount: int
    robber_balance: int
    target_balance: Optional[int] = None
    retry_after: int = 0


@dataclass(frozen=True)
class LeaderboardEntry:
    rank: int
    user_id: int
    balance: int


@dataclass(frozen=True)
class LeaderboardResult:
    entries: tuple[LeaderboardEntry, ...]
    user_rank: int
    user_balance: int


@dataclass(frozen=True)
class SecurityUpgradeResult:
    status: Literal["upgraded", "insufficient", "maxed"]
    level: int
    cost: int
    balance: int
    protection_percent: float


@dataclass(frozen=True)
class EconomyStatistics:
    guilds: int
    accounts: int
    total_balance: int
    average_balance: int
    highest_balance: int


@dataclass(frozen=True)
class ActivityStartResult:
    started: bool
    balance: int
    retry_after: int = 0


@dataclass(frozen=True)
class LoanResult:
    status: Literal["borrowed", "active", "repaid", "none", "invalid"]
    balance: int
    loan_balance: int
    loan_due: Optional[int]
    amount: int = 0


class PostgresEconomyStore:
    """Async PostgreSQL economy store backed by a reusable connection pool."""

    def __init__(
        self,
        database_url: str,
        *,
        min_pool_size: int = 5,
        max_pool_size: int = 10,
        log_queue_size: int = 10_000,
        log_batch_size: int = 100,
    ) -> None:
        if not database_url:
            raise ValueError("A PostgreSQL database URL is required")
        try:
            from psycopg.rows import dict_row
            from psycopg_pool import AsyncConnectionPool
        except ImportError as error:
            raise RuntimeError(
                'PostgreSQL support requires: pip install "psycopg[binary,pool]>=3.2,<4"'
            ) from error

        min_pool_size = max(1, int(min_pool_size))
        max_pool_size = max(min_pool_size, int(max_pool_size))
        self._pool = AsyncConnectionPool(
            conninfo=database_url,
            min_size=min_pool_size,
            max_size=max_pool_size,
            timeout=10,
            kwargs={
                "row_factory": dict_row,
                "prepare_threshold": 3,
                "application_name": "chudite-economy",
            },
            open=False,
            name="economy",
        )
        self._open_lock = asyncio.Lock()
        self._opened = False
        self._log_writer = EconomyLogWriter(
            database_url,
            queue_size=log_queue_size,
            batch_size=log_batch_size,
        )

    async def open(self) -> None:
        """Open the pool and apply idempotent schema migrations once."""
        if self._opened:
            return
        async with self._open_lock:
            if self._opened:
                return
            await self._pool.open(wait=True, timeout=30)
            await self._initialize()
            self._opened = True
            try:
                await self._log_writer.start()
            except Exception:
                # Audit logging is explicitly lower priority than the economy.
                # A logger outage must not prevent the store from opening.
                logging.getLogger("chuds.bot.economy-log").exception(
                    "Economy audit logger unavailable; economy remains online"
                )

    async def close(self) -> None:
        if self._opened:
            await self._log_writer.close()
            await self._pool.close()
            self._opened = False

    async def _initialize(self) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS economy_accounts (
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    balance BIGINT NOT NULL DEFAULT 250 CHECK (balance >= 0),
                    last_activity BIGINT,
                    last_work BIGINT,
                    last_rob BIGINT,
                    last_fish BIGINT,
                    last_memory BIGINT,
                    last_bounty BIGINT,
                    loan_balance BIGINT NOT NULL DEFAULT 0 CHECK (loan_balance >= 0),
                    loan_due BIGINT,
                    security_level INTEGER NOT NULL DEFAULT 0
                        CHECK (security_level BETWEEN 0 AND 20),
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            await connection.execute(
                """ALTER TABLE economy_accounts
                   ADD COLUMN IF NOT EXISTS security_level INTEGER NOT NULL DEFAULT 0
                   CHECK (security_level BETWEEN 0 AND 20)"""
            )
            await connection.execute(
                "ALTER TABLE economy_accounts ADD COLUMN IF NOT EXISTS last_fish BIGINT"
            )
            await connection.execute(
                "ALTER TABLE economy_accounts ADD COLUMN IF NOT EXISTS last_memory BIGINT"
            )
            await connection.execute(
                "ALTER TABLE economy_accounts ADD COLUMN IF NOT EXISTS last_bounty BIGINT"
            )
            await connection.execute(
                """ALTER TABLE economy_accounts ADD COLUMN IF NOT EXISTS loan_balance BIGINT
                   NOT NULL DEFAULT 0 CHECK (loan_balance >= 0)"""
            )
            await connection.execute(
                "ALTER TABLE economy_accounts ADD COLUMN IF NOT EXISTS loan_due BIGINT"
            )
            await connection.execute(
                """CREATE INDEX IF NOT EXISTS economy_leaderboard_idx
                   ON economy_accounts (guild_id, balance DESC, user_id ASC)"""
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS economy_log (
                    id BIGSERIAL PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    counterparty_id BIGINT,
                    amount BIGINT NOT NULL,
                    balance_after BIGINT NOT NULL,
                    counterparty_balance_after BIGINT,
                    occurred_at BIGINT NOT NULL,
                    details JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await connection.execute(
                """CREATE INDEX IF NOT EXISTS economy_log_guild_time_idx
                   ON economy_log (guild_id, occurred_at DESC)"""
            )

    def _log(
        self,
        event_type: str,
        guild_id: int,
        user_id: int,
        amount: int,
        balance_after: int,
        occurred_at: int,
        *,
        counterparty_id: Optional[int] = None,
        counterparty_balance_after: Optional[int] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        self._log_writer.enqueue(
            EconomyLogRecord(
                event_type=event_type,
                guild_id=guild_id,
                user_id=user_id,
                counterparty_id=counterparty_id,
                amount=amount,
                balance_after=balance_after,
                counterparty_balance_after=counterparty_balance_after,
                occurred_at=occurred_at,
                details=details,
            )
        )

    async def _connection(self) -> Any:
        await self.open()
        return self._pool.connection()

    @staticmethod
    def _now(now: Optional[int]) -> int:
        return int(time.time() if now is None else now)

    @staticmethod
    async def _fetchone(connection: Any, query: str, parameters: tuple[Any, ...]) -> Any:
        cursor = await connection.execute(query, parameters)
        return await cursor.fetchone()

    @staticmethod
    async def _fetchall(connection: Any, query: str, parameters: tuple[Any, ...]) -> list[Any]:
        cursor = await connection.execute(query, parameters)
        return await cursor.fetchall()

    @staticmethod
    async def _ensure_accounts(connection: Any, guild_id: int, *user_ids: int) -> None:
        placeholders = ", ".join(["(%s, %s)"] * len(user_ids))
        parameters: list[int] = []
        for user_id in user_ids:
            parameters.extend((guild_id, user_id))
        await connection.execute(
            f"""INSERT INTO economy_accounts (guild_id, user_id)
                VALUES {placeholders} ON CONFLICT DO NOTHING""",
            tuple(parameters),
        )

    async def balance(self, guild_id: int, user_id: int, *, now: Optional[int] = None) -> int:
        timestamp = self._now(now)
        connection_context = await self._connection()
        async with connection_context as connection:
            row = await self._fetchone(
                connection,
                """INSERT INTO economy_accounts (guild_id, user_id, last_activity)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (guild_id, user_id) DO UPDATE
                   SET last_activity = EXCLUDED.last_activity
                   RETURNING balance""",
                (guild_id, user_id, timestamp),
            )
            return int(row["balance"])

    async def peek_balance(self, guild_id: int, user_id: int) -> Optional[int]:
        connection_context = await self._connection()
        async with connection_context as connection:
            row = await self._fetchone(
                connection,
                """SELECT balance FROM economy_accounts
                   WHERE guild_id = %s AND user_id = %s""",
                (guild_id, user_id),
            )
            return None if row is None else int(row["balance"])

    async def leaderboard(
        self,
        guild_id: int,
        user_id: int,
        *,
        limit: int = 10,
        now: Optional[int] = None,
    ) -> LeaderboardResult:
        """Fetch the top accounts and viewer rank in one database round trip."""
        timestamp = self._now(now)
        limit = max(1, int(limit))
        connection_context = await self._connection()
        async with connection_context as connection:
            rows = await self._fetchall(
                connection,
                """WITH viewer AS (
                       INSERT INTO economy_accounts (guild_id, user_id, last_activity)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (guild_id, user_id) DO UPDATE
                       SET last_activity = EXCLUDED.last_activity
                       RETURNING user_id, balance
                   ), all_accounts AS (
                       SELECT user_id, balance FROM economy_accounts
                       WHERE guild_id = %s AND user_id <> %s
                       UNION ALL
                       SELECT user_id, balance FROM viewer
                   ), ranked AS (
                       SELECT user_id, balance,
                              ROW_NUMBER() OVER (ORDER BY balance DESC, user_id ASC) AS rank
                       FROM all_accounts
                   ), annotated AS (
                       SELECT user_id, balance, rank,
                              MAX(rank) FILTER (WHERE user_id = %s) OVER () AS user_rank,
                              MAX(balance) FILTER (WHERE user_id = %s) OVER () AS user_balance
                       FROM ranked
                   )
                   SELECT user_id, balance, rank, user_rank, user_balance
                   FROM annotated
                   WHERE rank <= %s OR user_id = %s
                   ORDER BY rank""",
                (guild_id, user_id, timestamp, guild_id, user_id, user_id, user_id, limit, user_id),
            )
            if not rows:
                raise RuntimeError("Leaderboard query returned no accounts")
            entries = tuple(
                LeaderboardEntry(int(row["rank"]), int(row["user_id"]), int(row["balance"]))
                for row in rows
                if int(row["rank"]) <= limit
            )
            return LeaderboardResult(
                entries,
                int(rows[0]["user_rank"]),
                int(rows[0]["user_balance"]),
            )

    async def statistics(self) -> EconomyStatistics:
        """Return global economy totals in one aggregate query."""
        connection_context = await self._connection()
        async with connection_context as connection:
            row = await self._fetchone(
                connection,
                """SELECT COUNT(DISTINCT guild_id) AS guilds,
                          COUNT(*) AS accounts,
                          COALESCE(SUM(balance), 0) AS total_balance,
                          COALESCE(ROUND(AVG(balance)), 0) AS average_balance,
                          COALESCE(MAX(balance), 0) AS highest_balance
                   FROM economy_accounts""",
                (),
            )
            return EconomyStatistics(
                guilds=int(row["guilds"]),
                accounts=int(row["accounts"]),
                total_balance=int(row["total_balance"]),
                average_balance=int(row["average_balance"]),
                highest_balance=int(row["highest_balance"]),
            )

    async def work(
        self,
        guild_id: int,
        user_id: int,
        reward: int,
        *,
        now: Optional[int] = None,
    ) -> WorkResult:
        """Settle work and its cooldown in one database round trip."""
        timestamp = self._now(now)
        reward = max(0, int(reward))
        connection_context = await self._connection()
        async with connection_context as connection:
            row = await self._fetchone(
                connection,
                """WITH inserted AS (
                       INSERT INTO economy_accounts
                           (guild_id, user_id, balance, last_activity, last_work)
                       VALUES (%s, %s, %s + %s, %s, %s)
                       ON CONFLICT DO NOTHING
                       RETURNING balance, TRUE AS worked, 0::BIGINT AS retry_after,
                                 %s::BIGINT AS gross_earned, 0::BIGINT AS garnished,
                                 0::BIGINT AS loan_remaining
                   ), work_values AS (
                       SELECT guild_id, user_id,
                              CASE WHEN loan_balance > 0 AND loan_due <= %s
                                   THEN LEAST(loan_balance, (%s + 1) / 2)
                                   ELSE 0 END::BIGINT AS garnishment
                       FROM economy_accounts
                       WHERE guild_id = %s AND user_id = %s
                         AND NOT EXISTS (SELECT 1 FROM inserted)
                         AND (last_work IS NULL OR last_work <= %s - %s)
                       FOR UPDATE
                   ), worked AS (
                       UPDATE economy_accounts AS account
                       SET balance = account.balance + %s - work_values.garnishment,
                           loan_balance = account.loan_balance - work_values.garnishment,
                           loan_due = CASE
                               WHEN account.loan_balance - work_values.garnishment = 0 THEN NULL
                               ELSE account.loan_due END,
                           last_activity = %s,
                           last_work = %s
                       FROM work_values
                       WHERE account.guild_id = work_values.guild_id
                         AND account.user_id = work_values.user_id
                       RETURNING account.balance, TRUE AS worked, 0::BIGINT AS retry_after,
                                 %s::BIGINT AS gross_earned,
                                 work_values.garnishment AS garnished,
                                 account.loan_balance AS loan_remaining
                   ), cooling_down AS (
                       UPDATE economy_accounts
                       SET last_activity = %s
                       WHERE guild_id = %s AND user_id = %s
                         AND NOT EXISTS (SELECT 1 FROM inserted)
                         AND NOT EXISTS (SELECT 1 FROM worked)
                       RETURNING balance, FALSE AS worked,
                           GREATEST(1, %s - (%s - last_work))::BIGINT AS retry_after,
                           0::BIGINT AS gross_earned, 0::BIGINT AS garnished,
                           loan_balance AS loan_remaining
                   )
                   SELECT * FROM inserted
                   UNION ALL SELECT * FROM worked
                   UNION ALL SELECT * FROM cooling_down""",
                (
                    guild_id, user_id, STARTING_BALANCE, reward, timestamp, timestamp, reward,
                    timestamp, reward, guild_id, user_id, timestamp, WORK_COOLDOWN_SECONDS,
                    reward, timestamp, timestamp, reward,
                    timestamp, guild_id, user_id, WORK_COOLDOWN_SECONDS, timestamp,
                ),
            )
            result = None
            if row is not None:
                worked = bool(row["worked"])
                gross_earned = int(row["gross_earned"])
                garnished = int(row["garnished"])
                result = WorkResult(
                    gross_earned - garnished if worked else 0,
                    int(row["balance"]),
                    int(row["retry_after"]),
                    gross_earned,
                    garnished,
                    int(row["loan_remaining"]),
                )
        if result is None:  # Rare first-use race: retry after the competing insert commits.
            return await self.work(guild_id, user_id, reward, now=timestamp)
        if result.gross_earned:
            self._log(
                "work", guild_id, user_id, result.earned, result.balance, timestamp,
                details={
                    "gross_earned": result.gross_earned,
                    "garnished": result.garnished,
                    "loan_remaining": result.loan_remaining,
                },
            )
        return result

    async def gamble(
        self,
        guild_id: int,
        user_id: int,
        amount: int,
        won: bool,
        *,
        now: Optional[int] = None,
    ) -> GambleResult:
        result = await self.settle_wager(
            guild_id,
            user_id,
            amount,
            profit=amount if won else -amount,
            now=now,
        )
        return GambleResult(
            result.accepted,
            won if result.accepted else False,
            result.amount,
            result.balance,
        )

    async def settle_wager(
        self,
        guild_id: int,
        user_id: int,
        amount: int,
        *,
        profit: int,
        now: Optional[int] = None,
    ) -> WagerResult:
        """Atomically settle a wager in one database round trip."""
        timestamp = self._now(now)
        amount = int(amount)
        profit = int(profit)
        if amount > 0 and profit < -amount:
            raise ValueError("A wager cannot lose more than its amount")
        accepted_at_start = amount > 0 and amount <= STARTING_BALANCE
        starting_result = STARTING_BALANCE + profit if accepted_at_start else STARTING_BALANCE
        connection_context = await self._connection()
        async with connection_context as connection:
            row = await self._fetchone(
                connection,
                """WITH inserted AS (
                       INSERT INTO economy_accounts
                           (guild_id, user_id, balance, last_activity)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT DO NOTHING
                       RETURNING balance, %s::BOOLEAN AS accepted
                   ), settled AS (
                       UPDATE economy_accounts
                       SET balance = balance + %s, last_activity = %s
                       WHERE guild_id = %s AND user_id = %s
                         AND NOT EXISTS (SELECT 1 FROM inserted)
                         AND %s > 0 AND balance >= %s
                       RETURNING balance, TRUE AS accepted
                   ), rejected AS (
                       UPDATE economy_accounts SET last_activity = %s
                       WHERE guild_id = %s AND user_id = %s
                         AND NOT EXISTS (SELECT 1 FROM inserted)
                         AND NOT EXISTS (SELECT 1 FROM settled)
                       RETURNING balance, FALSE AS accepted
                   )
                   SELECT * FROM inserted
                   UNION ALL SELECT * FROM settled
                   UNION ALL SELECT * FROM rejected""",
                (
                    guild_id, user_id, starting_result, timestamp, accepted_at_start,
                    profit, timestamp, guild_id, user_id, amount, amount,
                    timestamp, guild_id, user_id,
                ),
            )
            result = None
            if row is not None:
                accepted = bool(row["accepted"])
                result = WagerResult(
                    accepted, amount, profit if accepted else 0, int(row["balance"])
                )
        if result is None:
            return await self.settle_wager(
                guild_id, user_id, amount, profit=profit, now=timestamp
            )
        if result.accepted:
            self._log(
                "wager", guild_id, user_id, result.profit, result.balance, timestamp,
                details={"wager": result.amount},
            )
        return result

    async def pay_reserved_wager(
        self,
        guild_id: int,
        user_id: int,
        payout: int,
        *,
        now: Optional[int] = None,
    ) -> int:
        """Credit the payout for a wager that was deducted before interactive play."""
        timestamp = self._now(now)
        payout = max(0, int(payout))
        connection_context = await self._connection()
        async with connection_context as connection:
            row = await self._fetchone(
                connection,
                """UPDATE economy_accounts
                   SET balance = balance + %s, last_activity = %s
                   WHERE guild_id = %s AND user_id = %s
                   RETURNING balance""",
                (payout, timestamp, guild_id, user_id),
            )
            if row is None:
                raise RuntimeError("Reserved wager account no longer exists")
            balance = int(row["balance"])
        self._log("wager_payout", guild_id, user_id, payout, balance, timestamp)
        return balance

    async def credit_activity_reward(
        self,
        guild_id: int,
        user_id: int,
        amount: int,
        *,
        now: Optional[int] = None,
    ) -> int:
        """Credit a non-gambling game reward and mark the player active."""
        timestamp = self._now(now)
        amount = max(0, int(amount))
        connection_context = await self._connection()
        async with connection_context as connection:
            row = await self._fetchone(
                connection,
                """INSERT INTO economy_accounts
                       (guild_id, user_id, balance, last_activity)
                   VALUES (%s, %s, %s + %s, %s)
                   ON CONFLICT (guild_id, user_id) DO UPDATE
                   SET balance = economy_accounts.balance + EXCLUDED.balance - %s,
                       last_activity = EXCLUDED.last_activity
                   RETURNING balance""",
                (guild_id, user_id, STARTING_BALANCE, amount, timestamp, STARTING_BALANCE),
            )
            balance = int(row["balance"])
        self._log("activity_reward", guild_id, user_id, amount, balance, timestamp)
        return balance

    async def credit_message_reward(self, guild_id: int, user_id: int, amount: int) -> int:
        """Silently credit a buffered message reward without affecting rob activity."""
        amount = max(0, int(amount))
        timestamp = self._now(None)
        connection_context = await self._connection()
        async with connection_context as connection:
            row = await self._fetchone(
                connection,
                """INSERT INTO economy_accounts (guild_id, user_id, balance)
                   VALUES (%s, %s, %s + %s)
                   ON CONFLICT (guild_id, user_id) DO UPDATE
                   SET balance = economy_accounts.balance + EXCLUDED.balance - %s
                   RETURNING balance""",
                (guild_id, user_id, STARTING_BALANCE, amount, STARTING_BALANCE),
            )
            balance = int(row["balance"])
        self._log("message_reward", guild_id, user_id, amount, balance, timestamp)
        return balance

    async def start_activity(
        self,
        guild_id: int,
        user_id: int,
        activity: Literal["fish", "memory", "bounty"],
        *,
        now: Optional[int] = None,
    ) -> ActivityStartResult:
        """Atomically consume an activity cooldown before starting a game."""
        settings = {
            "fish": ("last_fish", FISH_COOLDOWN_SECONDS),
            "memory": ("last_memory", MEMORY_COOLDOWN_SECONDS),
            "bounty": ("last_bounty", BOUNTY_COOLDOWN_SECONDS),
        }
        column, cooldown = settings[activity]
        timestamp = self._now(now)
        connection_context = await self._connection()
        async with connection_context as connection:
            await self._ensure_accounts(connection, guild_id, user_id)
            row = await self._fetchone(
                connection,
                f"""SELECT balance, {column} AS last_used FROM economy_accounts
                    WHERE guild_id = %s AND user_id = %s FOR UPDATE""",
                (guild_id, user_id),
            )
            last_used = row["last_used"]
            if last_used is not None:
                retry_after = cooldown - (timestamp - int(last_used))
                if retry_after > 0:
                    await self._touch(connection, guild_id, user_id, timestamp)
                    return ActivityStartResult(False, int(row["balance"]), retry_after)
            await connection.execute(
                f"""UPDATE economy_accounts SET {column} = %s, last_activity = %s
                    WHERE guild_id = %s AND user_id = %s""",
                (timestamp, timestamp, guild_id, user_id),
            )
            return ActivityStartResult(True, int(row["balance"]))

    async def loan_status(
        self,
        guild_id: int,
        user_id: int,
        *,
        now: Optional[int] = None,
    ) -> LoanResult:
        timestamp = self._now(now)
        connection_context = await self._connection()
        async with connection_context as connection:
            row = await self._fetchone(
                connection,
                """INSERT INTO economy_accounts (guild_id, user_id, last_activity)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (guild_id, user_id) DO UPDATE
                   SET last_activity = EXCLUDED.last_activity
                   RETURNING balance, loan_balance, loan_due""",
                (guild_id, user_id, timestamp),
            )
            loan_balance = int(row["loan_balance"])
            return LoanResult(
                "active" if loan_balance else "none",
                int(row["balance"]),
                loan_balance,
                None if row["loan_due"] is None else int(row["loan_due"]),
            )

    async def take_loan(
        self,
        guild_id: int,
        user_id: int,
        amount: int,
        *,
        now: Optional[int] = None,
    ) -> LoanResult:
        timestamp = self._now(now)
        amount = int(amount)
        if amount < 100 or amount > MAX_LOAN_AMOUNT:
            status = await self.loan_status(guild_id, user_id, now=timestamp)
            return LoanResult(
                "invalid", status.balance, status.loan_balance, status.loan_due, amount
            )
        connection_context = await self._connection()
        async with connection_context as connection:
            await self._ensure_accounts(connection, guild_id, user_id)
            row = await self._fetchone(
                connection,
                """SELECT balance, loan_balance, loan_due FROM economy_accounts
                   WHERE guild_id = %s AND user_id = %s FOR UPDATE""",
                (guild_id, user_id),
            )
            if int(row["loan_balance"]):
                await self._touch(connection, guild_id, user_id, timestamp)
                return LoanResult(
                    "active",
                    int(row["balance"]),
                    int(row["loan_balance"]),
                    None if row["loan_due"] is None else int(row["loan_due"]),
                )
            due = timestamp + LOAN_TERM_SECONDS
            updated = await self._fetchone(
                connection,
                """UPDATE economy_accounts
                   SET balance = balance + %s, loan_balance = %s, loan_due = %s,
                       last_activity = %s
                   WHERE guild_id = %s AND user_id = %s
                   RETURNING balance""",
                (amount, amount, due, timestamp, guild_id, user_id),
            )
            result = LoanResult("borrowed", int(updated["balance"]), amount, due, amount)
        self._log(
            "loan_borrowed", guild_id, user_id, amount, result.balance, timestamp,
            details={"loan_balance": result.loan_balance, "loan_due": due},
        )
        return result

    async def repay_loan(
        self,
        guild_id: int,
        user_id: int,
        amount: Optional[int] = None,
        *,
        now: Optional[int] = None,
    ) -> LoanResult:
        timestamp = self._now(now)
        connection_context = await self._connection()
        async with connection_context as connection:
            await self._ensure_accounts(connection, guild_id, user_id)
            row = await self._fetchone(
                connection,
                """SELECT balance, loan_balance, loan_due FROM economy_accounts
                   WHERE guild_id = %s AND user_id = %s FOR UPDATE""",
                (guild_id, user_id),
            )
            balance = int(row["balance"])
            loan_balance = int(row["loan_balance"])
            loan_due = None if row["loan_due"] is None else int(row["loan_due"])
            if not loan_balance:
                await self._touch(connection, guild_id, user_id, timestamp)
                return LoanResult("none", balance, 0, None)
            requested = loan_balance if amount is None else int(amount)
            if requested <= 0 or balance <= 0:
                await self._touch(connection, guild_id, user_id, timestamp)
                return LoanResult("invalid", balance, loan_balance, loan_due, requested)
            payment = min(requested, balance, loan_balance)
            loan_balance -= payment
            balance -= payment
            updated_due = loan_due if loan_balance else None
            await connection.execute(
                """UPDATE economy_accounts
                   SET balance = %s, loan_balance = %s, loan_due = %s, last_activity = %s
                   WHERE guild_id = %s AND user_id = %s""",
                (balance, loan_balance, updated_due, timestamp, guild_id, user_id),
            )
            result = LoanResult("repaid", balance, loan_balance, updated_due, payment)
        self._log(
            "loan_repaid", guild_id, user_id, -payment, result.balance, timestamp,
            details={"payment": payment, "loan_balance": result.loan_balance},
        )
        return result

    async def upgrade_security(
        self,
        guild_id: int,
        user_id: int,
        *,
        now: Optional[int] = None,
    ) -> SecurityUpgradeResult:
        """Buy the next tier in one database round trip."""
        timestamp = self._now(now)
        connection_context = await self._connection()
        async with connection_context as connection:
            row = await self._fetchone(
                connection,
                """WITH inserted AS (
                       INSERT INTO economy_accounts (guild_id, user_id, last_activity)
                       VALUES (%s, %s, %s)
                       ON CONFLICT DO NOTHING
                       RETURNING 'insufficient'::TEXT AS status, security_level, balance,
                                 500::BIGINT AS cost
                   ), upgraded AS (
                       UPDATE economy_accounts
                       SET balance = balance - (500 * (security_level + 1) * (security_level + 1)),
                           security_level = security_level + 1,
                           last_activity = %s
                       WHERE guild_id = %s AND user_id = %s
                         AND NOT EXISTS (SELECT 1 FROM inserted)
                         AND security_level < %s
                         AND balance >= (500 * (security_level + 1) * (security_level + 1))
                       RETURNING 'upgraded'::TEXT AS status, security_level, balance,
                                 (500 * security_level * security_level)::BIGINT AS cost
                   ), unchanged AS (
                       UPDATE economy_accounts SET last_activity = %s
                       WHERE guild_id = %s AND user_id = %s
                         AND NOT EXISTS (SELECT 1 FROM inserted)
                         AND NOT EXISTS (SELECT 1 FROM upgraded)
                       RETURNING CASE WHEN security_level >= %s THEN 'maxed' ELSE 'insufficient' END AS status,
                                 security_level, balance,
                                 CASE WHEN security_level >= %s THEN 0
                                      ELSE 500 * (security_level + 1) * (security_level + 1)
                                 END::BIGINT AS cost
                   )
                   SELECT * FROM inserted
                   UNION ALL SELECT * FROM upgraded
                   UNION ALL SELECT * FROM unchanged""",
                (
                    guild_id, user_id, timestamp,
                    timestamp, guild_id, user_id, MAX_SECURITY_LEVEL,
                    timestamp, guild_id, user_id, MAX_SECURITY_LEVEL, MAX_SECURITY_LEVEL,
                ),
            )
            result = None
            if row is not None:
                level = int(row["security_level"])
                result = SecurityUpgradeResult(
                    str(row["status"]),  # type: ignore[arg-type]
                    level,
                    int(row["cost"]),
                    int(row["balance"]),
                    security_protection_percent(level),
                )
        if result is None:
            return await self.upgrade_security(guild_id, user_id, now=timestamp)
        if result.status == "upgraded":
            self._log(
                "security_upgrade", guild_id, user_id, -result.cost,
                result.balance, timestamp, details={"level": result.level},
            )
        return result

    async def gift(
        self,
        guild_id: int,
        giver_id: int,
        recipient_id: int,
        amount: int,
        *,
        now: Optional[int] = None,
    ) -> GiftResult:
        """Transfer coins while locking both accounts in a stable order."""
        timestamp = self._now(now)
        amount = int(amount)
        connection_context = await self._connection()
        async with connection_context as connection:
            await self._ensure_accounts(connection, guild_id, giver_id, recipient_id)
            rows = await self._fetchall(
                connection,
                """SELECT user_id, balance FROM economy_accounts
                   WHERE guild_id = %s AND user_id IN (%s, %s)
                   ORDER BY user_id FOR UPDATE""",
                (guild_id, giver_id, recipient_id),
            )
            balances = {int(row["user_id"]): int(row["balance"]) for row in rows}
            giver_balance = balances[giver_id]
            recipient_balance = balances[recipient_id]
            if amount <= 0 or amount > giver_balance:
                await connection.execute(
                    """UPDATE economy_accounts SET last_activity = %s
                       WHERE guild_id = %s AND user_id = %s""",
                    (timestamp, guild_id, giver_id),
                )
                return GiftResult(False, amount, giver_balance, recipient_balance)

            giver_balance -= amount
            recipient_balance += amount
            await connection.execute(
                """UPDATE economy_accounts
                   SET balance = CASE WHEN user_id = %s THEN %s ELSE %s END,
                       last_activity = CASE WHEN user_id = %s THEN %s ELSE last_activity END
                   WHERE guild_id = %s AND user_id IN (%s, %s)""",
                (
                    giver_id, giver_balance, recipient_balance,
                    giver_id, timestamp, guild_id, giver_id, recipient_id,
                ),
            )
            result = GiftResult(True, amount, giver_balance, recipient_balance)
        self._log(
            "gift", guild_id, giver_id, -amount, result.giver_balance, timestamp,
            counterparty_id=recipient_id,
            counterparty_balance_after=result.recipient_balance,
        )
        return result

    async def rob(
        self,
        guild_id: int,
        robber_id: int,
        target_id: int,
        *,
        success_roll: float,
        steal_percent: int,
        fine_percent: int,
        now: Optional[int] = None,
    ) -> RobResult:
        """Resolve a robbery atomically while locking both accounts in ID order."""
        timestamp = self._now(now)
        connection_context = await self._connection()
        async with connection_context as connection:
            await self._ensure_accounts(connection, guild_id, robber_id)
            rows = await self._fetchall(
                connection,
                """SELECT * FROM economy_accounts
                   WHERE guild_id = %s AND user_id IN (%s, %s)
                   ORDER BY user_id FOR UPDATE""",
                (guild_id, robber_id, target_id),
            )
            accounts = {int(row["user_id"]): row for row in rows}
            robber = accounts[robber_id]
            robber_balance = int(robber["balance"])

            last_rob = robber["last_rob"]
            if last_rob is not None:
                retry_after = ROB_COOLDOWN_SECONDS - (timestamp - int(last_rob))
                if retry_after > 0:
                    await self._touch(connection, guild_id, robber_id, timestamp)
                    return RobResult("cooldown", 0, robber_balance, retry_after=retry_after)

            target = accounts.get(target_id)
            if target is None or target["last_activity"] is None:
                await self._touch(connection, guild_id, robber_id, timestamp)
                return RobResult("inactive", 0, robber_balance)
            if timestamp - int(target["last_activity"]) > ROB_ACTIVITY_WINDOW_SECONDS:
                await self._touch(connection, guild_id, robber_id, timestamp)
                return RobResult("inactive", 0, robber_balance)

            target_balance = int(target["balance"])
            if target_balance <= 0:
                await self._touch(connection, guild_id, robber_id, timestamp)
                return RobResult("broke", 0, robber_balance, target_balance)

            succeeded = success_roll < rob_success_chance(int(target["security_level"]))
            if succeeded:
                amount = min(
                    max(1, target_balance * max(1, steal_percent) // 100),
                    target_balance,
                )
                robber_balance += amount
                target_balance -= amount
                status: Literal["success", "caught"] = "success"
            else:
                amount = robber_balance * max(1, fine_percent) // 100
                amount = min(max(1, amount), robber_balance) if robber_balance else 0
                robber_balance -= amount
                target_balance += amount
                status = "caught"

            await connection.execute(
                """UPDATE economy_accounts
                   SET balance = CASE WHEN user_id = %s THEN %s ELSE %s END,
                       last_activity = CASE WHEN user_id = %s THEN %s ELSE last_activity END,
                       last_rob = CASE WHEN user_id = %s THEN %s ELSE last_rob END
                   WHERE guild_id = %s AND user_id IN (%s, %s)""",
                (
                    robber_id, robber_balance, target_balance,
                    robber_id, timestamp, robber_id, timestamp,
                    guild_id, robber_id, target_id,
                ),
            )
            result = RobResult(status, amount, robber_balance, target_balance)
        robber_delta = amount if result.status == "success" else -amount
        self._log(
            "rob", guild_id, robber_id, robber_delta, result.robber_balance, timestamp,
            counterparty_id=target_id,
            counterparty_balance_after=result.target_balance,
            details={"status": result.status},
        )
        return result

    @staticmethod
    async def _touch(connection: Any, guild_id: int, user_id: int, timestamp: int) -> None:
        await connection.execute(
            """UPDATE economy_accounts SET last_activity = %s
               WHERE guild_id = %s AND user_id = %s""",
            (timestamp, guild_id, user_id),
        )
