"""Asynchronous, transaction-safe PostgreSQL storage for the guild economy."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Literal, Optional


STARTING_BALANCE = 250
WORK_COOLDOWN_SECONDS = 3 * 60
ROB_COOLDOWN_SECONDS = 10 * 60
ROB_ACTIVITY_WINDOW_SECONDS = 15 * 60
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


class PostgresEconomyStore:
    """Async PostgreSQL economy store backed by a reusable connection pool."""

    def __init__(
        self,
        database_url: str,
        *,
        min_pool_size: int = 5,
        max_pool_size: int = 10,
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

    async def close(self) -> None:
        if self._opened:
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
                """CREATE INDEX IF NOT EXISTS economy_leaderboard_idx
                   ON economy_accounts (guild_id, balance DESC, user_id ASC)"""
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
                       RETURNING balance, TRUE AS worked, 0::BIGINT AS retry_after
                   ), worked AS (
                       UPDATE economy_accounts
                       SET balance = balance + %s, last_activity = %s, last_work = %s
                       WHERE guild_id = %s AND user_id = %s
                         AND NOT EXISTS (SELECT 1 FROM inserted)
                         AND (last_work IS NULL OR last_work <= %s - %s)
                       RETURNING balance, TRUE AS worked, 0::BIGINT AS retry_after
                   ), cooling_down AS (
                       UPDATE economy_accounts
                       SET last_activity = %s
                       WHERE guild_id = %s AND user_id = %s
                         AND NOT EXISTS (SELECT 1 FROM inserted)
                         AND NOT EXISTS (SELECT 1 FROM worked)
                       RETURNING balance, FALSE AS worked,
                           GREATEST(1, %s - (%s - last_work))::BIGINT AS retry_after
                   )
                   SELECT * FROM inserted
                   UNION ALL SELECT * FROM worked
                   UNION ALL SELECT * FROM cooling_down""",
                (
                    guild_id, user_id, STARTING_BALANCE, reward, timestamp, timestamp,
                    reward, timestamp, timestamp, guild_id, user_id,
                    timestamp, WORK_COOLDOWN_SECONDS,
                    timestamp, guild_id, user_id, WORK_COOLDOWN_SECONDS, timestamp,
                ),
            )
            if row is None:  # Rare first-use race: retry after the competing insert commits.
                return await self.work(guild_id, user_id, reward, now=timestamp)
            worked = bool(row["worked"])
            return WorkResult(reward if worked else 0, int(row["balance"]), int(row["retry_after"]))

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
            if row is None:
                return await self.settle_wager(
                    guild_id, user_id, amount, profit=profit, now=timestamp
                )
            accepted = bool(row["accepted"])
            return WagerResult(accepted, amount, profit if accepted else 0, int(row["balance"]))

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
            if row is None:
                return await self.upgrade_security(guild_id, user_id, now=timestamp)
            level = int(row["security_level"])
            return SecurityUpgradeResult(
                str(row["status"]),  # type: ignore[arg-type]
                level,
                int(row["cost"]),
                int(row["balance"]),
                security_protection_percent(level),
            )

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
            return GiftResult(True, amount, giver_balance, recipient_balance)

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
            return RobResult(status, amount, robber_balance, target_balance)

    @staticmethod
    async def _touch(connection: Any, guild_id: int, user_id: int, timestamp: int) -> None:
        await connection.execute(
            """UPDATE economy_accounts SET last_activity = %s
               WHERE guild_id = %s AND user_id = %s""",
            (timestamp, guild_id, user_id),
        )
