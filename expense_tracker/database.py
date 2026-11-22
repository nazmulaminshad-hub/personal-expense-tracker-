"""SQLite persistence for income and expense transactions.

Amounts are stored as integer paisa (the smallest BDT unit) to avoid
floating-point rounding. Use ``bdt_to_paisa`` and ``paisa_to_bdt`` to
convert between BDT and paisa; conversion rejects values that cannot be
represented exactly in paisa.
"""

from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext
import os
from pathlib import Path
import sqlite3
import calendar
from typing import Any, Dict, Iterable, Iterator, List, Optional, Union


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIRECTORY = PROJECT_ROOT / "data"
DEFAULT_DATABASE_PATH = DATA_DIRECTORY / "personal_finance.db"
MAX_SQLITE_INTEGER = 2**63 - 1
MAX_CATEGORY_LENGTH = 100
MAX_NOTE_LENGTH = 1000
BACKUP_DIRECTORY = DATA_DIRECTORY / "backups"


class DatabaseError(RuntimeError):
    """Raised when a SQLite operation fails."""


def _set_private_file_permissions(path: Path) -> None:
    """Restrict database and backup files on operating systems that support POSIX modes."""
    if os.name != "nt":
        os.chmod(path, 0o600)


@contextmanager
def _connection(database_path: Union[str, Path]) -> Iterator[sqlite3.Connection]:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = None
    try:
        connection = sqlite3.connect(str(path))
        connection.row_factory = sqlite3.Row
        _set_private_file_permissions(path)
        with connection:
            yield connection
    except sqlite3.Error as exc:
        raise DatabaseError("SQLite operation failed for {!s}".format(path)) from exc
    finally:
        if connection is not None:
            connection.close()


def initialize_database(
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> None:
    """Create the database directory and transactions table if needed."""
    with _connection(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_type TEXT NOT NULL
                    CHECK (transaction_type IN ('income', 'expense')),
                amount INTEGER NOT NULL CHECK (amount > 0),
                category TEXT NOT NULL CHECK (length(trim(category)) > 0),
                transaction_date TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        _apply_schema_migrations(connection)


def _migration_is_applied(
    connection: sqlite3.Connection,
    version: int,
) -> bool:
    row = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE version = ?",
        (version,),
    ).fetchone()
    return row is not None


def _record_migration(
    connection: sqlite3.Connection,
    version: int,
) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO schema_migrations (version)
        VALUES (?)
        """,
        (version,),
    )
    connection.execute("PRAGMA user_version = {}".format(version))


def _create_indexes(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_transactions_transaction_date
        ON transactions(transaction_date)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_transactions_transaction_type
        ON transactions(transaction_type)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_transactions_category
        ON transactions(category)
        """
    )


def _existing_data_supports_strict_schema(
    connection: sqlite3.Connection,
) -> bool:
    invalid_date_count = connection.execute(
        """
        SELECT COUNT(*) FROM transactions
        WHERE transaction_date IS NULL
           OR date(transaction_date) IS NULL
           OR date(transaction_date) != transaction_date
        """
    ).fetchone()[0]
    category_too_long = connection.execute(
        """
        SELECT COUNT(*) FROM transactions
        WHERE length(category) > ?
        """,
        (MAX_CATEGORY_LENGTH,),
    ).fetchone()[0]
    note_too_long = connection.execute(
        """
        SELECT COUNT(*) FROM transactions
        WHERE note IS NULL OR length(note) > ?
        """,
        (MAX_NOTE_LENGTH,),
    ).fetchone()[0]
    invalid_category = connection.execute(
        """
        SELECT COUNT(*) FROM transactions
        WHERE category IS NULL
           OR length(trim(category)) = 0
           OR length(category) > ?
        """,
        (MAX_CATEGORY_LENGTH,),
    ).fetchone()[0]
    invalid_transaction_type = connection.execute(
        """
        SELECT COUNT(*) FROM transactions
        WHERE transaction_type NOT IN ('income', 'expense')
           OR transaction_type IS NULL
        """
    ).fetchone()[0]
    invalid_amount = connection.execute(
        """
        SELECT COUNT(*) FROM transactions
        WHERE typeof(amount) != 'integer'
           OR amount <= 0
           OR amount > ?
        """,
        (MAX_SQLITE_INTEGER,),
    ).fetchone()[0]
    return (
        invalid_date_count == 0
        and invalid_category == 0
        and invalid_transaction_type == 0
        and invalid_amount == 0
        and note_too_long == 0
    )


