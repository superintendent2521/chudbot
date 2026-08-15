import tempfile
import unittest
from pathlib import Path

from economy_store import SQLiteEconomyStore
from migrate_economy_to_postgres import read_sqlite_accounts


class EconomyMigrationTests(unittest.TestCase):
    def test_reader_preserves_balances_and_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "economy.db"
            store = SQLiteEconomyStore(database_path)
            store.work(123, 456, 100, now=1_000)

            accounts = read_sqlite_accounts(database_path)

        self.assertEqual(accounts, [(123, 456, 350, 1_000, 1_000, None)])


if __name__ == "__main__":
    unittest.main()
