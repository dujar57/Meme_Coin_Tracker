"""
Comptes locaux (pseudo + mot de passe) et sessions par token.
Usage typique : instance SQLite sur votre machine ; pas conçu pour un gros multi-tenant public sans durcissement (HTTPS, rate-limit, etc.).
"""
from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from config import USE_POSTGRES

# PBKDF2 — stdlib uniquement
_PBKDF2_ITERS = 480_000
_SESSION_DAYS = 30

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_\-.]{3,32}$")


def validate_username(username: str) -> Optional[str]:
    u = (username or "").strip()
    if not _USERNAME_RE.match(u):
        return None
    return u


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("ascii"), _PBKDF2_ITERS
    )
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, hexdigest = stored.split("$", 1)
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("ascii"), _PBKDF2_ITERS
        )
        return secrets.compare_digest(dk.hex(), hexdigest)
    except (ValueError, AttributeError, TypeError):
        return False


def ensure_auth_tables(cursor: Any) -> None:
    if USE_POSTGRES:
        from db_backend import postgres_table_columns

        cols = postgres_table_columns(cursor, "user_saved_wallets")
        if cols and "follows" not in cols:
            cursor.execute(
                "ALTER TABLE user_saved_wallets ADD COLUMN follows INTEGER NOT NULL DEFAULT 1"
            )
        if cols and "last_synced_at" not in cols:
            cursor.execute(
                "ALTER TABLE user_saved_wallets ADD COLUMN last_synced_at TEXT"
            )
        return

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL COLLATE NOCASE UNIQUE,
            password_hash TEXT NOT NULL,
            active_wallet_address TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_saved_wallets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            address TEXT NOT NULL,
            label TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, address),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_sessions_token ON user_sessions(token)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_saved_user ON user_saved_wallets(user_id)"
    )
    _migrate_user_saved_wallets(cursor)


def _migrate_user_saved_wallets(cursor: Any) -> None:
    cursor.execute("PRAGMA table_info(user_saved_wallets)")
    cols = {str(r["name"]) for r in cursor.fetchall()}
    if "follows" not in cols:
        cursor.execute(
            "ALTER TABLE user_saved_wallets ADD COLUMN follows INTEGER NOT NULL DEFAULT 1"
        )
    if "last_synced_at" not in cols:
        cursor.execute("ALTER TABLE user_saved_wallets ADD COLUMN last_synced_at TEXT")


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    return dict(row)


def get_user_by_token(conn: Any, token: str) -> Optional[dict[str, Any]]:
    if not token or len(token) < 10:
        return None
    c = conn.cursor()
    c.execute(
        """
        SELECT u.id, u.username, u.active_wallet_address
        FROM user_sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token = ? AND datetime(s.expires_at) > datetime('now')
        """,
        (token,),
    )
    return _row_to_dict(c.fetchone())


def create_session(conn: Any, user_id: int) -> str:
    raw = secrets.token_urlsafe(48)
    exp = datetime.now(timezone.utc) + timedelta(days=_SESSION_DAYS)
    exp_s = exp.strftime("%Y-%m-%d %H:%M:%S")
    c = conn.cursor()
    c.execute(
        "INSERT INTO user_sessions (user_id, token, expires_at) VALUES (?, ?, ?)",
        (user_id, raw, exp_s),
    )
    conn.commit()
    return raw


def delete_session(conn: Any, token: str) -> None:
    c = conn.cursor()
    c.execute("DELETE FROM user_sessions WHERE token = ?", (token,))
    conn.commit()


def register_user(conn: Any, username: str, password: str) -> tuple[Optional[int], Optional[str]]:
    u = validate_username(username)
    if not u:
        return None, "Pseudo : 3–32 caractères (lettres, chiffres, _ - .)"
    if len(password) < 6:
        return None, "Mot de passe : au moins 6 caractères"
    ph = hash_password(password)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (u, ph))
        conn.commit()
        lid = c.lastrowid
        if USE_POSTGRES and lid is None:
            c.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (u,))
            row = c.fetchone()
            lid = int(row["id"]) if row else None
        return lid, None
    except sqlite3.IntegrityError:
        return None, "Ce pseudo est déjà pris"
    except Exception as e:
        if USE_POSTGRES:
            import psycopg2.errors

            if isinstance(e, psycopg2.errors.UniqueViolation):
                return None, "Ce pseudo est déjà pris"
        raise


