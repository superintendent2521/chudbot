"""Asynchronous, transaction-safe PostgreSQL storage for the guild economy."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Optional

from economy_logging import EconomyLogRecord, EconomyLogWriter


STARTING_BALANCE = 250
WORK_COOLDOWN_SECONDS = 3 * 60
ROB_COOLDOWN_SECONDS = 10 * 60
ROB_ACTIVITY_WINDOW_SECONDS = 15 * 60
LOAN_TERM_SECONDS = 60 * 60
MAX_LOAN_AMOUNT = 5_000
MAX_MARKET_QUANTITY = 1_000_000
MAX_MARKET_PRICE = 1_000_000_000
MAX_OPEN_BUY_ORDERS = 20
BUY_ORDER_TTL_SECONDS = 7 * 24 * 60 * 60
FISH_COOLDOWN_SECONDS = 5 * 60
MEMORY_COOLDOWN_SECONDS = 5 * 60
BOUNTY_COOLDOWN_SECONDS = 10 * 60
DUMPSTER_COOLDOWN_SECONDS = 5 * 60
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
    queued_logs: int
    dropped_logs: int
    escrowed_coins: int


@dataclass(frozen=True)
class InventoryEntry:
    item_key: str
    quantity: int


@dataclass(frozen=True)
class InventoryAwardResult:
    items: tuple[InventoryEntry, ...]


@dataclass(frozen=True)
class InventoryTransferResult:
    status: Literal["transferred", "invalid", "insufficient"]
    quantity: int
    remaining: int


@dataclass(frozen=True)
class InventoryConsumeResult:
    status: Literal["consumed", "invalid", "insufficient"]
    quantity: int
    remaining: int


@dataclass(frozen=True)
class EquipmentAvailability:
    inventory_quantity: int
    uses_remaining: int


@dataclass(frozen=True)
class EquipmentUseResult:
    status: Literal["used", "invalid", "insufficient"]
    uses_remaining: int
    activated_new: bool
    total_uses: int


@dataclass(frozen=True)
class InventorySaleResult:
    status: Literal["sold", "invalid", "insufficient"]
    quantity: int
    payout: int
    remaining: int
    balance: int


@dataclass(frozen=True)
class BuyOrderResult:
    status: Literal["created", "insufficient", "invalid", "limit"]
    order_id: Optional[int]
    quantity: int
    price_each: int
    balance: int


@dataclass(frozen=True)
class BuyOrderEntry:
    order_id: int
    buyer_id: int
    item_key: str
    quantity_remaining: int
    price_each: int
    expires_at: int


@dataclass(frozen=True)
class MarketSaleEntry:
    order_id: int
    buyer_id: int
    seller_id: int
    item_key: str
    quantity: int
    price_each: int
    sold_at: int


@dataclass(frozen=True)
class FillBuyOrderResult:
    status: Literal["filled", "unavailable", "invalid", "insufficient"]
    order_id: int
    buyer_id: Optional[int]
    item_key: Optional[str]
    quantity: int
    payout: int
    seller_balance: int
    order_remaining: int


@dataclass(frozen=True)
class CancelBuyOrderResult:
    status: Literal["cancelled", "unavailable"]
    order_id: int
    refund: int
    balance: int


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
        log_flush_interval: float = 10.0,
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
            flush_interval=log_flush_interval,
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
                    last_dumpster BIGINT,
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
                "ALTER TABLE economy_accounts ADD COLUMN IF NOT EXISTS last_dumpster BIGINT"
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
                    balance_after BIGINT,
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
            await connection.execute(
                "ALTER TABLE economy_log ALTER COLUMN balance_after DROP NOT NULL"
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS economy_inventory (
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    item_key TEXT NOT NULL,
                    quantity BIGINT NOT NULL CHECK (quantity > 0),
                    first_acquired_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL,
                    PRIMARY KEY (guild_id, user_id, item_key)
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS economy_equipment_charges (
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    item_key TEXT NOT NULL,
                    uses_remaining BIGINT NOT NULL CHECK (uses_remaining > 0),
                    activated_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL,
                    PRIMARY KEY (guild_id, user_id, item_key)
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS economy_buy_orders (
                    id BIGSERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    buyer_id BIGINT NOT NULL,
                    item_key TEXT NOT NULL,
                    quantity_original BIGINT NOT NULL CHECK (quantity_original > 0),
                    quantity_remaining BIGINT NOT NULL CHECK (quantity_remaining >= 0),
                    price_each BIGINT NOT NULL CHECK (price_each > 0),
                    status TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'filled', 'cancelled')),
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL,
                    expires_at BIGINT
                )
                """
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS economy_inventory_settings (
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    is_private BOOLEAN NOT NULL DEFAULT FALSE,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            await connection.execute(
                """CREATE INDEX IF NOT EXISTS economy_buy_orders_market_idx
                   ON economy_buy_orders (guild_id, status, item_key, price_each DESC)"""
            )
            await connection.execute(
                "ALTER TABLE economy_buy_orders ADD COLUMN IF NOT EXISTS expires_at BIGINT"
            )
            await connection.execute(
                """UPDATE economy_buy_orders
                   SET expires_at = created_at + %s WHERE expires_at IS NULL""",
                (BUY_ORDER_TTL_SECONDS,),
            )
            await connection.execute(
                """ALTER TABLE economy_buy_orders
                   ALTER COLUMN expires_at SET NOT NULL"""
            )
            await connection.execute(
                """CREATE INDEX IF NOT EXISTS economy_buy_orders_expiry_idx
                   ON economy_buy_orders (guild_id, status, expires_at)"""
            )
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS economy_market_sales (
                    id BIGSERIAL PRIMARY KEY,
                    order_id BIGINT NOT NULL,
                    guild_id BIGINT NOT NULL,
                    buyer_id BIGINT NOT NULL,
                    seller_id BIGINT NOT NULL,
                    item_key TEXT NOT NULL,
                    quantity BIGINT NOT NULL CHECK (quantity > 0),
                    price_each BIGINT NOT NULL CHECK (price_each > 0),
                    sold_at BIGINT NOT NULL
                )
                """
            )
            await connection.execute(
                """CREATE INDEX IF NOT EXISTS economy_market_sales_history_idx
                   ON economy_market_sales (guild_id, item_key, sold_at DESC)"""
            )

    def _log(
        self,
        event_type: str,
        guild_id: int,
        user_id: int,
        amount: int,
        balance_after: Optional[int],
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
                          COALESCE(MAX(balance), 0) AS highest_balance,
                          COALESCE((
                              SELECT SUM(quantity_remaining * price_each)
                              FROM economy_buy_orders WHERE status = 'open'
                          ), 0) AS escrowed_coins
                   FROM economy_accounts""",
                (),
            )
            return EconomyStatistics(
                guilds=int(row["guilds"]),
                accounts=int(row["accounts"]),
                total_balance=int(row["total_balance"]),
                average_balance=int(row["average_balance"]),
                highest_balance=int(row["highest_balance"]),
                queued_logs=self._log_writer.queued,
                dropped_logs=self._log_writer.dropped,
                escrowed_coins=int(row["escrowed_coins"]),
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

    async def inventory(self, guild_id: int, user_id: int) -> tuple[InventoryEntry, ...]:
        """Return a user's persistent inventory for this guild."""
        connection_context = await self._connection()
        async with connection_context as connection:
            rows = await self._fetchall(
                connection,
                """SELECT item_key, quantity FROM economy_inventory
                   WHERE guild_id = %s AND user_id = %s
                   ORDER BY quantity DESC, item_key ASC""",
                (guild_id, user_id),
            )
        return tuple(
            InventoryEntry(str(row["item_key"]), int(row["quantity"])) for row in rows
        )

    async def equipment_uses(
        self, guild_id: int, user_id: int
    ) -> tuple[InventoryEntry, ...]:
        """Return activated equipment and its remaining dumpster uses."""
        connection_context = await self._connection()
        async with connection_context as connection:
            rows = await self._fetchall(
                connection,
                """SELECT item_key, uses_remaining AS quantity
                   FROM economy_equipment_charges
                   WHERE guild_id = %s AND user_id = %s
                   ORDER BY uses_remaining DESC, item_key ASC""",
                (guild_id, user_id),
            )
        return tuple(
            InventoryEntry(str(row["item_key"]), int(row["quantity"])) for row in rows
        )

    async def equipment_availability(
        self, guild_id: int, user_id: int, item_key: str
    ) -> EquipmentAvailability:
        """Check stored items and active uses in one database round trip."""
        connection_context = await self._connection()
        async with connection_context as connection:
            row = await self._fetchone(
                connection,
                """SELECT
                       COALESCE((SELECT quantity FROM economy_inventory
                         WHERE guild_id = %s AND user_id = %s AND item_key = %s), 0)
                           AS inventory_quantity,
                       COALESCE((SELECT uses_remaining FROM economy_equipment_charges
                         WHERE guild_id = %s AND user_id = %s AND item_key = %s), 0)
                           AS uses_remaining""",
                (guild_id, user_id, item_key, guild_id, user_id, item_key),
            )
        return EquipmentAvailability(
            int(row["inventory_quantity"]), int(row["uses_remaining"])
        )

    async def inventory_is_private(self, guild_id: int, user_id: int) -> bool:
        connection_context = await self._connection()
        async with connection_context as connection:
            row = await self._fetchone(
                connection,
                """SELECT is_private FROM economy_inventory_settings
                   WHERE guild_id = %s AND user_id = %s""",
                (guild_id, user_id),
            )
        return False if row is None else bool(row["is_private"])

    async def set_inventory_private(
        self, guild_id: int, user_id: int, is_private: bool
    ) -> bool:
        connection_context = await self._connection()
        async with connection_context as connection:
            await connection.execute(
                """INSERT INTO economy_inventory_settings (guild_id, user_id, is_private)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (guild_id, user_id) DO UPDATE
                   SET is_private = EXCLUDED.is_private""",
                (guild_id, user_id, bool(is_private)),
            )
        return bool(is_private)

    async def add_inventory_items(
        self,
        guild_id: int,
        user_id: int,
        items: Mapping[str, int],
        *,
        source: str,
        now: Optional[int] = None,
    ) -> InventoryAwardResult:
        """Atomically add item quantities, then queue a best-effort audit record."""
        timestamp = self._now(now)
        awarded = {
            str(item_key): int(quantity)
            for item_key, quantity in items.items()
            if item_key and int(quantity) > 0
        }
        if not awarded:
            raise ValueError("At least one positive inventory quantity is required")

        placeholders = ", ".join(["(%s, %s, %s, %s, %s, %s)"] * len(awarded))
        parameters: list[Any] = []
        for item_key, quantity in awarded.items():
            parameters.extend(
                (guild_id, user_id, item_key, quantity, timestamp, timestamp)
            )

        connection_context = await self._connection()
        async with connection_context as connection:
            await self._ensure_accounts(connection, guild_id, user_id)
            rows = await self._fetchall(
                connection,
                f"""INSERT INTO economy_inventory
                    (guild_id, user_id, item_key, quantity, first_acquired_at, updated_at)
                    VALUES {placeholders}
                    ON CONFLICT (guild_id, user_id, item_key) DO UPDATE
                    SET quantity = economy_inventory.quantity + EXCLUDED.quantity,
                        updated_at = EXCLUDED.updated_at
                    RETURNING item_key, quantity""",
                tuple(parameters),
            )
            result = InventoryAwardResult(
                tuple(
                    InventoryEntry(str(row["item_key"]), int(row["quantity"]))
                    for row in rows
                )
            )
        self._log(
            "inventory_award",
            guild_id,
            user_id,
            sum(awarded.values()),
            None,
            timestamp,
            details={"source": source, "items": awarded},
        )
        return result

    async def transfer_inventory_item(
        self,
        guild_id: int,
        sender_id: int,
        recipient_id: int,
        item_key: str,
        quantity: int,
        *,
        now: Optional[int] = None,
    ) -> InventoryTransferResult:
        """Atomically transfer an inventory item between two users."""
        timestamp = self._now(now)
        quantity = int(quantity)
        if sender_id == recipient_id or quantity <= 0 or quantity > MAX_MARKET_QUANTITY:
            return InventoryTransferResult("invalid", quantity, 0)
        connection_context = await self._connection()
        async with connection_context as connection:
            rows = await self._fetchall(
                connection,
                """SELECT user_id, quantity FROM economy_inventory
                   WHERE guild_id = %s AND user_id IN (%s, %s) AND item_key = %s
                   ORDER BY user_id FOR UPDATE""",
                (guild_id, sender_id, recipient_id, item_key),
            )
            quantities = {int(row["user_id"]): int(row["quantity"]) for row in rows}
            available = quantities.get(sender_id, 0)
            if available < quantity:
                return InventoryTransferResult("insufficient", quantity, available)
            remaining = available - quantity
            if remaining:
                await connection.execute(
                    """UPDATE economy_inventory SET quantity = %s, updated_at = %s
                       WHERE guild_id = %s AND user_id = %s AND item_key = %s""",
                    (remaining, timestamp, guild_id, sender_id, item_key),
                )
            else:
                await connection.execute(
                    """DELETE FROM economy_inventory
                       WHERE guild_id = %s AND user_id = %s AND item_key = %s""",
                    (guild_id, sender_id, item_key),
                )
            await connection.execute(
                """INSERT INTO economy_inventory
                       (guild_id, user_id, item_key, quantity, first_acquired_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (guild_id, user_id, item_key) DO UPDATE
                   SET quantity = economy_inventory.quantity + EXCLUDED.quantity,
                       updated_at = EXCLUDED.updated_at""",
                (guild_id, recipient_id, item_key, quantity, timestamp, timestamp),
            )
        self._log(
            "inventory_transfer", guild_id, sender_id, quantity, None, timestamp,
            counterparty_id=recipient_id,
            details={"item_key": item_key},
        )
        return InventoryTransferResult("transferred", quantity, remaining)

    async def consume_inventory_item(
        self,
        guild_id: int,
        user_id: int,
        item_key: str,
        quantity: int = 1,
        *,
        source: str,
        now: Optional[int] = None,
    ) -> InventoryConsumeResult:
        """Atomically remove consumable inventory for an activity."""
        timestamp = self._now(now)
        quantity = int(quantity)
        if quantity <= 0 or quantity > MAX_MARKET_QUANTITY:
            return InventoryConsumeResult("invalid", quantity, 0)
        connection_context = await self._connection()
        async with connection_context as connection:
            row = await self._fetchone(
                connection,
                """SELECT quantity FROM economy_inventory
                   WHERE guild_id = %s AND user_id = %s AND item_key = %s FOR UPDATE""",
                (guild_id, user_id, item_key),
            )
            available = 0 if row is None else int(row["quantity"])
            if available < quantity:
                return InventoryConsumeResult("insufficient", quantity, available)
            remaining = available - quantity
            if remaining:
                await connection.execute(
                    """UPDATE economy_inventory SET quantity = %s, updated_at = %s
                       WHERE guild_id = %s AND user_id = %s AND item_key = %s""",
                    (remaining, timestamp, guild_id, user_id, item_key),
                )
            else:
                await connection.execute(
                    """DELETE FROM economy_inventory
                       WHERE guild_id = %s AND user_id = %s AND item_key = %s""",
                    (guild_id, user_id, item_key),
                )
        self._log(
            "inventory_consumed", guild_id, user_id, quantity, None, timestamp,
            details={"item_key": item_key, "source": source},
        )
        return InventoryConsumeResult("consumed", quantity, remaining)

    async def use_inventory_equipment(
        self,
        guild_id: int,
        user_id: int,
        item_key: str,
        total_uses: int,
        *,
        source: str,
        now: Optional[int] = None,
    ) -> EquipmentUseResult:
        """Spend one active use, activating one inventory item when necessary."""
        timestamp = self._now(now)
        total_uses = int(total_uses)
        if not item_key or total_uses < 1 or total_uses > 100:
            return EquipmentUseResult("invalid", 0, False, total_uses)

        activated_new = False
        connection_context = await self._connection()
        async with connection_context as connection:
            await self._ensure_accounts(connection, guild_id, user_id)
            # Serialize first activation when no charge row exists yet.
            await self._fetchone(
                connection,
                """SELECT user_id FROM economy_accounts
                   WHERE guild_id = %s AND user_id = %s FOR UPDATE""",
                (guild_id, user_id),
            )
            charge = await self._fetchone(
                connection,
                """SELECT uses_remaining FROM economy_equipment_charges
                   WHERE guild_id = %s AND user_id = %s AND item_key = %s FOR UPDATE""",
                (guild_id, user_id, item_key),
            )
            if charge is not None:
                uses_remaining = int(charge["uses_remaining"]) - 1
                if uses_remaining > 0:
                    await connection.execute(
                        """UPDATE economy_equipment_charges
                           SET uses_remaining = %s, updated_at = %s
                           WHERE guild_id = %s AND user_id = %s AND item_key = %s""",
                        (uses_remaining, timestamp, guild_id, user_id, item_key),
                    )
                else:
                    await connection.execute(
                        """DELETE FROM economy_equipment_charges
                           WHERE guild_id = %s AND user_id = %s AND item_key = %s""",
                        (guild_id, user_id, item_key),
                    )
            else:
                inventory = await self._fetchone(
                    connection,
                    """SELECT quantity FROM economy_inventory
                       WHERE guild_id = %s AND user_id = %s AND item_key = %s FOR UPDATE""",
                    (guild_id, user_id, item_key),
                )
                available = 0 if inventory is None else int(inventory["quantity"])
                if available < 1:
                    return EquipmentUseResult("insufficient", 0, False, total_uses)
                activated_new = True
                inventory_remaining = available - 1
                if inventory_remaining > 0:
                    await connection.execute(
                        """UPDATE economy_inventory SET quantity = %s, updated_at = %s
                           WHERE guild_id = %s AND user_id = %s AND item_key = %s""",
                        (inventory_remaining, timestamp, guild_id, user_id, item_key),
                    )
                else:
                    await connection.execute(
                        """DELETE FROM economy_inventory
                           WHERE guild_id = %s AND user_id = %s AND item_key = %s""",
                        (guild_id, user_id, item_key),
                    )
                uses_remaining = total_uses - 1
                if uses_remaining > 0:
                    await connection.execute(
                        """INSERT INTO economy_equipment_charges
                               (guild_id, user_id, item_key, uses_remaining,
                                activated_at, updated_at)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        (
                            guild_id, user_id, item_key, uses_remaining,
                            timestamp, timestamp,
                        ),
                    )

        self._log(
            "inventory_equipment_used", guild_id, user_id, 1, None, timestamp,
            details={
                "item_key": item_key,
                "source": source,
                "uses_remaining": uses_remaining,
                "activated_new": activated_new,
                "total_uses": total_uses if activated_new else None,
            },
        )
        return EquipmentUseResult(
            "used", uses_remaining, activated_new,
            total_uses if activated_new else uses_remaining + 1,
        )

    async def sell_inventory_item(
        self,
        guild_id: int,
        user_id: int,
        item_key: str,
        quantity: int,
        unit_price: int,
        *,
        now: Optional[int] = None,
    ) -> InventorySaleResult:
        """Atomically sell inventory to the automated market at a fixed price."""
        timestamp = self._now(now)
        quantity = int(quantity)
        unit_price = int(unit_price)
        if (
            quantity <= 0
            or quantity > MAX_MARKET_QUANTITY
            or unit_price <= 0
            or unit_price > MAX_MARKET_PRICE
        ):
            return InventorySaleResult("invalid", quantity, 0, 0, 0)
        payout = quantity * unit_price
        connection_context = await self._connection()
        async with connection_context as connection:
            await self._ensure_accounts(connection, guild_id, user_id)
            row = await self._fetchone(
                connection,
                """SELECT quantity FROM economy_inventory
                   WHERE guild_id = %s AND user_id = %s AND item_key = %s
                   FOR UPDATE""",
                (guild_id, user_id, item_key),
            )
            available = 0 if row is None else int(row["quantity"])
            if available < quantity:
                balance_row = await self._fetchone(
                    connection,
                    """SELECT balance FROM economy_accounts
                       WHERE guild_id = %s AND user_id = %s""",
                    (guild_id, user_id),
                )
                return InventorySaleResult(
                    "insufficient", quantity, 0, available, int(balance_row["balance"])
                )
            remaining = available - quantity
            if remaining:
                await connection.execute(
                    """UPDATE economy_inventory SET quantity = %s, updated_at = %s
                       WHERE guild_id = %s AND user_id = %s AND item_key = %s""",
                    (remaining, timestamp, guild_id, user_id, item_key),
                )
            else:
                await connection.execute(
                    """DELETE FROM economy_inventory
                       WHERE guild_id = %s AND user_id = %s AND item_key = %s""",
                    (guild_id, user_id, item_key),
                )
            balance_row = await self._fetchone(
                connection,
                """UPDATE economy_accounts
                   SET balance = balance + %s, last_activity = %s
                   WHERE guild_id = %s AND user_id = %s RETURNING balance""",
                (payout, timestamp, guild_id, user_id),
            )
            result = InventorySaleResult(
                "sold", quantity, payout, remaining, int(balance_row["balance"])
            )
        self._log(
            "inventory_system_sale", guild_id, user_id, payout, result.balance, timestamp,
            details={"item_key": item_key, "quantity": quantity, "unit_price": unit_price},
        )
        return result

    async def create_buy_order(
        self,
        guild_id: int,
        buyer_id: int,
        item_key: str,
        quantity: int,
        price_each: int,
        *,
        now: Optional[int] = None,
    ) -> BuyOrderResult:
        """Create a fully escrowed player buy order."""
        timestamp = self._now(now)
        await self.expire_buy_orders(guild_id, now=timestamp)
        quantity = int(quantity)
        price_each = int(price_each)
        connection_context = await self._connection()
        async with connection_context as connection:
            await self._ensure_accounts(connection, guild_id, buyer_id)
            balance_row = await self._fetchone(
                connection,
                """SELECT balance FROM economy_accounts
                   WHERE guild_id = %s AND user_id = %s FOR UPDATE""",
                (guild_id, buyer_id),
            )
            balance = int(balance_row["balance"])
            if (
                quantity <= 0
                or quantity > MAX_MARKET_QUANTITY
                or price_each <= 0
                or price_each > MAX_MARKET_PRICE
            ):
                return BuyOrderResult("invalid", None, quantity, price_each, balance)
            open_count_row = await self._fetchone(
                connection,
                """SELECT COUNT(*) AS open_count FROM economy_buy_orders
                   WHERE guild_id = %s AND buyer_id = %s AND status = 'open'""",
                (guild_id, buyer_id),
            )
            if int(open_count_row["open_count"]) >= MAX_OPEN_BUY_ORDERS:
                return BuyOrderResult("limit", None, quantity, price_each, balance)
            escrow = quantity * price_each
            if balance < escrow:
                return BuyOrderResult("insufficient", None, quantity, price_each, balance)
            balance_row = await self._fetchone(
                connection,
                """UPDATE economy_accounts SET balance = balance - %s, last_activity = %s
                   WHERE guild_id = %s AND user_id = %s RETURNING balance""",
                (escrow, timestamp, guild_id, buyer_id),
            )
            order_row = await self._fetchone(
                connection,
                """INSERT INTO economy_buy_orders
                        (guild_id, buyer_id, item_key, quantity_original,
                        quantity_remaining, price_each, created_at, updated_at, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    guild_id, buyer_id, item_key, quantity, quantity,
                    price_each, timestamp, timestamp, timestamp + BUY_ORDER_TTL_SECONDS,
                ),
            )
            result = BuyOrderResult(
                "created", int(order_row["id"]), quantity,
                price_each, int(balance_row["balance"]),
            )
        self._log(
            "buy_order_created", guild_id, buyer_id, -escrow, result.balance, timestamp,
            details={
                "order_id": result.order_id,
                "item_key": item_key,
                "quantity": quantity,
                "price_each": price_each,
            },
        )
        return result

    async def buy_orders(
        self,
        guild_id: int,
        *,
        item_key: Optional[str] = None,
        buyer_id: Optional[int] = None,
        limit: int = 15,
        now: Optional[int] = None,
    ) -> tuple[BuyOrderEntry, ...]:
        """List the highest-paying open buy orders in a guild."""
        timestamp = self._now(now)
        await self.expire_buy_orders(guild_id, now=timestamp)
        limit = max(1, min(50, int(limit)))
        filters = ["guild_id = %s", "status = 'open'", "expires_at > %s"]
        parameters: list[Any] = [guild_id, timestamp]
        if item_key is not None:
            filters.append("item_key = %s")
            parameters.append(item_key)
        if buyer_id is not None:
            filters.append("buyer_id = %s")
            parameters.append(buyer_id)
        parameters.append(limit)
        connection_context = await self._connection()
        async with connection_context as connection:
            rows = await self._fetchall(
                connection,
                f"""SELECT id, buyer_id, item_key, quantity_remaining, price_each,
                           expires_at
                    FROM economy_buy_orders
                    WHERE {' AND '.join(filters)}
                    ORDER BY price_each DESC, created_at ASC LIMIT %s""",
                tuple(parameters),
            )
        return tuple(
            BuyOrderEntry(
                int(row["id"]), int(row["buyer_id"]), str(row["item_key"]),
                int(row["quantity_remaining"]), int(row["price_each"]),
                int(row["expires_at"]),
            )
            for row in rows
        )

    async def expire_buy_orders(
        self, guild_id: int, *, now: Optional[int] = None
    ) -> int:
        """Refund and close expired orders; safe to call from market read paths."""
        timestamp = self._now(now)
        connection_context = await self._connection()
        refunds: dict[int, int] = {}
        order_ids_by_buyer: dict[int, list[int]] = {}
        async with connection_context as connection:
            rows = await self._fetchall(
                connection,
                """SELECT id, buyer_id, quantity_remaining, price_each
                   FROM economy_buy_orders
                   WHERE guild_id = %s AND status = 'open' AND expires_at <= %s
                   ORDER BY buyer_id, id FOR UPDATE""",
                (guild_id, timestamp),
            )
            if not rows:
                return 0
            order_ids = [int(row["id"]) for row in rows]
            for row in rows:
                buyer_id = int(row["buyer_id"])
                refunds[buyer_id] = refunds.get(buyer_id, 0) + (
                    int(row["quantity_remaining"]) * int(row["price_each"])
                )
                order_ids_by_buyer.setdefault(buyer_id, []).append(int(row["id"]))
            placeholders = ", ".join(["%s"] * len(order_ids))
            await connection.execute(
                f"""UPDATE economy_buy_orders
                    SET quantity_remaining = 0, status = 'cancelled', updated_at = %s
                    WHERE id IN ({placeholders})""",
                (timestamp, *order_ids),
            )
            buyer_ids = sorted(refunds)
            await self._ensure_accounts(connection, guild_id, *buyer_ids)
            await self._fetchall(
                connection,
                f"""SELECT user_id FROM economy_accounts
                    WHERE guild_id = %s AND user_id IN ({', '.join(['%s'] * len(buyer_ids))})
                    ORDER BY user_id FOR UPDATE""",
                (guild_id, *buyer_ids),
            )
            for buyer_id in buyer_ids:
                await connection.execute(
                    """UPDATE economy_accounts SET balance = balance + %s
                       WHERE guild_id = %s AND user_id = %s""",
                    (refunds[buyer_id], guild_id, buyer_id),
                )
        for buyer_id, refund in refunds.items():
            self._log(
                "buy_order_expired", guild_id, buyer_id, refund, None, timestamp,
                details={"order_ids": order_ids_by_buyer[buyer_id]},
            )
        return len(rows)

    async def fill_buy_order(
        self,
        guild_id: int,
        seller_id: int,
        order_id: int,
        quantity: int,
        *,
        now: Optional[int] = None,
    ) -> FillBuyOrderResult:
        """Sell inventory into an escrowed buy order atomically."""
        timestamp = self._now(now)
        await self.expire_buy_orders(guild_id, now=timestamp)
        order_id = int(order_id)
        quantity = int(quantity)
        if quantity <= 0 or quantity > MAX_MARKET_QUANTITY:
            return FillBuyOrderResult(
                "invalid", order_id, None, None, quantity, 0, 0, 0
            )
        connection_context = await self._connection()
        async with connection_context as connection:
            order = await self._fetchone(
                connection,
                """SELECT buyer_id, item_key, quantity_remaining, price_each
                   FROM economy_buy_orders
                   WHERE guild_id = %s AND id = %s AND status = 'open' FOR UPDATE""",
                (guild_id, order_id),
            )
            if order is None:
                return FillBuyOrderResult(
                    "unavailable", order_id, None, None, quantity, 0, 0, 0
                )
            buyer_id = int(order["buyer_id"])
            item_key = str(order["item_key"])
            order_remaining = int(order["quantity_remaining"])
            if buyer_id == seller_id or quantity > order_remaining:
                return FillBuyOrderResult(
                    "invalid", order_id, buyer_id, item_key,
                    quantity, 0, 0, order_remaining,
                )
            inventory_rows = await self._fetchall(
                connection,
                """SELECT user_id, quantity FROM economy_inventory
                   WHERE guild_id = %s AND user_id IN (%s, %s) AND item_key = %s
                   ORDER BY user_id FOR UPDATE""",
                (guild_id, seller_id, buyer_id, item_key),
            )
            inventory_quantities = {
                int(row["user_id"]): int(row["quantity"]) for row in inventory_rows
            }
            available = inventory_quantities.get(seller_id, 0)
            if available < quantity:
                return FillBuyOrderResult(
                    "insufficient", order_id, buyer_id, item_key,
                    quantity, 0, 0, order_remaining,
                )
            seller_remaining = available - quantity
            if seller_remaining:
                await connection.execute(
                    """UPDATE economy_inventory SET quantity = %s, updated_at = %s
                       WHERE guild_id = %s AND user_id = %s AND item_key = %s""",
                    (seller_remaining, timestamp, guild_id, seller_id, item_key),
                )
            else:
                await connection.execute(
                    """DELETE FROM economy_inventory
                       WHERE guild_id = %s AND user_id = %s AND item_key = %s""",
                    (guild_id, seller_id, item_key),
                )
            await connection.execute(
                """INSERT INTO economy_inventory
                       (guild_id, user_id, item_key, quantity, first_acquired_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (guild_id, user_id, item_key) DO UPDATE
                   SET quantity = economy_inventory.quantity + EXCLUDED.quantity,
                       updated_at = EXCLUDED.updated_at""",
                (guild_id, buyer_id, item_key, quantity, timestamp, timestamp),
            )
            order_remaining -= quantity
            await connection.execute(
                """UPDATE economy_buy_orders
                   SET quantity_remaining = %s,
                       status = CASE WHEN %s = 0 THEN 'filled' ELSE 'open' END,
                       updated_at = %s
                   WHERE id = %s""",
                (order_remaining, order_remaining, timestamp, order_id),
            )
            payout = quantity * int(order["price_each"])
            await self._ensure_accounts(connection, guild_id, seller_id)
            balance_row = await self._fetchone(
                connection,
                """UPDATE economy_accounts SET balance = balance + %s, last_activity = %s
                   WHERE guild_id = %s AND user_id = %s RETURNING balance""",
                (payout, timestamp, guild_id, seller_id),
            )
            await connection.execute(
                """INSERT INTO economy_market_sales
                       (order_id, guild_id, buyer_id, seller_id, item_key,
                        quantity, price_each, sold_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    order_id, guild_id, buyer_id, seller_id, item_key,
                    quantity, int(order["price_each"]), timestamp,
                ),
            )
            result = FillBuyOrderResult(
                "filled", order_id, buyer_id, item_key, quantity, payout,
                int(balance_row["balance"]), order_remaining,
            )
        self._log(
            "buy_order_filled", guild_id, seller_id, result.payout,
            result.seller_balance, timestamp, counterparty_id=result.buyer_id,
            details={
                "order_id": order_id,
                "item_key": result.item_key,
                "quantity": quantity,
            },
        )
        return result

    async def cancel_buy_order(
        self,
        guild_id: int,
        buyer_id: int,
        order_id: int,
        *,
        now: Optional[int] = None,
    ) -> CancelBuyOrderResult:
        """Cancel a user's open order and refund all unfilled escrow."""
        timestamp = self._now(now)
        await self.expire_buy_orders(guild_id, now=timestamp)
        order_id = int(order_id)
        connection_context = await self._connection()
        async with connection_context as connection:
            order = await self._fetchone(
                connection,
                """SELECT quantity_remaining, price_each FROM economy_buy_orders
                   WHERE guild_id = %s AND id = %s AND buyer_id = %s
                     AND status = 'open' FOR UPDATE""",
                (guild_id, order_id, buyer_id),
            )
            await self._ensure_accounts(connection, guild_id, buyer_id)
            if order is None:
                balance_row = await self._fetchone(
                    connection,
                    """SELECT balance FROM economy_accounts
                       WHERE guild_id = %s AND user_id = %s""",
                    (guild_id, buyer_id),
                )
                return CancelBuyOrderResult(
                    "unavailable", order_id, 0, int(balance_row["balance"])
                )
            refund = int(order["quantity_remaining"]) * int(order["price_each"])
            await connection.execute(
                """UPDATE economy_buy_orders
                   SET quantity_remaining = 0, status = 'cancelled', updated_at = %s
                   WHERE id = %s""",
                (timestamp, order_id),
            )
            balance_row = await self._fetchone(
                connection,
                """UPDATE economy_accounts SET balance = balance + %s, last_activity = %s
                   WHERE guild_id = %s AND user_id = %s RETURNING balance""",
                (refund, timestamp, guild_id, buyer_id),
            )
            result = CancelBuyOrderResult(
                "cancelled", order_id, refund, int(balance_row["balance"])
            )
        self._log(
            "buy_order_cancelled", guild_id, buyer_id, result.refund,
            result.balance, timestamp, details={"order_id": order_id},
        )
        return result

    async def market_sales(
        self,
        guild_id: int,
        item_key: str,
        *,
        limit: int = 10,
    ) -> tuple[MarketSaleEntry, ...]:
        """Return recent completed player-market sales for an item."""
        limit = max(1, min(25, int(limit)))
        connection_context = await self._connection()
        async with connection_context as connection:
            rows = await self._fetchall(
                connection,
                """SELECT order_id, buyer_id, seller_id, item_key, quantity,
                          price_each, sold_at
                   FROM economy_market_sales
                   WHERE guild_id = %s AND item_key = %s
                   ORDER BY sold_at DESC, id DESC LIMIT %s""",
                (guild_id, item_key, limit),
            )
        return tuple(
            MarketSaleEntry(
                int(row["order_id"]), int(row["buyer_id"]), int(row["seller_id"]),
                str(row["item_key"]), int(row["quantity"]),
                int(row["price_each"]), int(row["sold_at"]),
            )
            for row in rows
        )

    async def start_activity(
        self,
        guild_id: int,
        user_id: int,
        activity: Literal["fish", "memory", "bounty", "dumpster"],
        *,
        now: Optional[int] = None,
    ) -> ActivityStartResult:
        """Atomically consume an activity cooldown before starting a game."""
        settings = {
            "fish": ("last_fish", FISH_COOLDOWN_SECONDS),
            "memory": ("last_memory", MEMORY_COOLDOWN_SECONDS),
            "bounty": ("last_bounty", BOUNTY_COOLDOWN_SECONDS),
            "dumpster": ("last_dumpster", DUMPSTER_COOLDOWN_SECONDS),
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
