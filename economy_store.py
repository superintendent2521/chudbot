"""Persistent, transaction-safe storage for the guild economy."""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal, Optional


STARTING_BALANCE = 250
WORK_COOLDOWN_SECONDS = 30 * 60
ROB_COOLDOWN_SECONDS = 10 * 60
ROB_ACTIVITY_WINDOW_SECONDS = 15 * 60


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


class EconomyStore:
    """Stores balances per guild and applies money changes atomically."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
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
        with self._lock, self._connection() as connection:
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
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_account(connection, guild_id, user_id)
            connection.execute(
                "UPDATE economy_accounts SET last_activity = ? WHERE guild_id = ? AND user_id = ?",
                (timestamp, guild_id, user_id),
            )
            return int(self._row(connection, guild_id, user_id)["balance"])

    def peek_balance(self, guild_id: int, user_id: int) -> Optional[int]:
        """Read a balance without marking the viewed user active."""
        with self._lock, self._connection() as connection:
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
        with self._lock, self._connection() as connection:
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
            higher_accounts = connection.execute(
                """SELECT COUNT(*)
                   FROM economy_accounts
                   WHERE guild_id = ?
                     AND (balance > ? OR (balance = ? AND user_id < ?))""",
                (guild_id, user_balance, user_balance, user_id),
            ).fetchone()[0]
            return LeaderboardResult(entries, int(higher_accounts) + 1, user_balance)

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
        with self._lock, self._connection() as connection:
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
        timestamp = self._now(now)
        amount = int(amount)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_account(connection, guild_id, user_id)
            row = self._row(connection, guild_id, user_id)
            balance = int(row["balance"])
            connection.execute(
                "UPDATE economy_accounts SET last_activity = ? WHERE guild_id = ? AND user_id = ?",
                (timestamp, guild_id, user_id),
            )
            if amount <= 0 or amount > balance:
                return GambleResult(False, False, amount, balance)

            balance += amount if won else -amount
            connection.execute(
                "UPDATE economy_accounts SET balance = ? WHERE guild_id = ? AND user_id = ?",
                (balance, guild_id, user_id),
            )
            return GambleResult(True, won, amount, balance)

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
        with self._lock, self._connection() as connection:
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
