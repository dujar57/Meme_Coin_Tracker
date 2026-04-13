"""
Couche base de données : SQLite (défaut) ou PostgreSQL (Neon, Render) via DATABASE_URL.

Active Postgres seulement si DATABASE_URL est défini ET DATABASE_BACKEND=postgres
(évite d’utiliser une URL résiduelle par erreur).
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from config import DATABASE_URL, USE_POSTGRES

if USE_POSTGRES:
    import psycopg2
    from psycopg2.extras import RealDictCursor

_INSERT_INTO_RE = re.compile(r"INSERT\s+INTO\s+(\w+)", re.IGNORECASE | re.MULTILINE)


def adapt_sql_postgres(sql: str) -> str:
    """Adapte le SQL écrit pour SQLite vers PostgreSQL (placeholders et fonctions)."""
    s = sql

    if re.search(r"INSERT\s+OR\s+IGNORE\s+INTO\s+purchases", s, re.I):
        s = re.sub(
            r"INSERT\s+OR\s+IGNORE\s+INTO\s+purchases",
            "INSERT INTO purchases",
            s,
            count=1,
            flags=re.I,
        )
        if "ON CONFLICT (token_id, transaction_signature)" not in s:
            s = s.rstrip()
            if s.endswith(")"):
                s += " ON CONFLICT (token_id, transaction_signature) DO NOTHING"

    if "INSERT OR IGNORE INTO imported_tx" in s:
        s = s.replace(
            "INSERT OR IGNORE INTO imported_tx (signature, tx_type) VALUES (?, 'buy')",
            "INSERT INTO imported_tx (signature, tx_type) VALUES (?, 'buy') ON CONFLICT (signature) DO NOTHING",
        )
        s = s.replace(
            "INSERT OR IGNORE INTO imported_tx (signature, tx_type) VALUES (?, 'sell')",
            "INSERT INTO imported_tx (signature, tx_type) VALUES (?, 'sell') ON CONFLICT (signature) DO NOTHING",
        )

    if "INSERT OR IGNORE INTO wallets" in s:
        s = s.replace(
            "INSERT OR IGNORE INTO wallets (address, label) VALUES (?, ?)",
            "INSERT INTO wallets (address, label) VALUES (?, ?) ON CONFLICT (address) DO NOTHING",
        )

    s = s.replace(":wallet", "%(wallet)s")
    s = s.replace(":wsol_mint", "%(wsol_mint)s")
    s = s.replace(":sol_now", "%(sol_now)s")

    s = s.replace("datetime('now', '-' || ? || ' days')", "(CURRENT_TIMESTAMP - (?::integer * INTERVAL '1 day'))")
    s = s.replace("datetime('now', '-23 hours')", "(NOW() - INTERVAL '23 hours')")
    s = s.replace("datetime('now', ?)", "(NOW() + ?::interval)")
    s = s.replace("datetime('now')", "NOW()")
    s = s.replace("datetime(recorded_at)", "recorded_at")
    s = s.replace("datetime(s.expires_at)", "s.expires_at")

    s = re.sub(
        r"CASE\s+WHEN\s+datetime\(\?\)\s*<\s*datetime\(\?\)",
        "CASE WHEN ?::timestamp < ?::timestamp",
        s,
        flags=re.I,
    )

    s = s.replace("DATE(ph.timestamp)", "(ph.timestamp::date)")
    s = s.replace("GROUP BY DATE(ph.timestamp)", "GROUP BY (ph.timestamp::date)")
    s = s.replace("DATE(timestamp)", "(timestamp::date)")
    s = s.replace("GROUP BY DATE(timestamp)", "GROUP BY (timestamp::date)")

    s = s.replace("IFNULL(", "COALESCE(")
    s = s.replace("MAX(0.0,", "GREATEST(0.0,")

    s = s.replace("?", "%s")
    return s


def _pg_set_lastrowid(raw_cursor: Any, original_sql: str) -> Optional[int]:
    if not re.search(r"^\s*INSERT\s+INTO\s+\w+", original_sql, re.I | re.MULTILINE):
        return None
    m = _INSERT_INTO_RE.search(original_sql)
    if not m:
        return None
    table = m.group(1)
    try:
        raw_cursor.execute(
            "SELECT currval(pg_get_serial_sequence(%s, 'id')) AS _lid",
            (table,),
        )
        row = raw_cursor.fetchone()
        if not row:
            return None
        v = row["_lid"] if isinstance(row, dict) else row[0]
        return int(v) if v is not None else None
    except Exception:
        return None


class _PgCursorWrapper:
    def __init__(self, raw: Any):
        self._raw = raw
        self.lastrowid: Optional[int] = None

    def execute(self, sql: str, params: Any = None):
        orig = sql
        adapted = adapt_sql_postgres(sql)
        self.lastrowid = None
        if params is None:
            self._raw.execute(adapted)
        else:
            self._raw.execute(adapted, params)
        lid = _pg_set_lastrowid(self._raw, orig)
        if lid is not None:
            self.lastrowid = lid
        return self

    def executemany(self, sql: str, seq_of_params: Any):
        adapted = adapt_sql_postgres(sql)
        self.lastrowid = None
        self._raw.executemany(adapted, seq_of_params)
        return self

    def fetchone(self):
        return self._raw.fetchone()

    def fetchall(self):
        return self._raw.fetchall()

    @property
    def rowcount(self) -> int:
        return self._raw.rowcount


class _PgConnWrapper:
    def __init__(self, raw: Any):
        self._raw = raw

    def cursor(self):
        return _PgCursorWrapper(self._raw.cursor(cursor_factory=RealDictCursor))

    def execute(self, sql: str, params: Any = None):
        c = self.cursor()
        c.execute(sql, params)
        return c

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()


@contextmanager
def get_pg_connection() -> Iterator[_PgConnWrapper]:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL manquant pour PostgreSQL")
    raw = psycopg2.connect(DATABASE_URL)
    try:
        yield _PgConnWrapper(raw)
    finally:
        raw.close()


def init_postgres_schema(cursor: _PgCursorWrapper | Any) -> None:
    """Schéma cible aligné sur l’état final SQLite (migrations déjà appliquées)."""
    stmts = [
        """
        CREATE TABLE IF NOT EXISTS tokens (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            address TEXT NOT NULL,
            detection_date TEXT,
            comments TEXT,
            event TEXT,
            mcap_target TEXT,
            purchase_date TEXT,
            current_tokens DOUBLE PRECISION DEFAULT 0,
            purchased_tokens DOUBLE PRECISION DEFAULT 0,
            purchase_price DOUBLE PRECISION DEFAULT 0,
            current_price DOUBLE PRECISION DEFAULT 0,
            loss DOUBLE PRECISION DEFAULT 0,
            gain DOUBLE PRECISION DEFAULT 0,
            current_value DOUBLE PRECISION DEFAULT 0,
            invested_amount DOUBLE PRECISION DEFAULT 0,
            sold_tokens DOUBLE PRECISION DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sol_usd_at_buy DOUBLE PRECISION DEFAULT NULL,
            wallet_address TEXT NOT NULL DEFAULT '',
            price_is_stale INTEGER DEFAULT 0,
            price_warning TEXT DEFAULT NULL,
            UNIQUE (address, wallet_address)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sales (
            id SERIAL PRIMARY KEY,
            token_id INTEGER NOT NULL REFERENCES tokens(id),
            sale_date TEXT NOT NULL,
            tokens_sold DOUBLE PRECISION NOT NULL,
            sale_price DOUBLE PRECISION NOT NULL,
            sale_amount DOUBLE PRECISION NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            transaction_signature TEXT,
            sale_timestamp BIGINT,
            sale_slot INTEGER DEFAULT 0,
            sol_received DOUBLE PRECISION DEFAULT 0,
            sol_usd_at_sale DOUBLE PRECISION DEFAULT NULL,
            hifo_buy_cost_usd DOUBLE PRECISION DEFAULT NULL,
            hifo_pnl_usd DOUBLE PRECISION DEFAULT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS price_history (
            id SERIAL PRIMARY KEY,
            token_id INTEGER NOT NULL REFERENCES tokens(id),
            price DOUBLE PRECISION NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS purchases (
            id SERIAL PRIMARY KEY,
            token_id INTEGER NOT NULL REFERENCES tokens(id),
            purchase_date TEXT NOT NULL,
            purchase_timestamp BIGINT,
            tokens_bought DOUBLE PRECISION NOT NULL,
            purchase_price DOUBLE PRECISION NOT NULL,
            sol_spent DOUBLE PRECISION NOT NULL,
            transaction_signature TEXT NOT NULL,
            sol_usd_at_buy DOUBLE PRECISION DEFAULT NULL,
            wallet_address TEXT DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            purchase_slot INTEGER DEFAULT 0,
            UNIQUE (token_id, transaction_signature)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS wallet_sol_flow (
            wallet_address TEXT PRIMARY KEY,
            sol_recu DOUBLE PRECISION NOT NULL DEFAULT 0,
            sol_envoye DOUBLE PRECISION NOT NULL DEFAULT 0,
            pages_scanned INTEGER NOT NULL DEFAULT 0,
            updated_ts DOUBLE PRECISION NOT NULL DEFAULT 0,
            flow_agg_version INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS imported_tx (
            signature TEXT PRIMARY KEY,
            tx_type TEXT NOT NULL,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS wallet_hifo_cache (
            wallet_address TEXT PRIMARY KEY,
            realized_gain DOUBLE PRECISION NOT NULL DEFAULT 0,
            realized_loss DOUBLE PRECISION NOT NULL DEFAULT 0,
            fingerprint TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS wallet_pnl_snapshots (
            id SERIAL PRIMARY KEY,
            wallet_address TEXT NOT NULL,
            recorded_at TIMESTAMP NOT NULL,
            net_pnl_usd DOUBLE PRECISION NOT NULL,
            total_invested_usd DOUBLE PRECISION DEFAULT 0,
            current_value_usd DOUBLE PRECISION DEFAULT 0,
            withdrawn_usd DOUBLE PRECISION DEFAULT 0
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS token_targets (
            id SERIAL PRIMARY KEY,
            token_id INTEGER NOT NULL REFERENCES tokens(id),
            wallet_address TEXT,
            mcap_target TEXT,
            tp_price DOUBLE PRECISION,
            sl_price DOUBLE PRECISION,
            alert_enabled INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS token_notes (
            id SERIAL PRIMARY KEY,
            token_id INTEGER NOT NULL REFERENCES tokens(id),
            note_date TEXT NOT NULL,
            content TEXT,
            event_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS wallets (
            address TEXT PRIMARY KEY,
            label TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS wallet_reference_capital (
            wallet_address TEXT PRIMARY KEY,
            amount_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            active_wallet_address TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS user_saved_wallets (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            address TEXT NOT NULL,
            label TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            follows INTEGER NOT NULL DEFAULT 1,
            last_synced_at TEXT,
            UNIQUE (user_id, address)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS user_sessions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token TEXT NOT NULL UNIQUE,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ]
    for stmt in stmts:
        cursor.execute(stmt)

    indexes = [
        ("idx_wallet_pnl_wallet_time", "wallet_pnl_snapshots", "(wallet_address, recorded_at)"),
        ("idx_tokens_wallet", "tokens", "(wallet_address)"),
        ("idx_tokens_address", "tokens", "(address)"),
        ("idx_sales_token", "sales", "(token_id)"),
        ("idx_sales_signature", "sales", "(transaction_signature)"),
        ("idx_sales_date", "sales", "(sale_date)"),
        ("idx_purchases_token", "purchases", "(token_id)"),
        ("idx_purchases_signature", "purchases", "(transaction_signature)"),
        ("idx_purchases_wallet", "purchases", "(wallet_address)"),
        ("idx_purchases_timestamp", "purchases", "(purchase_timestamp)"),
        ("idx_imported_tx_sig", "imported_tx", "(signature)"),
        ("idx_price_history_token", "price_history", "(token_id)"),
        ("idx_price_history_timestamp", "price_history", "(timestamp)"),
        ("idx_sales_token_date", "sales", "(token_id, sale_date)"),
        ("idx_purchases_token_timestamp", "purchases", "(token_id, purchase_timestamp)"),
        ("idx_tokens_wallet_created", "tokens", "(wallet_address, created_at)"),
        ("idx_tokens_wallet_mint", "tokens", "(wallet_address, address)"),
        ("idx_purchases_wallet_ts", "purchases", "(wallet_address, purchase_timestamp)"),
        ("idx_sales_token_ts", "sales", "(token_id, sale_timestamp)"),
        ("idx_price_history_token_ts", "price_history", "(token_id, timestamp)"),
        ("idx_user_sessions_token", "user_sessions", "(token)"),
        ("idx_user_saved_user", "user_saved_wallets", "(user_id)"),
        ("idx_token_targets_token", "token_targets", "(token_id)"),
        ("idx_token_notes_token", "token_notes", "(token_id)"),
        ("idx_users_username_lower", "users", "(LOWER(username))"),
    ]
    for idx_name, table_name, column_spec in indexes:
        if idx_name == "idx_users_username_lower":
            cursor.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {idx_name} ON {table_name} {column_spec}"
            )
        else:
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table_name} {column_spec}")

    for partial_sql in (
        "CREATE INDEX IF NOT EXISTS idx_purchases_token_active ON purchases(token_id) WHERE tokens_bought > 0 AND sol_spent > 0",
        "CREATE INDEX IF NOT EXISTS idx_purchases_wallet_active ON purchases(wallet_address, token_id) WHERE tokens_bought > 0 AND sol_spent > 0",
    ):
        cursor.execute(partial_sql)

    try:
        cursor.execute("ANALYZE tokens")
        cursor.execute("ANALYZE purchases")
        cursor.execute("ANALYZE sales")
        cursor.execute("ANALYZE price_history")
    except Exception:
        pass


def wipe_all_postgres_data(cursor: _PgCursorWrapper | Any) -> list[str]:
    """TRUNCATE des tables applicatives (ordre respectant les FK)."""
    tables = [
        "user_sessions",
        "user_saved_wallets",
        "users",
        "wallet_pnl_snapshots",
        "wallet_hifo_cache",
        "wallet_sol_flow",
        "imported_tx",
        "wallet_reference_capital",
        "wallets",
        "token_notes",
        "token_targets",
        "price_history",
        "purchases",
        "sales",
        "tokens",
        "settings",
    ]
    cleared: list[str] = []
    cursor.execute("TRUNCATE TABLE " + ", ".join(tables) + " RESTART IDENTITY CASCADE")
    cleared.extend(tables)
    return cleared


def is_unique_constraint_error(exc: BaseException) -> bool:
    import sqlite3

    if isinstance(exc, sqlite3.IntegrityError):
        return True
    if USE_POSTGRES:
        import psycopg2.errors

        return isinstance(exc, psycopg2.errors.UniqueViolation)
    return False


def postgres_table_columns(cursor: _PgCursorWrapper | Any, table: str) -> set[str]:
    cursor.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table,),
    )
    return {str(r["column_name"]) for r in cursor.fetchall()}