def _rebuild_strict_transactions_table(
    connection: sqlite3.Connection,
) -> None:
    connection.execute("DROP TABLE IF EXISTS transactions_strict_backup")
    connection.execute(
        "ALTER TABLE transactions RENAME TO transactions_strict_backup"
    )
    connection.execute(
        """
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_type TEXT NOT NULL
                CHECK (transaction_type IN ('income', 'expense')),
            amount INTEGER NOT NULL CHECK (amount > 0),
            category TEXT NOT NULL
                CHECK (
                    length(trim(category)) > 0
                    AND length(category) <= 100
                ),
            transaction_date TEXT NOT NULL
                CHECK (
                    length(transaction_date) = 10
                    AND date(transaction_date) IS NOT NULL
                    AND date(transaction_date) = transaction_date
                ),
            note TEXT NOT NULL DEFAULT ''
                CHECK (length(note) <= 1000),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        INSERT INTO transactions (
            id, transaction_type, amount, category,
            transaction_date, note, created_at
        )
        SELECT
            id, transaction_type, amount, category,
            transaction_date, note, created_at
        FROM transactions_strict_backup
        """
    )
    connection.execute("DROP TABLE transactions_strict_backup")
    connection.execute(
        """
        DELETE FROM sqlite_sequence
        WHERE name = 'transactions'
        """
    )
    connection.execute(
        """
        INSERT INTO sqlite_sequence (name, seq)
        SELECT 'transactions', COALESCE(MAX(id), 0)
        FROM transactions
        """
    )


