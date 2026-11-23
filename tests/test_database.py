from contextlib import closing
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from expense_tracker.database import (
    _connection,
    DatabaseError,
    add_transaction,
    bdt_to_paisa,
    create_database_backup,
    delete_transaction,
    get_transaction,
    initialize_database,
    list_categories,
    list_transactions_filtered,
    list_transactions,
    paisa_to_bdt,
    restore_database_backup,
    summarize_transactions_filtered,
    update_transaction,
)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "nested" / "personal_finance.db"
        )
        initialize_database(self.database_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_project_connections_enable_foreign_keys(self):
        with _connection(self.database_path) as connection:
            self.assertEqual(
                connection.execute("PRAGMA foreign_keys").fetchone()[0],
                1,
            )

    def test_initialization_creates_directory_and_is_idempotent(self):
        self.assertTrue(self.database_path.is_file())
        initialize_database(self.database_path)

        with closing(sqlite3.connect(str(self.database_path))) as connection:
            columns = connection.execute(
                "PRAGMA table_info(transactions)"
            ).fetchall()

        self.assertEqual(
            [column[1] for column in columns],
            [
                "id",
                "transaction_type",
                "amount",
                "category",
                "transaction_date",
                "note",
                "created_at",
            ],
        )

    def test_database_constraints_reject_invalid_values(self):
        invalid_rows = (
            ("transfer", 100, "Other", "2026-01-01", ""),
            ("expense", 0, "Other", "2026-01-01", ""),
        )
        for values in invalid_rows:
            with self.subTest(values=values):
                with closing(sqlite3.connect(str(self.database_path))) as connection:
                    with connection:
                        with self.assertRaises(sqlite3.IntegrityError):
                            connection.execute(
                                """
                                INSERT INTO transactions (
                                    transaction_type, amount, category,
                                    transaction_date, note
                                )
                                VALUES (?, ?, ?, ?, ?)
                                """,
                                values,
                            )

    def test_add_get_list_update_and_delete_transaction(self):
        transaction_id = add_transaction(
            "expense",
            bdt_to_paisa(Decimal("123.45")),
            "Food",
            "2026-11-21",
            "Lunch; it was a user's note",
            self.database_path,
        )

        transaction = get_transaction(transaction_id, self.database_path)
        self.assertEqual(transaction["amount"], 12345)
        self.assertEqual(transaction["note"], "Lunch; it was a user's note")
        self.assertEqual(transaction["transaction_type"], "expense")
        self.assertTrue(transaction["created_at"])

        add_transaction(
            "income",
            250000,
            "Salary",
            "2026-11-01",
            database_path=self.database_path,
        )
        self.assertEqual(len(list_transactions(database_path=self.database_path)), 2)
        self.assertEqual(
            len(list_transactions("income", self.database_path)),
            1,
        )

        self.assertTrue(
            update_transaction(
                transaction_id,
                "income",
                20000,
                "Refund",
                "2026-11-20",
                "Updated note",
                self.database_path,
            )
        )
        updated = get_transaction(transaction_id, self.database_path)
        self.assertEqual(updated["transaction_type"], "income")
        self.assertEqual(updated["amount"], 20000)
        self.assertEqual(updated["category"], "Refund")
        self.assertEqual(updated["note"], "Updated note")

        self.assertTrue(delete_transaction(transaction_id, self.database_path))
        self.assertIsNone(get_transaction(transaction_id, self.database_path))
        self.assertFalse(delete_transaction(transaction_id, self.database_path))
        self.assertFalse(
            update_transaction(
                999,
                "expense",
                100,
                "Other",
                "2026-11-20",
                database_path=self.database_path,
            )
        )

    def test_amount_conversion_is_exact_and_rejects_invalid_values(self):
        self.assertEqual(bdt_to_paisa(Decimal("123.45")), 12345)
        self.assertEqual(bdt_to_paisa("0.10"), 10)
        self.assertEqual(paisa_to_bdt(12345), Decimal("123.45"))

        for invalid_amount in (
            Decimal("1.001"),
            Decimal("123.4500000000000000000000000000000000000000000000000001"),
            Decimal("0"),
            Decimal("-1"),
        ):
            with self.subTest(amount=invalid_amount):
                with self.assertRaises(ValueError):
                    bdt_to_paisa(invalid_amount)

        with self.assertRaises(TypeError):
            bdt_to_paisa(0.1)

    def test_invalid_transactions_are_rejected_before_writing(self):
        with self.assertRaises(ValueError):
            add_transaction(
                "transfer",
                100,
                "Other",
                "2026-11-21",
                database_path=self.database_path,
            )
        with self.assertRaises(ValueError):
            add_transaction(
                "expense",
                100,
                "Other",
                "21-11-2026",
                database_path=self.database_path,
            )

    def test_sqlite_errors_are_reported_as_database_errors(self):
        missing_table_path = (
            Path(self.temporary_directory.name) / "uninitialized.db"
        )
        with self.assertRaises(DatabaseError):
            add_transaction(
                "expense",
                100,
                "Other",
                "2026-11-21",
                database_path=missing_table_path,
            )

    def test_migration_preserves_existing_transactions_and_adds_indexes(self):
        with closing(sqlite3.connect(str(self.database_path))) as connection:
            connection.execute("DROP TABLE IF EXISTS schema_migrations")
            connection.execute(
                "DROP TABLE IF EXISTS transactions"
            )
            connection.execute(
                """
                CREATE TABLE transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    transaction_type TEXT,
                    amount INTEGER,
                    category TEXT,
                    transaction_date TEXT,
                    note TEXT,
                    created_at TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO transactions (
                    transaction_type, amount, category,
                    transaction_date, note, created_at
                )
                VALUES ('income', 25000, 'Salary', '2026-11-01', 'Pay', '2026-11-01 10:00:00')
                """
            )
            connection.commit()

        initialize_database(self.database_path)

        rows = list_transactions(database_path=self.database_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["amount"], 25000)
        self.assertEqual(rows[0]["category"], "Salary")

        with closing(sqlite3.connect(str(self.database_path))) as connection:
            indexes = {
                row[1]: row
                for row in connection.execute("PRAGMA index_list(transactions)")
            }
            migrations = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()

        self.assertIn("idx_transactions_transaction_date", indexes)
        self.assertIn("idx_transactions_transaction_type", indexes)
        self.assertIn("idx_transactions_category", indexes)
        self.assertEqual(
            [row[0] for row in migrations],
            [1, 2, 3, 4],
        )

    def test_malformed_legacy_dates_are_preserved_and_future_dates_are_rejected(self):
        with closing(sqlite3.connect(str(self.database_path))) as connection:
            connection.execute("DROP TABLE IF EXISTS transactions")
            connection.execute(
                """
                CREATE TABLE transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    transaction_type TEXT,
                    amount INTEGER,
                    category TEXT,
                    transaction_date TEXT,
                    note TEXT,
                    created_at TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO transactions (
                    transaction_type, amount, category,
                    transaction_date, note, created_at
                )
                VALUES ('expense', 500, 'Legacy', 'not-a-date', 'Old', '2026-01-01')
                """
            )
            connection.commit()

        initialize_database(self.database_path)

        rows = list_transactions(database_path=self.database_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["transaction_date"], "not-a-date")

        with self.assertRaises((ValueError, DatabaseError, sqlite3.IntegrityError)):
            add_transaction(
                "expense",
                100,
                "Food",
                "2026/11/21",
                "",
                self.database_path,
            )

    def test_category_and_note_length_limits_are_enforced(self):
        with self.assertRaises(ValueError):
            add_transaction(
                "expense",
                100,
                "C" * 101,
                "2026-11-21",
                "",
                self.database_path,
            )
        with self.assertRaises(ValueError):
            add_transaction(
                "expense",
                100,
                "Food",
                "2026-11-21",
                "N" * 1001,
                self.database_path,
            )

    def test_database_filtered_search_pagination_and_summary(self):
        add_transaction("expense", 100, "Food", "2026-11-01", "Lunch", self.database_path)
        add_transaction("expense", 250, "Food", "2026-11-02", "Dinner", self.database_path)
        add_transaction("income", 5000, "Salary", "2026-11-03", "Monthly", self.database_path)

        filtered = list_transactions_filtered(
            search="food",
            start_date="2026-11-01",
            end_date="2026-11-30",
            limit=1,
            offset=0,
            database_path=self.database_path,
        )
        self.assertEqual(len(filtered), 1)

        summary = summarize_transactions_filtered(
            search="food",
            start_date="2026-11-01",
            end_date="2026-11-30",
            database_path=self.database_path,
        )
        self.assertEqual(summary["transaction_count"], 2)
        self.assertEqual(summary["expense_paisa"], 350)
        self.assertEqual(summary["income_paisa"], 0)

        categories = list_categories(database_path=self.database_path)
        self.assertEqual(categories, ["Food", "Salary"])

    def test_database_filter_handles_unusual_search_and_pagination(self):
        for index in range(250):
            add_transaction(
                "expense",
                100 + index,
                "Category",
                "2026-11-{:02d}".format((index % 28) + 1),
                "Note _%_ {:03d}".format(index),
                self.database_path,
            )

        first_page = list_transactions_filtered(
            limit=100,
            offset=0,
            database_path=self.database_path,
        )
        second_page = list_transactions_filtered(
            limit=100,
            offset=100,
            database_path=self.database_path,
        )
        literal_search = list_transactions_filtered(
            search="_%_",
            database_path=self.database_path,
        )

        self.assertEqual(len(first_page), 100)
        self.assertEqual(len(second_page), 100)
        self.assertEqual(len(literal_search), 250)

    def test_database_filters_malformed_legacy_rows_safely(self):
        with closing(sqlite3.connect(str(self.database_path))) as connection:
            connection.execute("DROP TABLE IF EXISTS transactions")
            connection.execute(
                """
                CREATE TABLE transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    transaction_type TEXT,
                    amount INTEGER,
                    category TEXT,
                    transaction_date TEXT,
                    note TEXT,
                    created_at TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO transactions (
                    transaction_type, amount, category,
                    transaction_date, note
                )
                VALUES
                    ('transfer', 100, 'Bad Type', '2026-11-01', 'Bad'),
                    ('expense', 0, 'Bad Amount', '2026-11-02', 'Bad'),
                    ('expense', 500, 'Bad Date', 'not-a-date', 'Bad'),
                    ('expense', 500, 'Valid', '2026-11-03', 'Good')
                """
            )
            connection.commit()

        filtered = list_transactions_filtered(database_path=self.database_path)
        summary = summarize_transactions_filtered(database_path=self.database_path)
        categories = list_categories(database_path=self.database_path)

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["category"], "Valid")
        self.assertEqual(summary["transaction_count"], 1)
        self.assertEqual(summary["expense_paisa"], 500)
        self.assertEqual(categories, ["Valid"])

    def test_identical_valid_transactions_are_not_deduplicated(self):
        first_id = add_transaction(
            "income",
            1234,
            "Verification",
            "2026-11-22",
            "Identical",
            self.database_path,
        )
        second_id = add_transaction(
            "income",
            1234,
            "Verification",
            "2026-11-22",
            "Identical",
            self.database_path,
        )

        self.assertNotEqual(first_id, second_id)
        self.assertEqual(
            len(
                [
                    row
                    for row in list_transactions(database_path=self.database_path)
                    if row["note"] == "Identical"
                ]
            ),
            2,
        )

    def test_database_backup_and_restore_preserve_transactions(self):
        transaction_id = add_transaction(
            "income",
            10000,
            "Salary",
            "2026-11-21",
            "Backed up",
            self.database_path,
        )
        backup_path = create_database_backup(
            self.database_path,
            self.database_path.parent / "backups",
        )
        self.assertTrue(backup_path.is_file())

        delete_transaction(transaction_id, self.database_path)
        self.assertIsNone(get_transaction(transaction_id, self.database_path))

        restore_database_backup(backup_path, self.database_path)
        restored = get_transaction(transaction_id, self.database_path)
        self.assertIsNotNone(restored)
        self.assertEqual(restored["note"], "Backed up")


if __name__ == "__main__":
    unittest.main()
