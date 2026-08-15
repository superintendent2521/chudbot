"""One-time migration of economy accounts from SQLite to PostgreSQL."""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path
from typing import Optional

from economy_store import DEFAULT_POSTGRES_URL, PostgresEconomyStore


AccountRow = tuple[int, int, int, Optional[int], Optional[int], Optional[int]]


def read_sqlite_accounts(database_path: str | Path) -> list[AccountRow]:
    path = Path(database_path).resolve()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """SELECT guild_id, user_id, balance, last_activity, last_work, last_rob
               FROM economy_accounts
               ORDER BY guild_id, user_id"""
        ).fetchall()
        return [tuple(int(value) if value is not None else None for value in row) for row in rows]  # type: ignore[return-value]
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", default="economy.db", help="Path to the existing SQLite DB")
    parser.add_argument(
        "--database-url",
        default=(
            os.getenv("ECONOMY_DATABASE_URL")
            or os.getenv("DATABASE_URL")
            or DEFAULT_POSTGRES_URL
        ),
        help="PostgreSQL URL (defaults to ECONOMY_DATABASE_URL or DATABASE_URL)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace matching PostgreSQL rows; default is to leave them untouched",
    )
    args = parser.parse_args()
    accounts = read_sqlite_accounts(args.sqlite)
    store = PostgresEconomyStore(args.database_url, min_pool_size=1, max_pool_size=2)
    try:
        imported = store.import_accounts(accounts, overwrite=args.overwrite)
    finally:
        store.close()
    print(f"Read {len(accounts)} SQLite accounts; imported {imported} into PostgreSQL.")


if __name__ == "__main__":
    main()