def _create_validation_triggers(connection: sqlite3.Connection) -> None:
    connection.execute("DROP TRIGGER IF EXISTS transactions_validate_insert")
    connection.execute("DROP TRIGGER IF EXISTS transactions_validate_update")
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS transactions_validate_insert
        BEFORE INSERT ON transactions
        WHEN
            NEW.transaction_type NOT IN ('income', 'expense')
            OR NEW.amount IS NULL
            OR typeof(NEW.amount) != 'integer'
            OR NEW.amount <= 0
            OR NEW.amount > 9223372036854775807
            OR length(NEW.category) > 100
            OR length(NEW.note) > 1000
            OR NEW.category IS NULL
            OR length(trim(NEW.category)) = 0
            OR NEW.transaction_date IS NULL
            OR length(NEW.transaction_date) != 10
            OR date(NEW.transaction_date) IS NULL
            OR date(NEW.transaction_date) != NEW.transaction_date
        BEGIN
            SELECT RAISE(ABORT, 'invalid transaction data');
        END
        """
    )
    connection.execute(
        """
        CREATE TRIGGER IF NOT EXISTS transactions_validate_update
        BEFORE UPDATE ON transactions
        WHEN
            NEW.transaction_type NOT IN ('income', 'expense')
            OR NEW.amount IS NULL
            OR typeof(NEW.amount) != 'integer'
            OR NEW.amount <= 0
            OR NEW.amount > 9223372036854775807
            OR length(NEW.category) > 100
            OR length(NEW.note) > 1000
            OR NEW.category IS NULL
            OR length(trim(NEW.category)) = 0
            OR NEW.transaction_date IS NULL
            OR length(NEW.transaction_date) != 10
            OR date(NEW.transaction_date) IS NULL
            OR date(NEW.transaction_date) != NEW.transaction_date
        BEGIN
            SELECT RAISE(ABORT, 'invalid transaction data');
        END
        """
    )


def _apply_schema_migrations(connection: sqlite3.Connection) -> None:
    if not _migration_is_applied(connection, 1):
        _create_indexes(connection)
        _record_migration(connection, 1)

    if not _migration_is_applied(connection, 2):
        if _existing_data_supports_strict_schema(connection):
            _rebuild_strict_transactions_table(connection)
        _create_validation_triggers(connection)
        _create_indexes(connection)
        _record_migration(connection, 2)

    if not _migration_is_applied(connection, 3):
        _create_validation_triggers(connection)
        _create_indexes(connection)
        _record_migration(connection, 3)

    if not _migration_is_applied(connection, 4):
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS overall_budgets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month TEXT NOT NULL,
                amount INTEGER NOT NULL CHECK (amount > 0),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(month),
                CHECK (
                    length(month) = 7
                    AND substr(month, 5, 1) = '-'
                    AND date(month || '-01') IS NOT NULL
                    AND date(month || '-01') = month || '-01'
                )
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS category_budgets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month TEXT NOT NULL,
                category TEXT NOT NULL,
                amount INTEGER NOT NULL CHECK (amount > 0),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(month, category),
                CHECK (
                    length(month) = 7
                    AND substr(month, 5, 1) = '-'
                    AND date(month || '-01') IS NOT NULL
                    AND length(trim(category)) > 0
                    AND length(category) <= 100
                )
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS transaction_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(transaction_id, tag),
                FOREIGN KEY(transaction_id) REFERENCES transactions(id) ON DELETE CASCADE,
                CHECK (length(trim(tag)) > 0 AND length(tag) <= 50)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS recurring_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_type TEXT NOT NULL
                    CHECK (transaction_type IN ('income', 'expense')),
                amount INTEGER NOT NULL CHECK (amount > 0),
                category TEXT NOT NULL
                    CHECK (length(trim(category)) > 0 AND length(category) <= 100),
                note TEXT NOT NULL DEFAULT ''
                    CHECK (length(note) <= 1000),
                schedule_type TEXT NOT NULL
                    CHECK (schedule_type IN ('weekly', 'monthly')),
                start_date TEXT NOT NULL,
                next_run_date TEXT NOT NULL,
                end_date TEXT,
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK (
                    length(start_date) = 10
                    AND date(start_date) = start_date
                    AND length(next_run_date) = 10
                    AND date(next_run_date) = next_run_date
                    AND (
                        end_date IS NULL
                        OR (
                            length(end_date) = 10
                            AND date(end_date) = end_date
                        )
                    )
                )
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS recurring_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                template_id INTEGER NOT NULL,
                occurrence_date TEXT NOT NULL,
                transaction_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(template_id, occurrence_date),
                FOREIGN KEY(template_id) REFERENCES recurring_templates(id) ON DELETE CASCADE,
                FOREIGN KEY(transaction_id) REFERENCES transactions(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_transaction_tags_transaction_id "
            "ON transaction_tags(transaction_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_category_budgets_month "
            "ON category_budgets(month)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_recurring_templates_next_run "
            "ON recurring_templates(active, next_run_date)"
        )
        connection.execute("PRAGMA foreign_keys = ON")
        _record_migration(connection, 4)


def bdt_to_paisa(amount_bdt: Union[Decimal, str]) -> int:
    """Convert a positive BDT Decimal or decimal string to exact integer paisa.

    Floats are intentionally rejected. For example, use
    ``bdt_to_paisa(Decimal("123.45"))`` to get ``12345``.
    """
    if isinstance(amount_bdt, Decimal):
        amount = amount_bdt
    elif isinstance(amount_bdt, str):
        try:
            amount = Decimal(amount_bdt)
        except InvalidOperation as exc:
            raise ValueError("amount_bdt must be a valid decimal amount") from exc
    else:
        raise TypeError("amount_bdt must be a Decimal or decimal string")

    if not amount.is_finite() or amount <= 0:
        raise ValueError("amount_bdt must be a finite positive amount")

    max_amount_bdt = Decimal(MAX_SQLITE_INTEGER).scaleb(-2)
    if amount > max_amount_bdt:
        raise ValueError("amount_bdt exceeds SQLite's supported integer range")

    with localcontext() as context:
        context.prec = max(context.prec, len(amount.as_tuple().digits) + 2)
        amount_paisa = amount * Decimal(100)
    if amount_paisa != amount_paisa.to_integral_value():
        raise ValueError("amount_bdt must have no more than two non-zero decimals")

    return int(amount_paisa)


def paisa_to_bdt(amount_paisa: int) -> Decimal:
    """Convert a positive integer number of paisa to a Decimal BDT amount."""
    if isinstance(amount_paisa, bool) or not isinstance(amount_paisa, int):
        raise TypeError("amount_paisa must be an integer")
    if amount_paisa <= 0:
        raise ValueError("amount_paisa must be positive")
    if amount_paisa > MAX_SQLITE_INTEGER:
        raise ValueError("amount_paisa exceeds SQLite's supported integer range")

    return Decimal(amount_paisa).scaleb(-2)


def _validate_transaction_type(transaction_type: str) -> None:
    if transaction_type not in ("income", "expense"):
        raise ValueError("transaction_type must be 'income' or 'expense'")


def _validate_amount_paisa(amount_paisa: int) -> None:
    if isinstance(amount_paisa, bool) or not isinstance(amount_paisa, int):
        raise TypeError("amount_paisa must be an integer")
    if amount_paisa <= 0:
        raise ValueError("amount_paisa must be positive")
    if amount_paisa > MAX_SQLITE_INTEGER:
        raise ValueError("amount_paisa exceeds SQLite's supported integer range")


def _validate_text(value: str, field_name: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError("{} must be a string".format(field_name))
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise ValueError("{} must not be empty".format(field_name))
    maximum_length = (
        MAX_CATEGORY_LENGTH if field_name == "category"
        else MAX_NOTE_LENGTH if field_name == "note"
        else None
    )
    if maximum_length is not None and len(normalized) > maximum_length:
        raise ValueError(
            "{} must be at most {} characters".format(field_name, maximum_length)
        )
    return normalized


def _validate_transaction_date(transaction_date: str) -> str:
    transaction_date = _validate_text(transaction_date, "transaction_date")
    try:
        parsed_date = date.fromisoformat(transaction_date)
    except ValueError as exc:
        raise ValueError("transaction_date must use YYYY-MM-DD format") from exc
    if parsed_date.isoformat() != transaction_date:
        raise ValueError("transaction_date must use YYYY-MM-DD format")
    return transaction_date


def _validate_transaction_id(transaction_id: int) -> None:
    if isinstance(transaction_id, bool) or not isinstance(transaction_id, int):
        raise TypeError("transaction_id must be an integer")
    if transaction_id <= 0:
        raise ValueError("transaction_id must be positive")


def add_transaction(
    transaction_type: str,
    amount_paisa: int,
    category: str,
    transaction_date: str,
    note: str = "",
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> int:
    """Insert a transaction and return its generated id."""
    _validate_transaction_type(transaction_type)
    _validate_amount_paisa(amount_paisa)
    category = _validate_text(category, "category")
    transaction_date = _validate_transaction_date(transaction_date)
    note = _validate_text(note, "note", allow_empty=True)

    with _connection(database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO transactions (
                transaction_type, amount, category, transaction_date, note
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (transaction_type, amount_paisa, category, transaction_date, note),
        )
        return int(cursor.lastrowid)


def get_transaction(
    transaction_id: int,
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> Optional[Dict[str, Any]]:
    """Return one transaction by id, or None if it does not exist."""
    _validate_transaction_id(transaction_id)
    with _connection(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM transactions WHERE id = ?",
            (transaction_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def list_transactions(
    transaction_type: Optional[str] = None,
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> List[Dict[str, Any]]:
    """Return transactions in date order, optionally filtered by type."""
    query = "SELECT * FROM transactions"
    parameters = ()
    if transaction_type is not None:
        _validate_transaction_type(transaction_type)
        query += " WHERE transaction_type = ?"
        parameters = (transaction_type,)
    query += " ORDER BY transaction_date DESC, id DESC"

    with _connection(database_path) as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]


def update_transaction(
    transaction_id: int,
    transaction_type: str,
    amount_paisa: int,
    category: str,
    transaction_date: str,
    note: str = "",
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> bool:
    """Update a transaction; return False if its id does not exist."""
    _validate_transaction_id(transaction_id)
    _validate_transaction_type(transaction_type)
    _validate_amount_paisa(amount_paisa)
    category = _validate_text(category, "category")
    transaction_date = _validate_transaction_date(transaction_date)
    note = _validate_text(note, "note", allow_empty=True)

    with _connection(database_path) as connection:
        cursor = connection.execute(
            """
            UPDATE transactions
            SET transaction_type = ?, amount = ?, category = ?,
                transaction_date = ?, note = ?
            WHERE id = ?
            """,
            (
                transaction_type,
                amount_paisa,
                category,
                transaction_date,
                note,
                transaction_id,
            ),
        )
        return cursor.rowcount > 0


def delete_transaction(
    transaction_id: int,
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> bool:
    """Delete a transaction; return False if its id does not exist."""
    _validate_transaction_id(transaction_id)
    with _connection(database_path) as connection:
        cursor = connection.execute(
            "DELETE FROM transactions WHERE id = ?",
            (transaction_id,),
        )
        return cursor.rowcount > 0


def create_database_backup(
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
    backup_directory: Union[str, Path] = BACKUP_DIRECTORY,
) -> Path:
    """Create a timestamped SQLite backup that never overwrites an existing file."""
    source_path = Path(database_path)
    backup_path_directory = Path(backup_directory)
    backup_path_directory.mkdir(parents=True, exist_ok=True)

    if not source_path.is_file():
        raise DatabaseError("Cannot back up missing database: {!s}".format(source_path))

    source = sqlite3.connect("file:{}?mode=ro".format(source_path.resolve().as_posix()), uri=True)
    destination = None
    try:
        integrity = source.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise DatabaseError("Database integrity check failed before backup")

        timestamp = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = backup_path_directory / "personal_finance_{}.db".format(timestamp)
        if backup_path.exists():
            raise DatabaseError("Refusing to overwrite existing backup: {!s}".format(backup_path))

        destination = sqlite3.connect(str(backup_path))
        source.backup(destination)
        destination.close()
        destination = None
        _set_private_file_permissions(backup_path)
        return backup_path
    except sqlite3.Error as exc:
        raise DatabaseError("Could not create SQLite backup") from exc
    finally:
        if destination is not None:
            destination.close()
        source.close()


def restore_database_backup(
    backup_path: Union[str, Path],
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> Path:
    """Restore a validated backup after creating a safety backup of the current database."""
    source_path = Path(backup_path)
    target_path = Path(database_path)
    if not source_path.is_file():
        raise DatabaseError("Backup file does not exist: {!s}".format(source_path))

    source = sqlite3.connect("file:{}?mode=ro".format(source_path.resolve().as_posix()), uri=True)
    try:
        integrity = source.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise DatabaseError("Backup integrity check failed")
        source.close()

        safety_backup = None
        if target_path.is_file():
            safety_backup = create_database_backup(target_path)

        source = sqlite3.connect("file:{}?mode=ro".format(source_path.resolve().as_posix()), uri=True)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(str(target_path))
        try:
            source.backup(target)
        finally:
            target.close()
        _set_private_file_permissions(target_path)

        return safety_backup if safety_backup is not None else target_path
    except sqlite3.Error as exc:
        raise DatabaseError("Could not restore SQLite backup") from exc
    finally:
        source.close()


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_transactions_filtered(
    search: str = "",
    transaction_type: Optional[str] = None,
    category: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> List[Dict[str, Any]]:
    """Return a filtered, date-ordered transaction page from SQLite."""
    if transaction_type is not None:
        _validate_transaction_type(transaction_type)
    if category is not None:
        category = _validate_text(category, "category")
    if start_date is not None:
        start_date = _validate_transaction_date(start_date)
    if end_date is not None:
        end_date = _validate_transaction_date(end_date)
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("start_date must be on or before end_date")
    if isinstance(limit, bool) or (limit is not None and (not isinstance(limit, int) or limit < 1)):
        raise ValueError("limit must be a positive integer")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    if not isinstance(search, str):
        raise TypeError("search must be a string")

    clauses = [
        "transaction_type IN ('income', 'expense')",
        "typeof(amount) = 'integer'",
        "amount > 0",
        "category IS NOT NULL",
        "length(trim(category)) > 0",
        "length(category) <= ?",
        "note IS NOT NULL",
        "length(note) <= ?",
        "transaction_date IS NOT NULL",
        "length(transaction_date) = 10",
        "date(transaction_date) = transaction_date",
    ]
    parameters = [MAX_CATEGORY_LENGTH, MAX_NOTE_LENGTH]
    if transaction_type is not None:
        clauses.append("transaction_type = ?")
        parameters.append(transaction_type)
    if category is not None:
        clauses.append("category = ?")
        parameters.append(category)
    if start_date is not None:
        clauses.append("transaction_date >= ?")
        parameters.append(start_date)
    if end_date is not None:
        clauses.append("transaction_date <= ?")
        parameters.append(end_date)
    if search.strip():
        escaped_search = _escape_like(search.strip())
        pattern = "%{}%".format(escaped_search)
        clauses.append(
            "(category COLLATE NOCASE LIKE ? ESCAPE '\\' "
            "OR note COLLATE NOCASE LIKE ? ESCAPE '\\')"
        )
        parameters.extend((pattern, pattern))

    query = "SELECT * FROM transactions"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY transaction_date DESC, id DESC"
    if limit is not None:
        query += " LIMIT ? OFFSET ?"
        parameters.extend((limit, offset))

    with _connection(database_path) as connection:
        rows = connection.execute(query, tuple(parameters)).fetchall()
    return [dict(row) for row in rows]


def summarize_transactions_filtered(
    search: str = "",
    transaction_type: Optional[str] = None,
    category: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> Dict[str, int]:
    """Return totals and count for the same filters used by history filtering."""
    if transaction_type is not None:
        _validate_transaction_type(transaction_type)
    if category is not None:
        category = _validate_text(category, "category")
    if start_date is not None:
        start_date = _validate_transaction_date(start_date)
    if end_date is not None:
        end_date = _validate_transaction_date(end_date)
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("start_date must be on or before end_date")
    if not isinstance(search, str):
        raise TypeError("search must be a string")

    clauses = [
        "transaction_type IN ('income', 'expense')",
        "typeof(amount) = 'integer'",
        "amount > 0",
        "category IS NOT NULL",
        "length(trim(category)) > 0",
        "length(category) <= ?",
        "note IS NOT NULL",
        "length(note) <= ?",
        "transaction_date IS NOT NULL",
        "length(transaction_date) = 10",
        "date(transaction_date) = transaction_date",
    ]
    parameters = [MAX_CATEGORY_LENGTH, MAX_NOTE_LENGTH]
    if transaction_type is not None:
        clauses.append("transaction_type = ?")
        parameters.append(transaction_type)
    if category is not None:
        clauses.append("category = ?")
        parameters.append(category)
    if start_date is not None:
        clauses.append("transaction_date >= ?")
        parameters.append(start_date)
    if end_date is not None:
        clauses.append("transaction_date <= ?")
        parameters.append(end_date)
    if search.strip():
        escaped_search = _escape_like(search.strip())
        pattern = "%{}%".format(escaped_search)
        clauses.append(
            "(category COLLATE NOCASE LIKE ? ESCAPE '\\' "
            "OR note COLLATE NOCASE LIKE ? ESCAPE '\\')"
        )
        parameters.extend((pattern, pattern))

    query = "SELECT COUNT(*), COALESCE(SUM(CASE WHEN transaction_type = 'income' THEN amount ELSE 0 END), 0), COALESCE(SUM(CASE WHEN transaction_type = 'expense' THEN amount ELSE 0 END), 0) FROM transactions"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)

    with _connection(database_path) as connection:
        count, income_paisa, expense_paisa = connection.execute(
            query,
            tuple(parameters),
        ).fetchone()
    income_paisa = int(income_paisa)
    expense_paisa = int(expense_paisa)
    return {
        "transaction_count": int(count),
        "income_paisa": income_paisa,
        "expense_paisa": expense_paisa,
        "net_paisa": income_paisa - expense_paisa,
    }


def list_categories(
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> List[str]:
    """Return distinct non-empty categories in alphabetical order."""
    with _connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT category
            FROM transactions
            WHERE transaction_type IN ('income', 'expense')
              AND typeof(amount) = 'integer'
              AND amount > 0
              AND category IS NOT NULL
              AND trim(category) != ''
              AND length(category) <= ?
              AND note IS NOT NULL
              AND length(note) <= ?
              AND transaction_date IS NOT NULL
              AND length(transaction_date) = 10
              AND date(transaction_date) = transaction_date
            ORDER BY category COLLATE NOCASE ASC
            """,
            (MAX_CATEGORY_LENGTH, MAX_NOTE_LENGTH),
        ).fetchall()
    return [str(row["category"]) for row in rows]


