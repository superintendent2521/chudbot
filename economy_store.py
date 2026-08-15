"""Persistent, transaction-safe storage for the guild economy."""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal, Optional, Sequence


STARTING_BALANCE = 250
WORK_COOLDOWN_SECONDS = 3 * 60
ROB_COOLDOWN_SECONDS = 10 * 60
ROB_ACTIVITY_WINDOW_SECONDS = 15 * 60
DEFAULT_POSTGRES_URL = "postgresql://postgres@postgres/postgres"


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


class SQLiteEconomyStore:
    """Legacy SQLite store, retained for data migration and isolated tests."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS economy_accounts (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    balance INTEGER NOT NULL DEFAULT 250 CHECK (balance >= 0),
                    last_activity INTEGER,
                    last_work INTEGER,
                    last_rob INTEGER,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS economy_leaderboard_idx
                   ON economy_accounts (guild_id, balance DESC, user_id ASC)"""
            )

    @staticmethod
    def _now(now: Optional[int]) -> int:
        return int(time.time() if now is None else now)

    @staticmethod
    def _ensure_account(connection: sqlite3.Connection, guild_id: int, user_id: int) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO economy_accounts (guild_id, user_id) VALUES (?, ?)",
            (guild_id, user_id),
        )

    @staticmethod
    def _row(connection: sqlite3.Connection, guild_id: int, user_id: int) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM economy_accounts WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        if row is None:  # Account creation and reads occur in the same transaction.
            raise RuntimeError("Economy account was not created")
        return row

    def balance(self, guild_id: int, user_id: int, *, now: Optional[int] = None) -> int:
        """Return a balance and record that this user used an economy command."""
        timestamp = self._now(now)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_account(connection, guild_id, user_id)
            connection.execute(
                "UPDATE economy_accounts SET last_activity = ? WHERE guild_id = ? AND user_id = ?",
                (timestamp, guild_id, user_id),
            )
            return int(self._row(connection, guild_id, user_id)["balance"])

    def peek_balance(self, guild_id: int, user_id: int) -> Optional[int]:
        """Read a balance without marking the viewed user active."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT balance FROM economy_accounts WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            return None if row is None else int(row["balance"])

    def leaderboard(
        self,
        guild_id: int,
        user_id: int,
        *,
        limit: int = 10,
        now: Optional[int] = None,
    ) -> LeaderboardResult:
        """Return the guild's richest accounts and the requesting user's rank."""
        timestamp = self._now(now)
        limit = max(1, int(limit))
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_account(connection, guild_id, user_id)
            connection.execute(
                "UPDATE economy_accounts SET last_activity = ? WHERE guild_id = ? AND user_id = ?",
                (timestamp, guild_id, user_id),
            )
            user_balance = int(self._row(connection, guild_id, user_id)["balance"])
            rows = connection.execute(
                """SELECT user_id, balance
                   FROM economy_accounts
                   WHERE guild_id = ?
                   ORDER BY balance DESC, user_id ASC
                   LIMIT ?""",
                (guild_id, limit),
            ).fetchall()
            entries = tuple(
                LeaderboardEntry(rank, int(row["user_id"]), int(row["balance"]))
                for rank, row in enumerate(rows, start=1)
            )
            richer_accounts = connection.execute(
                """SELECT COUNT(*) FROM economy_accounts
                   WHERE guild_id = ? AND balance > ?""",
                (guild_id, user_balance),
            ).fetchone()[0]
            earlier_ties = connection.execute(
                """SELECT COUNT(*) FROM economy_accounts
                   WHERE guild_id = ? AND balance = ? AND user_id < ?""",
                (guild_id, user_balance, user_id),
            ).fetchone()[0]
            user_rank = int(richer_accounts) + int(earlier_ties) + 1
            return LeaderboardResult(entries, user_rank, user_balance)

    def work(
        self,
        guild_id: int,
        user_id: int,
        reward: int,
        *,
        now: Optional[int] = None,
    ) -> WorkResult:
        timestamp = self._now(now)
        reward = max(0, int(reward))
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_account(connection, guild_id, user_id)
            row = self._row(connection, guild_id, user_id)
            connection.execute(
                "UPDATE economy_accounts SET last_activity = ? WHERE guild_id = ? AND user_id = ?",
                (timestamp, guild_id, user_id),
            )
            last_work = row["last_work"]
            if last_work is not None:
                retry_after = WORK_COOLDOWN_SECONDS - (timestamp - int(last_work))
                if retry_after > 0:
                    return WorkResult(0, int(row["balance"]), retry_after)

            balance = int(row["balance"]) + reward
            connection.execute(
                """UPDATE economy_accounts
                   SET balance = ?, last_work = ?
                   WHERE guild_id = ? AND user_id = ?""",
                (balance, timestamp, guild_id, user_id),
            )
            return WorkResult(reward, balance)

    def gamble(
        self,
        guild_id: int,
        user_id: int,
        amount: int,
        won: bool,
        *,
        now: Optional[int] = None,
    ) -> GambleResult:
        result = self.settle_wager(
            guild_id,
            user_id,
            amount,
            profit=amount if won else -amount,
            now=now,
        )
        return GambleResult(result.accepted, won if result.accepted else False, result.amount, result.balance)

    def settle_wager(
        self,
        guild_id: int,
        user_id: int,
        amount: int,
        *,
        profit: int,
        now: Optional[int] = None,
    ) -> WagerResult:
        """Atomically apply a game result after verifying the player can cover its wager."""
        timestamp = self._now(now)
        amount = int(amount)
        profit = int(profit)
        if amount > 0 and profit < -amount:
            raise ValueError("A wager cannot lose more than its amount")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_account(connection, guild_id, user_id)
            row = self._row(connection, guild_id, user_id)
            balance = int(row["balance"])
            connection.execute(
                "UPDATE economy_accounts SET last_activity = ? WHERE guild_id = ? AND user_id = ?",
                (timestamp, guild_id, user_id),
            )
            if amount <= 0 or amount > balance:
                return WagerResult(False, amount, 0, balance)

            balance += profit
            connection.execute(
                "UPDATE economy_accounts SET balance = ? WHERE guild_id = ? AND user_id = ?",
                (balance, guild_id, user_id),
            )
            return WagerResult(True, amount, profit, balance)

    def rob(
        self,
        guild_id: int,
        robber_id: int,
        target_id: int,
        *,
        succeeded: bool,
        steal_percent: int,
        fine_percent: int,
        now: Optional[int] = None,
    ) -> RobResult:
        """Attempt a robbery, requiring recent target activity in this guild."""
        timestamp = self._now(now)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_account(connection, guild_id, robber_id)
            robber = self._row(connection, guild_id, robber_id)
            robber_balance = int(robber["balance"])
            connection.execute(
                "UPDATE economy_accounts SET last_activity = ? WHERE guild_id = ? AND user_id = ?",
                (timestamp, guild_id, robber_id),
            )

            last_rob = robber["last_rob"]
            if last_rob is not None:
                retry_after = ROB_COOLDOWN_SECONDS - (timestamp - int(last_rob))
                if retry_after > 0:
                    return RobResult("cooldown", 0, robber_balance, retry_after=retry_after)

            target = connection.execute(
                "SELECT * FROM economy_accounts WHERE guild_id = ? AND user_id = ?",
                (guild_id, target_id),
            ).fetchone()
            if target is None or target["last_activity"] is None:
                return RobResult("inactive", 0, robber_balance)
            if timestamp - int(target["last_activity"]) > ROB_ACTIVITY_WINDOW_SECONDS:
                return RobResult("inactive", 0, robber_balance)

            target_balance = int(target["balance"])
            if target_balance <= 0:
                return RobResult("broke", 0, robber_balance, target_balance)

            if succeeded:
                amount = max(1, target_balance * max(1, steal_percent) // 100)
                amount = min(amount, target_balance, 500)
                robber_balance += amount
                target_balance -= amount
                status: Literal["success", "caught"] = "success"
            else:
                amount = robber_balance * max(1, fine_percent) // 100
                amount = min(max(1, amount), robber_balance) if robber_balance else 0
                robber_balance -= amount
                target_balance += amount
                status = "caught"

            connection.execute(
                """UPDATE economy_accounts
                   SET balance = ?, last_rob = ?
                   WHERE guild_id = ? AND user_id = ?""",
                (robber_balance, timestamp, guild_id, robber_id),
            )
            connection.execute(
                "UPDATE economy_accounts SET balance = ? WHERE guild_id = ? AND user_id = ?",
                (target_balance, guild_id, target_id),
            )
            return RobResult(status, amount, robber_balance, target_balance)


class PostgresEconomyStore:
    """Pooled PostgreSQL economy store with per-account transactional locking."""

    def __init__(
        self,
        database_url: str,
        *,
        min_pool_size: int = 2,
        max_pool_size: int = 10,
    ) -> None:
        if not database_url:
            raise ValueError("A PostgreSQL database URL is required")
        try:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except ImportError as error:
            raise RuntimeError(
                'PostgreSQL support requires: pip install "psycopg[binary,pool]>=3.2,<4"'
            ) from error

        min_pool_size = max(1, int(min_pool_size))
        max_pool_size = max(min_pool_size, int(max_pool_size))
        self._pool = ConnectionPool(
            conninfo=database_url,
            min_size=min_pool_size,
            max_size=max_pool_size,
            timeout=10,
            kwargs={"row_factory": dict_row},
            open=False,
            name="economy",
        )
        self._pool.open(wait=True, timeout=30)
        self._initialize()

    def close(self) -> None:
        self._pool.close()

    def _initialize(self) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS economy_accounts (
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    balance BIGINT NOT NULL DEFAULT 250 CHECK (balance >= 0),
                    last_activity BIGINT,
                    last_work BIGINT,
                    last_rob BIGINT,
                    PRIMARY KEY (guild_id, user_id)
                )
                """
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS economy_leaderboard_idx
                   ON economy_accounts (guild_id, balance DESC, user_id ASC)"""
            )

    @staticmethod
    def _now(now: Optional[int]) -> int:
        return int(time.time() if now is None else now)

    @staticmethod
    def _ensure_account(connection: Any, guild_id: int, user_id: int) -> None:
        connection.execute(
            """INSERT INTO economy_accounts (guild_id, user_id)
               VALUES (%s, %s) ON CONFLICT DO NOTHING""",
            (guild_id, user_id),
        )

    @staticmethod
    def _locked_row(connection: Any, guild_id: int, user_id: int) -> dict[str, Any]:
        row = connection.execute(
            """SELECT * FROM economy_accounts
               WHERE guild_id = %s AND user_id = %s
               FOR UPDATE""",
            (guild_id, user_id),
        ).fetchone()
        if row is None:
            raise RuntimeError("Economy account was not created")
        return row

    def balance(self, guild_id: int, user_id: int, *, now: Optional[int] = None) -> int:
        timestamp = self._now(now)
        with self._pool.connection() as connection:
            row = connection.execute(
                """INSERT INTO economy_accounts (guild_id, user_id, last_activity)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (guild_id, user_id) DO UPDATE
                   SET last_activity = EXCLUDED.last_activity
                   RETURNING balance""",
                (guild_id, user_id, timestamp),
            ).fetchone()
            return int(row["balance"])

    def peek_balance(self, guild_id: int, user_id: int) -> Optional[int]:
        with self._pool.connection() as connection:
            row = connection.execute(
                """SELECT balance FROM economy_accounts
                   WHERE guild_id = %s AND user_id = %s""",
                (guild_id, user_id),
            ).fetchone()
            return None if row is None else int(row["balance"])

    def leaderboard(
        self,
        guild_id: int,
        user_id: int,
        *,
        limit: int = 10,
        now: Optional[int] = None,
    ) -> LeaderboardResult:
        timestamp = self._now(now)
        limit = max(1, int(limit))
        with self._pool.connection() as connection:
            user_row = connection.execute(
                """INSERT INTO economy_accounts (guild_id, user_id, last_activity)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (guild_id, user_id) DO UPDATE
                   SET last_activity = EXCLUDED.last_activity
                   RETURNING balance""",
                (guild_id, user_id, timestamp),
            ).fetchone()
            user_balance = int(user_row["balance"])
            rows = connection.execute(
                """SELECT user_id, balance FROM economy_accounts
                   WHERE guild_id = %s
                   ORDER BY balance DESC, user_id ASC
                   LIMIT %s""",
                (guild_id, limit),
            ).fetchall()
            entries = tuple(
                LeaderboardEntry(rank, int(row["user_id"]), int(row["balance"]))
                for rank, row in enumerate(rows, start=1)
            )
            richer_accounts = connection.execute(
                """SELECT COUNT(*) AS count FROM economy_accounts
                   WHERE guild_id = %s AND balance > %s""",
                (guild_id, user_balance),
            ).fetchone()["count"]
            earlier_ties = connection.execute(
                """SELECT COUNT(*) AS count FROM economy_accounts
                   WHERE guild_id = %s AND balance = %s AND user_id < %s""",
                (guild_id, user_balance, user_id),
            ).fetchone()["count"]
            user_rank = int(richer_accounts) + int(earlier_ties) + 1
            return LeaderboardResult(entries, user_rank, user_balance)

    def work(
        self,
        guild_id: int,
        user_id: int,
        reward: int,
        *,
        now: Optional[int] = None,
    ) -> WorkResult:
        timestamp = self._now(now)
        reward = max(0, int(reward))
        with self._pool.connection() as connection:
            self._ensure_account(connection, guild_id, user_id)
            row = self._locked_row(connection, guild_id, user_id)
            balance = int(row["balance"])
            last_work = row["last_work"]
            if last_work is not None:
                retry_after = WORK_COOLDOWN_SECONDS - (timestamp - int(last_work))
                if retry_after > 0:
                    connection.execute(
                        """UPDATE economy_accounts SET last_activity = %s
                           WHERE guild_id = %s AND user_id = %s""",
                        (timestamp, guild_id, user_id),
                    )
                    return WorkResult(0, balance, retry_after)

            balance += reward
            connection.execute(
                """UPDATE economy_accounts
                   SET balance = %s, last_work = %s, last_activity = %s
                   WHERE guild_id = %s AND user_id = %s""",
                (balance, timestamp, timestamp, guild_id, user_id),
            )
            return WorkResult(reward, balance)

    def gamble(
        self,
        guild_id: int,
        user_id: int,
        amount: int,
        won: bool,
        *,
        now: Optional[int] = None,
    ) -> GambleResult:
        result = self.settle_wager(
            guild_id,
            user_id,
            amount,
            profit=amount if won else -amount,
            now=now,
        )
        return GambleResult(result.accepted, won if result.accepted else False, result.amount, result.balance)

    def settle_wager(
        self,
        guild_id: int,
        user_id: int,
        amount: int,
        *,
        profit: int,
        now: Optional[int] = None,
    ) -> WagerResult:
        timestamp = self._now(now)
        amount = int(amount)
        profit = int(profit)
        if amount > 0 and profit < -amount:
            raise ValueError("A wager cannot lose more than its amount")
        with self._pool.connection() as connection:
            self._ensure_account(connection, guild_id, user_id)
            row = self._locked_row(connection, guild_id, user_id)
            balance = int(row["balance"])
            if amount <= 0 or amount > balance:
                connection.execute(
                    """UPDATE economy_accounts SET last_activity = %s
                       WHERE guild_id = %s AND user_id = %s""",
                    (timestamp, guild_id, user_id),
                )
                return WagerResult(False, amount, 0, balance)

            balance += profit
            connection.execute(
                """UPDATE economy_accounts SET balance = %s, last_activity = %s
                   WHERE guild_id = %s AND user_id = %s""",
                (balance, timestamp, guild_id, user_id),
            )
            return WagerResult(True, amount, profit, balance)

    def rob(
        self,
        guild_id: int,
        robber_id: int,
        target_id: int,
        *,
        succeeded: bool,
        steal_percent: int,
        fine_percent: int,
        now: Optional[int] = None,
    ) -> RobResult:
        timestamp = self._now(now)
        with self._pool.connection() as connection:
            self._ensure_account(connection, guild_id, robber_id)
            rows = connection.execute(
                """SELECT * FROM economy_accounts
                   WHERE guild_id = %s AND (user_id = %s OR user_id = %s)
                   ORDER BY user_id FOR UPDATE""",
                (guild_id, robber_id, target_id),
            ).fetchall()
            accounts = {int(row["user_id"]): row for row in rows}
            robber = accounts[robber_id]
            robber_balance = int(robber["balance"])
            connection.execute(
                """UPDATE economy_accounts SET last_activity = %s
                   WHERE guild_id = %s AND user_id = %s""",
                (timestamp, guild_id, robber_id),
            )

            last_rob = robber["last_rob"]
            if last_rob is not None:
                retry_after = ROB_COOLDOWN_SECONDS - (timestamp - int(last_rob))
                if retry_after > 0:
                    return RobResult("cooldown", 0, robber_balance, retry_after=retry_after)

            target = accounts.get(target_id)
            if target is None or target["last_activity"] is None:
                return RobResult("inactive", 0, robber_balance)
            if timestamp - int(target["last_activity"]) > ROB_ACTIVITY_WINDOW_SECONDS:
                return RobResult("inactive", 0, robber_balance)

            target_balance = int(target["balance"])
            if target_balance <= 0:
                return RobResult("broke", 0, robber_balance, target_balance)

            if succeeded:
                amount = max(1, target_balance * max(1, steal_percent) // 100)
                amount = min(amount, target_balance, 500)
                robber_balance += amount
                target_balance -= amount
                status: Literal["success", "caught"] = "success"
            else:
                amount = robber_balance * max(1, fine_percent) // 100
                amount = min(max(1, amount), robber_balance) if robber_balance else 0
                robber_balance -= amount
                target_balance += amount
                status = "caught"

            connection.execute(
                """UPDATE economy_accounts SET balance = %s, last_rob = %s
                   WHERE guild_id = %s AND user_id = %s""",
                (robber_balance, timestamp, guild_id, robber_id),
            )
            connection.execute(
                """UPDATE economy_accounts SET balance = %s
                   WHERE guild_id = %s AND user_id = %s""",
                (target_balance, guild_id, target_id),
            )
            return RobResult(status, amount, robber_balance, target_balance)

    def import_accounts(
        self,
        accounts: Iterable[Sequence[Optional[int]]],
        *,
        overwrite: bool = False,
    ) -> int:
        """Bulk import SQLite account rows, safely ignoring existing rows by default."""
        values = list(accounts)
        if not values:
            return 0
        conflict = (
            """DO UPDATE SET balance = EXCLUDED.balance,
                   last_activity = EXCLUDED.last_activity,
                   last_work = EXCLUDED.last_work,
                   last_rob = EXCLUDED.last_rob"""
            if overwrite
            else "DO NOTHING"
        )
        query = f"""INSERT INTO economy_accounts
            (guild_id, user_id, balance, last_activity, last_work, last_rob)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (guild_id, user_id) {conflict}"""
        with self._pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.executemany(query, values)
                return max(0, cursor.rowcount)