def verify_login(conn: Any, username: str, password: str) -> tuple[Optional[int], Optional[str]]:
    u = validate_username(username)
    if not u:
        return None, "Identifiants invalides"
    c = conn.cursor()
    if USE_POSTGRES:
        c.execute(
            "SELECT id, password_hash FROM users WHERE LOWER(username) = LOWER(?)",
            (u,),
        )
    else:
        c.execute("SELECT id, password_hash FROM users WHERE username = ? COLLATE NOCASE", (u,))
    row = c.fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        return None, "Pseudo ou mot de passe incorrect"
    return int(row["id"]), None


def list_saved_wallets(conn: Any, user_id: int) -> list[dict[str, Any]]:
    c = conn.cursor()
    c.execute(
        """
        SELECT id, address, label, follows, last_synced_at, created_at
        FROM user_saved_wallets
        WHERE user_id = ? ORDER BY created_at DESC
        """,
        (user_id,),
    )
    out = []
    for r in c.fetchall():
        d = dict(r)
        fv = d.get("follows")
        d["follows"] = bool(fv) if fv is not None else True
        out.append(d)
    return out


def add_saved_wallet(
    conn: Any,
    user_id: int,
    address: str,
    label: Optional[str],
    follows: Optional[bool] = None,
) -> None:
    addr = (address or "").strip()
    if not addr:
        return
    lab = (label or "").strip() or None
    fl = 1 if (follows is None or follows) else 0
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO user_saved_wallets (user_id, address, label, follows) VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, address) DO UPDATE SET
            label = COALESCE(excluded.label, user_saved_wallets.label)
        """,
        (user_id, addr, lab, fl),
    )
    conn.commit()


def patch_saved_wallet(conn: Any, user_id: int, address: str, updates: dict[str, Any]) -> bool:
    """updates peut contenir label (str|None) et/ou follows (bool). Clés absentes = pas de changement."""
    addr = (address or "").strip()
    if not addr:
        return False
    parts: list[str] = []
    vals: list[Any] = []
    if "label" in updates:
        lv = updates["label"]
        parts.append("label = ?")
        vals.append(
            None
            if lv is None or (isinstance(lv, str) and not str(lv).strip())
            else str(lv).strip()
        )
    if "follows" in updates:
        parts.append("follows = ?")
        vals.append(1 if bool(updates["follows"]) else 0)
    if not parts:
        return False
    vals.extend([user_id, addr])
    c = conn.cursor()
    c.execute(
        f"UPDATE user_saved_wallets SET {', '.join(parts)} WHERE user_id = ? AND address = ?",
        vals,
    )
    conn.commit()
    return c.rowcount > 0


def mark_saved_wallet_synced(conn: Any, user_id: int, address: str) -> bool:
    addr = (address or "").strip()
    if not addr:
        return False
    c = conn.cursor()
    c.execute(
        """
        UPDATE user_saved_wallets SET last_synced_at = CURRENT_TIMESTAMP
        WHERE user_id = ? AND address = ?
        """,
        (user_id, addr),
    )
    conn.commit()
    return c.rowcount > 0


def user_owns_saved_wallet(conn: Any, user_id: int, address: str) -> bool:
    addr = (address or "").strip()
    if not addr:
        return False
    c = conn.cursor()
    c.execute(
        "SELECT 1 FROM user_saved_wallets WHERE user_id = ? AND address = ? LIMIT 1",
        (user_id, addr),
    )
    return c.fetchone() is not None


def remove_saved_wallet(conn: Any, user_id: int, address: str) -> bool:
    addr = (address or "").strip()
    if not addr:
        return False
    c = conn.cursor()
    c.execute(
        "DELETE FROM user_saved_wallets WHERE user_id = ? AND address = ?",
        (user_id, addr),
    )
    conn.commit()
    return c.rowcount > 0


def set_active_wallet(conn: Any, user_id: int, address: Optional[str]) -> None:
    addr = (address or "").strip() or None
    c = conn.cursor()
    c.execute("UPDATE users SET active_wallet_address = ? WHERE id = ?", (addr, user_id))
    conn.commit()