def _validate_month(month: str) -> str:
    month = _validate_text(month, "month")
    try:
        parsed_month = date.fromisoformat(month + "-01")
    except ValueError as exc:
        raise ValueError("month must use YYYY-MM format") from exc
    if parsed_month.isoformat()[:7] != month:
        raise ValueError("month must use YYYY-MM format")
    return month


def set_overall_budget(
    month: str,
    amount_paisa: int,
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> int:
    """Create or update the overall budget for a month."""
    month = _validate_month(month)
    _validate_amount_paisa(amount_paisa)
    with _connection(database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO overall_budgets (month, amount)
            VALUES (?, ?)
            ON CONFLICT(month) DO UPDATE SET
                amount = excluded.amount,
                updated_at = CURRENT_TIMESTAMP
            """,
            (month, amount_paisa),
        )
        row = connection.execute(
            "SELECT id FROM overall_budgets WHERE month = ?",
            (month,),
        ).fetchone()
        return int(row["id"])


def set_category_budget(
    month: str,
    category: str,
    amount_paisa: int,
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> int:
    """Create or update a category budget, preventing duplicates."""
    month = _validate_month(month)
    category = _validate_text(category, "category")
    _validate_amount_paisa(amount_paisa)
    with _connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO category_budgets (month, category, amount)
            VALUES (?, ?, ?)
            ON CONFLICT(month, category) DO UPDATE SET
                amount = excluded.amount,
                updated_at = CURRENT_TIMESTAMP
            """,
            (month, category, amount_paisa),
        )
        row = connection.execute(
            """
            SELECT id FROM category_budgets
            WHERE month = ? AND category = ?
            """,
            (month, category),
        ).fetchone()
        return int(row["id"])


def list_overall_budgets(
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> List[Dict[str, Any]]:
    with _connection(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM overall_budgets ORDER BY month DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def list_category_budgets(
    month: Optional[str] = None,
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> List[Dict[str, Any]]:
    if month is not None:
        month = _validate_month(month)
    query = "SELECT * FROM category_budgets"
    parameters = ()
    if month is not None:
        query += " WHERE month = ?"
        parameters = (month,)
    query += " ORDER BY month DESC, category COLLATE NOCASE ASC"
    with _connection(database_path) as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]


def delete_overall_budget(
    month: str,
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> bool:
    month = _validate_month(month)
    with _connection(database_path) as connection:
        cursor = connection.execute(
            "DELETE FROM overall_budgets WHERE month = ?",
            (month,),
        )
        return cursor.rowcount > 0


def delete_category_budget(
    month: str,
    category: str,
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> bool:
    month = _validate_month(month)
    category = _validate_text(category, "category")
    with _connection(database_path) as connection:
        cursor = connection.execute(
            "DELETE FROM category_budgets WHERE month = ? AND category = ?",
            (month, category),
        )
        return cursor.rowcount > 0


def calculate_budget_usage(
    month: str,
    transactions: Optional[List[Dict[str, Any]]] = None,
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> Dict[str, Any]:
    """Calculate overall and category budget usage from expense rows only."""
    month = _validate_month(month)
    if transactions is None:
        start = month + "-01"
        end = (date.fromisoformat(start) + timedelta(days=32)).replace(day=1)
        end = end - timedelta(days=1)
        transactions = list_transactions_filtered(
            start_date=start,
            end_date=end.isoformat(),
            database_path=database_path,
        )

    expense_by_category = {}
    total_expense = 0
    for transaction in transactions:
        transaction_date = str(transaction.get("transaction_date") or "")
        if not transaction_date.startswith(month + "-"):
            continue
        if transaction.get("transaction_type") != "expense":
            continue
        if not isinstance(transaction.get("amount"), int):
            continue
        amount = int(transaction["amount"])
        total_expense += amount
        category = _validate_text(
            str(transaction.get("category") or "Uncategorized"),
            "category",
        )
        expense_by_category[category] = (
            expense_by_category.get(category, 0) + amount
        )

    overall_rows = list_overall_budgets(database_path=database_path)
    overall_budget = next(
        (int(row["amount"]) for row in overall_rows if row["month"] == month),
        0,
    )
    category_rows = list_category_budgets(month, database_path)
    category_usage = []
    for row in category_rows:
        spent = expense_by_category.get(row["category"], 0)
        budget = int(row["amount"])
        category_usage.append(
            {
                "category": row["category"],
                "budget_paisa": budget,
                "spent_paisa": spent,
                "remaining_paisa": budget - spent,
                "usage_percent": (spent * 100.0 / budget) if budget else 0.0,
            }
        )

    overall_remaining = overall_budget - total_expense
    return {
        "month": month,
        "overall_budget_paisa": overall_budget,
        "total_expense_paisa": total_expense,
        "remaining_paisa": overall_remaining,
        "usage_percent": (
            total_expense * 100.0 / overall_budget
            if overall_budget else 0.0
        ),
        "category_usage": category_usage,
    }


def set_transaction_tags(
    transaction_id: int,
    tags: Iterable[str],
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> None:
    """Replace tags for a transaction without duplicating tags."""
    _validate_transaction_id(transaction_id)
    normalized_tags = []
    for tag in tags:
        normalized = _validate_text(tag, "tag")
        if len(normalized) > 50:
            raise ValueError("tag must be at most 50 characters")
        if normalized not in normalized_tags:
            normalized_tags.append(normalized)

    with _connection(database_path) as connection:
        row = connection.execute(
            "SELECT 1 FROM transactions WHERE id = ?",
            (transaction_id,),
        ).fetchone()
        if row is None:
            raise ValueError("transaction_id does not exist")
        connection.execute(
            "DELETE FROM transaction_tags WHERE transaction_id = ?",
            (transaction_id,),
        )
        for tag in normalized_tags:
            connection.execute(
                """
                INSERT INTO transaction_tags (transaction_id, tag)
                VALUES (?, ?)
                """,
                (transaction_id, tag),
            )


def list_transaction_tags(
    transaction_id: Optional[int] = None,
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> List[Dict[str, Any]]:
    query = "SELECT * FROM transaction_tags"
    parameters = ()
    if transaction_id is not None:
        _validate_transaction_id(transaction_id)
        query += " WHERE transaction_id = ?"
        parameters = (transaction_id,)
    query += " ORDER BY transaction_id ASC, tag COLLATE NOCASE ASC"
    with _connection(database_path) as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]


def add_recurring_template(
    transaction_type: str,
    amount_paisa: int,
    category: str,
    note: str,
    schedule_type: str,
    start_date: str,
    end_date: Optional[str] = None,
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> int:
    """Create a recurring transaction template without creating transactions."""
    _validate_transaction_type(transaction_type)
    _validate_amount_paisa(amount_paisa)
    category = _validate_text(category, "category")
    note = _validate_text(note, "note", allow_empty=True)
    if schedule_type not in ("weekly", "monthly"):
        raise ValueError("schedule_type must be 'weekly' or 'monthly'")
    start_date = _validate_transaction_date(start_date)
    if end_date is not None:
        end_date = _validate_transaction_date(end_date)
        if end_date < start_date:
            raise ValueError("end_date must be on or after start_date")

    with _connection(database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO recurring_templates (
                transaction_type, amount, category, note,
                schedule_type, start_date, next_run_date, end_date
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transaction_type,
                amount_paisa,
                category,
                note,
                schedule_type,
                start_date,
                start_date,
                end_date,
            ),
        )
        return int(cursor.lastrowid)


def list_recurring_templates(
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> List[Dict[str, Any]]:
    with _connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM recurring_templates
            ORDER BY active DESC, next_run_date ASC, id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def delete_recurring_template(
    template_id: int,
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> bool:
    _validate_transaction_id(template_id)
    with _connection(database_path) as connection:
        cursor = connection.execute(
            "DELETE FROM recurring_templates WHERE id = ?",
            (template_id,),
        )
        return cursor.rowcount > 0


def set_recurring_template_active(
    template_id: int,
    active: bool,
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> bool:
    _validate_transaction_id(template_id)
    if not isinstance(active, bool):
        raise TypeError("active must be a boolean")
    with _connection(database_path) as connection:
        cursor = connection.execute(
            "UPDATE recurring_templates SET active = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (1 if active else 0, template_id),
        )
        return cursor.rowcount > 0


def _next_occurrence_date(
    current_date: date,
    schedule_type: str,
) -> date:
    if schedule_type == "weekly":
        return current_date + timedelta(days=7)
    year = current_date.year
    month = current_date.month + 1
    if month == 13:
        month = 1
        year += 1
    day = min(
        current_date.day,
        calendar.monthrange(year, month)[1],
    )
    return date(year, month, day)


def execute_due_recurring_templates(
    as_of_date: Optional[date] = None,
    database_path: Union[str, Path] = DEFAULT_DATABASE_PATH,
) -> List[int]:
    """Create due recurring transactions explicitly; duplicate occurrences are prevented."""
    if as_of_date is None:
        as_of_date = date.today()
    if not isinstance(as_of_date, date):
        raise TypeError("as_of_date must be a date")

    created_transaction_ids = []
    with _connection(database_path) as connection:
        templates = connection.execute(
            """
            SELECT * FROM recurring_templates
            WHERE active = 1
              AND next_run_date <= ?
              AND (
                  end_date IS NULL
                  OR next_run_date <= end_date
              )
            ORDER BY next_run_date ASC, id ASC
            """,
            (as_of_date.isoformat(),),
        ).fetchall()

        for template in templates:
            occurrence = date.fromisoformat(template["next_run_date"])
            if template["end_date"] is not None:
                if occurrence > date.fromisoformat(template["end_date"]):
                    continue

            while occurrence <= as_of_date:
                if template["end_date"] is not None:
                    if occurrence > date.fromisoformat(template["end_date"]):
                        break

                existing = connection.execute(
                    """
                    SELECT 1 FROM recurring_executions
                    WHERE template_id = ? AND occurrence_date = ?
                    """,
                    (template["id"], occurrence.isoformat()),
                ).fetchone()
                if existing is None:
                    cursor = connection.execute(
                        """
                        INSERT INTO transactions (
                            transaction_type, amount, category, transaction_date, note
                        )
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            template["transaction_type"],
                            template["amount"],
                            template["category"],
                            occurrence.isoformat(),
                            template["note"],
                        ),
                    )
                    transaction_id = int(cursor.lastrowid)
                    connection.execute(
                        """
                        INSERT INTO recurring_executions (
                            template_id, occurrence_date, transaction_id
                        )
                        VALUES (?, ?, ?)
                        """,
                        (
                            template["id"],
                            occurrence.isoformat(),
                            transaction_id,
                        ),
                    )
                    created_transaction_ids.append(transaction_id)

                occurrence = _next_occurrence_date(
                    occurrence,
                    template["schedule_type"],
                )

            connection.execute(
                """
                UPDATE recurring_templates
                SET next_run_date = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (occurrence.isoformat(), template["id"]),
            )

    return created_transaction_ids
