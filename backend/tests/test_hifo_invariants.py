"""
Invariants globaux sur le HIFO / P+L réalisé : identité pnl = vente − coût,
cohérence dashboard, garde-fous repair (pas de perte « inventée » si prorata achat >> vente).
"""
import os
import sqlite3
import sys

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import main as m  # noqa: E402


def _mk_schema(conn: sqlite3.Connection) -> None:
    c = conn.cursor()
    c.executescript(
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
            invested_amount REAL,
            purchased_tokens REAL,
            sol_usd_at_buy REAL,
            user_position_cost_usd REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
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
            transaction_signature TEXT
        );
        """
    )
    conn.commit()


def _audit_gain_dict(gain: dict) -> list[str]:
    """Retourne une liste d’anomalies (vide = OK)."""
    issues: list[str] = []
    for sid, row in gain.items():
        if not isinstance(row, dict):
            continue
        pnl = row.get("pnl_usd")
        buy = row.get("buy_usd")
        sell = row.get("sell_usd")
        if pnl is None or buy is None or sell is None:
            continue
        p, b, s = float(pnl), float(buy), float(sell)
        if b < -1e-6 or s < -1e-6:
            issues.append(f"sale {sid}: buy ou sell négatif")
            continue
        if abs(p - (s - b)) > 0.05:
            issues.append(f"sale {sid}: pnl {p} != sell−buy ({s}-{b})")
        if abs(p) > max(s, b, 1.0) * 5 + 5000:
            issues.append(f"sale {sid}: |pnl|={p} excessif vs vente/coût")
    return issues


def test_invariant_pnl_equals_sell_minus_buy_two_sales():
    wallet = "12121212121212121212121212121212"
    mint = "Mint1212121212121212121212121212121212121212"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _mk_schema(conn)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO tokens (id, name, address, wallet_address, current_tokens, current_value, current_price)
        VALUES (1, 'A', ?, ?, 0, 0, 0)
        """,
        (mint, wallet),
    )
    c.execute(
        """
        INSERT INTO purchases (id, token_id, wallet_address, purchase_timestamp, tokens_bought, sol_spent, sol_usd_at_buy, purchase_date, transaction_signature)
        VALUES (1, 1, ?, 100, 100, 1.0, 100.0, '2024-01-01', 'a'),
               (2, 1, ?, 200, 100, 0.5, 100.0, '2024-01-02', 'b')
        """,
        (wallet, wallet),
    )
    c.execute(
        """
        INSERT INTO sales (id, token_id, tokens_sold, sol_received, sol_usd_at_sale, sale_timestamp, sale_date, transaction_signature)
        VALUES (1, 1, 100, 0.8, 100.0, 300, '2024-01-03', 's1'),
               (2, 1, 100, 0.4, 100.0, 400, '2024-01-04', 's2')
        """,
    )
    conn.commit()
    gain = m._compute_hifo_gain_per_sale(c, wallet, 100.0)
    assert _audit_gain_dict(gain) == []
    conn.close()


def test_repair_skips_when_vwap_exceeds_112pct_sell_invented_loss():
    """Prorata achat >> vente : ne pas forcer un coût HIFO qui crée une perte énorme fictive."""
    sales = [{"sale_id": 1, "token_id": 1, "token_amount": 1.0}]
    gain = {1: {"sell_usd": 100.0, "buy_usd": 10.0, "pnl_usd": 90.0}}
    pc = {1: 150.0}
    bt = {1: 1.0}
    m._repair_gain_per_sale_buy_vs_purchase_caps(gain, sales, pc, bt)
    assert gain[1]["buy_usd"] == 10.0
    assert gain[1]["pnl_usd"] == 90.0


