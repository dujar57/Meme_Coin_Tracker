"""
POST /api/recalculate-history : persistance hifo_* + schéma details ;
_hifo_reconcile_lots_remaining_usd_scale : coût latent vs Σ buy après filets.
"""
import contextlib
import os
import sqlite3
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import main as m  # noqa: E402


WALLET = "11111111111111111111111111111111"
MINT = "Mint1111111111111111111111111111111111111111"


def _mk_recalc_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE tokens (
            id INTEGER PRIMARY KEY,
            name TEXT,
            address TEXT NOT NULL,
            wallet_address TEXT,
            purchase_date TEXT,
            current_tokens REAL,
            current_value REAL,
            current_price REAL,
            gain REAL DEFAULT 0,
            loss REAL DEFAULT 0,
            invested_amount REAL DEFAULT 0,
            purchased_tokens REAL DEFAULT 0,
            sol_usd_at_buy REAL,
            user_position_cost_usd REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE purchases (
            id INTEGER PRIMARY KEY,
            token_id INTEGER,
            wallet_address TEXT,
            purchase_timestamp INTEGER,
            purchase_slot INTEGER DEFAULT 0,
            tokens_bought REAL,
            sol_spent REAL,
            purchase_price REAL DEFAULT 0,
            sol_usd_at_buy REAL,
            transaction_signature TEXT,
            purchase_date TEXT
        );
        CREATE TABLE sales (
            id INTEGER PRIMARY KEY,
            token_id INTEGER,
            tokens_sold REAL,
            sol_received REAL,
            sol_usd_at_sale REAL,
            sale_timestamp INTEGER,
            sale_slot INTEGER DEFAULT 0,
            sale_date TEXT,
            sale_price REAL DEFAULT 0,
            sale_amount REAL DEFAULT 0,
            transaction_signature TEXT,
            hifo_buy_cost_usd REAL,
            hifo_pnl_usd REAL
        );
        CREATE TABLE wallet_hifo_cache (
            wallet_address TEXT PRIMARY KEY,
            realized_gain REAL NOT NULL DEFAULT 0,
            realized_loss REAL NOT NULL DEFAULT 0,
            fingerprint TEXT NOT NULL DEFAULT '',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()


def test_hifo_reconcile_remaining_scale_mends_gap():
    tid = 1
    lots_by_token = {
        tid: [
            {
                "remaining": 50.0,
                "tokens_total": 100.0,
                "sol_spent": 1.0,
                "sol_rate_buy": 100.0,
                "price_usd": 1.0,
                "ts": 0,
                "slot": 0,
            }
        ]
    }
    sells_chrono = [{"token_id": tid, "sale_id": 99, "token_amount": 50}]
    gain_per_sale = {
        99: {"buy_usd": 60.0, "sell_usd": 50.0, "pnl_usd": -10.0},
    }
    m._hifo_reconcile_lots_remaining_usd_scale(gain_per_sale, sells_chrono, lots_by_token)
    rem = m._hifo_remaining_cost_usd(lots_by_token, tid)
    assert abs(rem - 40.0) < 0.05


@patch.object(m, "_record_wallet_pnl_snapshot", lambda *a, **k: None)
@patch.object(m, "_invalidate_dashboard_cache", lambda *a, **k: None)
def test_recalculate_history_persists_hifo_and_details(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    _mk_recalc_schema(conn)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO tokens (id, name, address, wallet_address, current_tokens, current_value, current_price)
        VALUES (1, 'T', ?, ?, 50, 25, 0.5)
        """,
        (MINT, WALLET),
    )
    c.execute(
        """
        INSERT INTO purchases (id, token_id, wallet_address, purchase_timestamp, purchase_slot,
            tokens_bought, sol_spent, sol_usd_at_buy, purchase_date, transaction_signature)
        VALUES (1, 1, ?, 100, 0, 100, 1.0, 100.0, '2024-01-01', 'sigbuy1')
        """,
        (WALLET,),
    )
    c.execute(
        """
        INSERT INTO sales (id, token_id, tokens_sold, sol_received, sol_usd_at_sale, sale_timestamp,
            sale_date, transaction_signature)
        VALUES (1, 1, 50, 0.5, 100.0, 200, '2024-01-02', 'sigsell1')
        """,
    )
    conn.commit()

    @contextlib.contextmanager
    def _fake_get_db():
        yield conn

    monkeypatch.setattr(m, "get_db", _fake_get_db)

    client = TestClient(m.app)
    r = client.post(f"/api/recalculate-history?wallet={WALLET}")
    assert r.status_code == 200
    body = r.json()
    assert body["recalculated"] == 1
    assert "details_legend" in body
    assert "invested_usd" in body["details_legend"]
    d0 = body["details"][0]
    assert d0["token_id"] == 1
    assert "realized_cost" in d0 and "remaining_basis_usd" in d0
    assert abs(d0["invested_usd"] - (d0["realized_cost"] + d0["remaining_basis_usd"])) < 0.15

    row = conn.execute("SELECT hifo_buy_cost_usd, hifo_pnl_usd FROM sales WHERE id=1").fetchone()
    assert row["hifo_buy_cost_usd"] is not None
    assert row["hifo_pnl_usd"] is not None
    assert abs(float(row["hifo_buy_cost_usd"]) + float(d0["remaining_basis_usd"]) - 100.0) < 0.2
