"""
Dashboard : cartes Gains/Pertes = P/L latent ; résultat net = latent + (gain figé − perte figée).
"""
import os
import sqlite3
import sys

import pytest

# Répertoire backend = parent de tests/
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
            current_tokens REAL,
            current_value REAL,
            current_price REAL,
            invested_amount REAL,
            purchased_tokens REAL,
            sol_usd_at_buy REAL,
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


def test_dashboard_totals_are_latent_only_excludes_realized():
    """
    100 tokens (100 USD), 50 vendus +50 USD réalisé, 50 restants valeur 100 USD vs coût ~50 -> latent +50.
    total_gain = 50 (latent). realized_gain = 50. net = 50 + 50 = 100.
    """
    wallet = "11111111111111111111111111111111"
    mint = "TokenMint1111111111111111111111111111111111"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _mk_schema(conn)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO tokens (id, name, address, wallet_address, current_tokens, current_value, current_price)
        VALUES (1, 'MEME', ?, ?, 50, 100.0, 2.0)
        """,
        (mint, wallet),
    )
    # 100 tokens, 1 SOL dépensé, SOL/USD=100 -> coût 100 USD (1 USD/token)
    c.execute(
        """
        INSERT INTO purchases (id, token_id, wallet_address, purchase_timestamp, tokens_bought, sol_spent, sol_usd_at_buy, purchase_date)
        VALUES (1, 1, ?, 1000, 100, 1.0, 100.0, '2024-01-01')
        """,
        (wallet,),
    )
    # Vente 50 tokens : reçu 1 SOL = 100 USD au moment de la vente ; coût HIFO 50 USD -> +50 réalisé
    c.execute(
        """
        INSERT INTO sales (id, token_id, tokens_sold, sol_received, sol_usd_at_sale, sale_timestamp, sale_date)
        VALUES (1, 1, 50, 1.0, 100.0, 2000, '2024-01-02')
        """,
    )
    conn.commit()

    sol_usd = 100.0
    tg, tl, net, rg, rl = m._hifo_dashboard_gain_loss_net(c, wallet, sol_usd)

    assert pytest.approx(rg, rel=1e-6) == 50.0, "réalisé attendu +50 sur la vente"
    assert pytest.approx(rl, rel=1e-6) == 0.0
    assert pytest.approx(tg, rel=1e-6) == 50.0, "carte Gains = latent seul"
    assert pytest.approx(tl, rel=1e-6) == 0.0
    assert pytest.approx(net, rel=1e-6) == 100.0, "net = latent + réalisé (50+50)"

    pmap = m._hifo_per_token_gain_loss_dict(c, wallet, sol_usd)
    assert 1 in pmap
    assert pytest.approx(pmap[1]["gain"], rel=1e-6) == 50.0
    assert pytest.approx(pmap[1]["loss"], rel=1e-6) == 0.0
    assert pytest.approx(pmap[1]["net"], rel=1e-6) == 50.0
    assert pmap[1]["latent_pnl_pct"] is not None
    assert pytest.approx(pmap[1]["latent_pnl_pct"], rel=1e-6) == 100.0  # +50 USD / 50 USD coût restant

    conn.close()


def test_realized_fige_stable_when_hifo_persisted_on_sales():
    """Gain figé = somme des hifo_pnl_usd : ne doit pas bouger si le cours SOL de la requête change."""
    wallet = "33333333333333333333333333333333"
    mint = "TokenMint3333333333333333333333333333333333"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _mk_schema(conn)
    c = conn.cursor()
    c.execute("ALTER TABLE sales ADD COLUMN hifo_pnl_usd REAL")
    c.execute("ALTER TABLE sales ADD COLUMN hifo_buy_cost_usd REAL")
    c.execute(
        """
        INSERT INTO tokens (id, name, address, wallet_address, current_tokens, current_value, current_price)
        VALUES (1, 'MEME', ?, ?, 50, 100.0, 2.0)
        """,
        (mint, wallet),
    )
    c.execute(
        """
        INSERT INTO purchases (id, token_id, wallet_address, purchase_timestamp, tokens_bought, sol_spent, sol_usd_at_buy, purchase_date)
        VALUES (1, 1, ?, 1000, 100, 1.0, 100.0, '2024-01-01')
        """,
        (wallet,),
    )
    c.execute(
        """
        INSERT INTO sales (id, token_id, tokens_sold, sol_received, sol_usd_at_sale, sale_timestamp, sale_date)
        VALUES (1, 1, 50, 1.0, 100.0, 2000, '2024-01-02')
        """,
    )
    c.execute(
        "UPDATE sales SET hifo_pnl_usd = 50, hifo_buy_cost_usd = 50 WHERE id = 1"
    )
    conn.commit()

    _, _, _, rg100, rl100 = m._hifo_dashboard_gain_loss_net(c, wallet, 100.0)
    _, _, _, rg999, rl999 = m._hifo_dashboard_gain_loss_net(c, wallet, 999.0)
    assert pytest.approx(rg100, rel=1e-6) == 50.0
    assert pytest.approx(rg999, rel=1e-6) == 50.0
    assert rl100 == rl999 == 0.0

    conn.close()


def test_fully_sold_token_zero_latent_realized_still_computed():
    """Token entièrement vendu : latent 0 sur les cartes agrégées (plus de position)."""
    wallet = "22222222222222222222222222222222"
    mint = "TokenMint2222222222222222222222222222222222"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _mk_schema(conn)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO tokens (id, name, address, wallet_address, current_tokens, current_value, current_price)
        VALUES (1, 'DEAD', ?, ?, 0, 0, 0)
        """,
        (mint, wallet),
    )
    c.execute(
        """
        INSERT INTO purchases (id, token_id, wallet_address, purchase_timestamp, tokens_bought, sol_spent, sol_usd_at_buy, purchase_date)
        VALUES (1, 1, ?, 1000, 100, 1.0, 100.0, '2024-01-01')
        """,
        (wallet,),
    )
    c.execute(
        """
        INSERT INTO sales (id, token_id, tokens_sold, sol_received, sol_usd_at_sale, sale_timestamp, sale_date)
        VALUES (1, 1, 100, 2.0, 100.0, 2000, '2024-01-02')
        """,
    )
    conn.commit()

    sol_usd = 100.0
    tg, tl, net, rg, rl = m._hifo_dashboard_gain_loss_net(c, wallet, sol_usd)
    assert tg == 0.0 and tl == 0.0
    assert rg > 0.0
    assert pytest.approx(net, rel=1e-6) == rg - rl

    pmap = m._hifo_per_token_gain_loss_dict(c, wallet, sol_usd)
    assert pmap[1]["gain"] == 0.0 and pmap[1]["loss"] == 0.0
    assert pmap[1]["latent_pnl_pct"] is None

    conn.close()