def test_dashboard_realized_matches_manual_sum_after_multi_token():
    wallet = "13131313131313131313131313131313"
    mint = "Mint1313131313131313131313131313131313131313"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _mk_schema(conn)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO tokens (id, name, address, wallet_address, current_tokens, current_value, current_price)
        VALUES (1, 'X', ?, ?, 0, 0, 0),
               (2, 'Y', ?, ?, 0, 0, 0)
        """,
        (mint, wallet, mint + "b", wallet),
    )
    c.execute(
        """
        INSERT INTO purchases (id, token_id, wallet_address, purchase_timestamp, tokens_bought, sol_spent, sol_usd_at_buy, purchase_date, transaction_signature)
        VALUES (1, 1, ?, 100, 10, 0.2, 100.0, '2024-01-01', 'p1'),
               (2, 2, ?, 100, 10, 0.3, 100.0, '2024-01-01', 'p2')
        """,
        (wallet, wallet),
    )
    c.execute(
        """
        INSERT INTO sales (id, token_id, tokens_sold, sol_received, sol_usd_at_sale, sale_timestamp, sale_date, transaction_signature)
        VALUES (1, 1, 10, 0.25, 100.0, 200, '2024-01-02', 's1'),
               (2, 2, 10, 0.35, 100.0, 200, '2024-01-02', 's2')
        """,
    )
    conn.commit()
    sol = 100.0
    gain = m._compute_hifo_gain_per_sale(c, wallet, sol)
    assert _audit_gain_dict(gain) == []
    rg = sum(float(x["pnl_usd"]) for x in gain.values() if x.get("pnl_usd") is not None and float(x["pnl_usd"]) > 0)
    rl = sum(
        abs(float(x["pnl_usd"]))
        for x in gain.values()
        if x.get("pnl_usd") is not None and float(x["pnl_usd"]) < 0
    )
    _, _, _, rg_d, rl_d = m._hifo_dashboard_gain_loss_net(c, wallet, sol)
    assert pytest.approx(rg, rel=1e-5) == rg_d
    assert pytest.approx(rl, rel=1e-5) == rl_d
    conn.close()


def test_hifo_uses_token_buy_rate_when_purchase_rate_missing():
    """Si purchase.sol_usd_at_buy manque, utiliser tokens.sol_usd_at_buy (pas le SOL spot actuel)."""
    wallet = "14141414141414141414141414141414"
    mint = "Mint1414141414141414141414141414141414141414"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _mk_schema(conn)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO tokens (
            id, name, address, wallet_address, current_tokens, current_value, current_price,
            invested_amount, purchased_tokens, sol_usd_at_buy
        )
        VALUES (1, 'Z', ?, ?, 0, 0, 0, 0.59, 100, 100.0)
        """,
        (mint, wallet),
    )
    c.execute(
        """
        INSERT INTO purchases (
            id, token_id, wallet_address, purchase_timestamp, tokens_bought,
            sol_spent, sol_usd_at_buy, purchase_date, transaction_signature
        )
        VALUES (1, 1, ?, 100, 100, 0.59, NULL, '2024-01-01', 'p1')
        """,
        (wallet,),
    )
    c.execute(
        """
        INSERT INTO sales (
            id, token_id, tokens_sold, sol_received, sol_usd_at_sale,
            sale_timestamp, sale_date, transaction_signature
        )
        VALUES (1, 1, 100, 0.58, 100.0, 200, '2024-01-02', 's1')
        """,
    )
    conn.commit()

    # Prix spot volontairement plus élevé : avant fix, le coût pouvait être surévalué (~0.59*130=76.7$).
    gain = m._compute_hifo_gain_per_sale(c, wallet, 130.0)
    row = gain[1]
    assert pytest.approx(58.0, rel=1e-6) == float(row["sell_usd"])
    assert pytest.approx(59.0, rel=1e-6) == float(row["buy_usd"])
    assert pytest.approx(-1.0, rel=1e-6) == float(row["pnl_usd"])
    conn.close()


def test_hifo_prefers_exact_remaining_lot_for_round_trip():
    """Vente ~= reliquat d'un lot : prioriser ce lot pour éviter un coût artificiellement éloigné."""
    wallet = "15151515151515151515151515151515"
    mint = "Mint1515151515151515151515151515151515151515"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _mk_schema(conn)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO tokens (id, name, address, wallet_address, current_tokens, current_value, current_price)
        VALUES (1, 'R', ?, ?, 0, 0, 0)
        """,
        (mint, wallet),
    )
    # Lot ancien plus cher + lot récent dont le reliquat matche exactement la vente.
    c.execute(
        """
        INSERT INTO purchases (id, token_id, wallet_address, purchase_timestamp, tokens_bought, sol_spent, sol_usd_at_buy, purchase_date, transaction_signature)
        VALUES
            (1, 1, ?, 100, 100, 0.7, 100.0, '2024-01-01', 'old-expensive'),
            (2, 1, ?, 200, 100, 0.59, 100.0, '2024-01-02', 'recent-match')
        """,
        (wallet, wallet),
    )
    c.execute(
        """
        INSERT INTO sales (id, token_id, tokens_sold, sol_received, sol_usd_at_sale, sale_timestamp, sale_date, transaction_signature)
        VALUES (1, 1, 100, 0.58, 100.0, 300, '2024-01-03', 's1')
        """,
    )
    conn.commit()

    gain = m._compute_hifo_gain_per_sale(c, wallet, 100.0)
    row = gain[1]
    assert pytest.approx(58.0, rel=1e-6) == float(row["sell_usd"])
    assert pytest.approx(59.0, rel=1e-6) == float(row["buy_usd"])
    assert pytest.approx(-1.0, rel=1e-6) == float(row["pnl_usd"])
    conn.close()
