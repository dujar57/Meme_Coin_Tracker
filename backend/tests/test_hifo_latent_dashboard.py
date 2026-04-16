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
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS wallet_hifo_cache (
            wallet_address TEXT PRIMARY KEY,
            realized_gain REAL NOT NULL DEFAULT 0,
            realized_loss REAL NOT NULL DEFAULT 0,
            fingerprint TEXT NOT NULL DEFAULT '',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    fp = m._wallet_hifo_fingerprint(c, wallet)
    c.execute(
        "INSERT INTO wallet_hifo_cache (wallet_address, realized_gain, realized_loss, fingerprint) VALUES (?, 50, 0, ?)",
        (wallet, fp),
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


def test_open_avg_buy_after_full_sell_and_rebuy_not_vwap_all_purchases():
    """
    Après vente totale puis rachat moins cher, le coût moyen position = dernier lot (HIFO restant),
    pas le VWAP (achat cher + achat bon marché) / somme des tokens achetés.
    """
    wallet = "44444444444444444444444444444444"
    mint = "TokenMint4444444444444444444444444444444444"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _mk_schema(conn)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO tokens (id, name, address, wallet_address, current_tokens, current_value, current_price)
        VALUES (1, 'MEME', ?, ?, 100.0, 100.0, 1.0)
        """,
        (mint, wallet),
    )
    # Achat 1 : 100 tokens, 2 SOL, SOL/USD=100 -> 2 USD/token, 200 USD de coût
    c.execute(
        """
        INSERT INTO purchases (id, token_id, wallet_address, purchase_timestamp, tokens_bought, sol_spent, sol_usd_at_buy, purchase_date)
        VALUES (1, 1, ?, 1000, 100.0, 2.0, 100.0, '2024-01-01')
        """,
        (wallet,),
    )
    c.execute(
        """
        INSERT INTO sales (id, token_id, tokens_sold, sol_received, sol_usd_at_sale, sale_timestamp, sale_date)
        VALUES (1, 1, 100.0, 1.0, 100.0, 1500, '2024-01-02')
        """,
    )
    # Achat 2 : 100 tokens, 1 SOL -> 1 USD/token, 100 USD (éligible seulement après la vente)
    c.execute(
        """
        INSERT INTO purchases (id, token_id, wallet_address, purchase_timestamp, tokens_bought, sol_spent, sol_usd_at_buy, purchase_date)
        VALUES (2, 1, ?, 2000, 100.0, 1.0, 100.0, '2024-03-01')
        """,
        (wallet,),
    )
    conn.commit()

    sol_usd = 100.0
    vwap = m._purchase_vwap_usd_for_token_id(c, 1, sol_usd)
    assert pytest.approx(vwap, rel=1e-6) == 1.5  # (200+100)/200 tokens

    _hmap, open_avg = m._hifo_per_token_gain_loss_and_open_avg(c, wallet, sol_usd)
    assert pytest.approx(open_avg[1], rel=1e-6) == 1.0  # coût lots restants / 100

    cost_map, pos_map = m._remaining_avg_cost_and_pos_by_token_ids(c, [1], sol_usd)
    assert pytest.approx(cost_map[1], rel=1e-6) == 100.0  # dernier achat seul (cycle actuel)
    assert pytest.approx(pos_map[1], rel=1e-6) == 100.0

    conn.close()


def test_auto_cost_ignores_buys_before_last_full_exit():
    """Après vente totale, seuls les achats du cycle courant comptent pour le coût auto."""
    wallet = "66666666666666666666666666666666"
    mint = "TokenMint6666666666666666666666666666666666"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _mk_schema(conn)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO tokens (id, name, address, wallet_address, current_tokens, current_value, current_price)
        VALUES (1, 'MEME', ?, ?, 100.0, 100.0, 1.0)
        """,
        (mint, wallet),
    )
    c.execute(
        """
        INSERT INTO purchases (id, token_id, wallet_address, purchase_timestamp, tokens_bought, sol_spent, sol_usd_at_buy, purchase_date, transaction_signature)
        VALUES (1, 1, ?, 100, 100.0, 5.0, 100.0, '2024-01-01', 'a1')
        """,
        (wallet,),
    )
    c.execute(
        """
        INSERT INTO sales (id, token_id, tokens_sold, sol_received, sol_usd_at_sale, sale_timestamp, sale_date, transaction_signature)
        VALUES (1, 1, 100.0, 1.0, 100.0, 200, '2024-01-02', 's1')
        """,
    )
    c.execute(
        """
        INSERT INTO purchases (id, token_id, wallet_address, purchase_timestamp, tokens_bought, sol_spent, sol_usd_at_buy, purchase_date, transaction_signature)
        VALUES (2, 1, ?, 300, 100.0, 0.8, 100.0, '2024-03-01', 'a2')
        """,
        (wallet,),
    )
    conn.commit()
    cost_map, pos_map = m._remaining_avg_cost_and_pos_by_token_ids(c, [1], 100.0)
    assert pytest.approx(cost_map[1], rel=1e-6) == 80.0
    assert pytest.approx(pos_map[1], rel=1e-6) == 100.0

    conn.close()


def test_auto_remaining_cost_partial_sell_halves_cost():
    """Vente partielle : coût restant = coût initial × (tokens restants / tokens avant vente)."""
    wallet = "55555555555555555555555555555555"
    mint = "TokenMint5555555555555555555555555555555555"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _mk_schema(conn)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO tokens (id, name, address, wallet_address, current_tokens, current_value, current_price)
        VALUES (1, 'MEME', ?, ?, 50.0, 50.0, 1.0)
        """,
        (mint, wallet),
    )
    c.execute(
        """
        INSERT INTO purchases (id, token_id, wallet_address, purchase_timestamp, tokens_bought, sol_spent, sol_usd_at_buy, purchase_date, transaction_signature)
        VALUES (1, 1, ?, 1000, 100.0, 1.0, 100.0, '2024-01-01', 'sigbuy1')
        """,
        (wallet,),
    )
    c.execute(
        """
        INSERT INTO sales (id, token_id, tokens_sold, sol_received, sol_usd_at_sale, sale_timestamp, sale_date, transaction_signature)
        VALUES (1, 1, 50.0, 0.5, 100.0, 2000, '2024-01-02', 'sigsell1')
        """,
    )
    conn.commit()
    cost_map, pos_map = m._remaining_avg_cost_and_pos_by_token_ids(c, [1], 100.0)
    assert pytest.approx(cost_map[1], rel=1e-6) == 50.0
    assert pytest.approx(pos_map[1], rel=1e-6) == 50.0

    conn.close()


def test_hifo_counts_purchase_when_purchase_row_wallet_is_null():
    """
    Achats avec `purchases.wallet_address` NULL (anciennes lignes) : le lot doit compter pour le HIFO
    dès que le token est rattaché au bon wallet (même critère que la liste des transactions).
    """
    wallet = "99999999999999999999999999999999"
    mint = "TokenMint9999999999999999999999999999999999"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _mk_schema(conn)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO tokens (id, name, address, wallet_address, current_tokens, current_value, current_price)
        VALUES (1, 'MEME', ?, ?, 0, 0, 0)
        """,
        (mint, wallet),
    )
    c.execute(
        """
        INSERT INTO purchases (id, token_id, wallet_address, purchase_timestamp, tokens_bought, sol_spent, sol_usd_at_buy, purchase_date, transaction_signature)
        VALUES (1, 1, NULL, 1000, 100.0, 1.0, 100.0, '2024-01-01', 'sigbuy9')
        """,
    )
    c.execute(
        """
        INSERT INTO sales (id, token_id, tokens_sold, sol_received, sol_usd_at_sale, sale_timestamp, sale_date, transaction_signature)
        VALUES (1, 1, 100.0, 0.95, 100.0, 2000, '2024-01-02', 'sigsell9')
        """,
    )
    conn.commit()

    gain, _lots = m._hifo_gain_per_sale_and_lots(c, wallet, 100.0)
    g1 = gain[1]
    assert pytest.approx(g1["buy_usd"], rel=1e-6) == 100.0
    assert pytest.approx(g1["sell_usd"], rel=1e-6) == 95.0
    assert pytest.approx(g1["pnl_usd"], rel=1e-6) == -5.0

    conn.close()


def test_hifo_lots_scaled_when_purchases_exceed_token_invested_usd():
    """
    Plafond min(Σ purchases USD, invested×SOL/USD sur tokens) : si la ligne token est plus basse
    (dérive / resync), le coût HIFO ne doit pas dépasser ce plafond.
    """
    wallet = "88888888888888888888888888888888"
    mint = "TokenMint8888888888888888888888888888888888"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _mk_schema(conn)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO tokens (id, name, address, wallet_address, current_tokens, current_value, current_price,
            invested_amount, purchased_tokens, sol_usd_at_buy)
        VALUES (1, 'MEME', ?, ?, 0, 0, 0, 0.5, 100.0, 100.0)
        """,
        (mint, wallet),
    )
    # Achats en BDD : 1 SOL dépensé × 100 = 100 USD (incohérent avec tokens.invested_amount = 0.5 SOL)
    c.execute(
        """
        INSERT INTO purchases (id, token_id, wallet_address, purchase_timestamp, tokens_bought, sol_spent, sol_usd_at_buy, purchase_date, transaction_signature)
        VALUES (1, 1, ?, 1000, 100.0, 1.0, 100.0, '2024-01-01', 'sigb8')
        """,
        (wallet,),
    )
    c.execute(
        """
        INSERT INTO sales (id, token_id, tokens_sold, sol_received, sol_usd_at_sale, sale_timestamp, sale_date, transaction_signature)
        VALUES (1, 1, 100.0, 0.4, 100.0, 2000, '2024-01-02', 'sigs8')
        """,
    )
    conn.commit()

    gain, _lots = m._hifo_gain_per_sale_and_lots(c, wallet, 100.0)
    g1 = gain[1]
    # min(100, 0.5*100) = 50 USD de coût max ; vente 0.4 SOL × 100 = 40 USD → P/L = -10
    assert pytest.approx(g1["buy_usd"], rel=1e-6) == 50.0
    assert pytest.approx(g1["sell_usd"], rel=1e-6) == 40.0
    assert pytest.approx(g1["pnl_usd"], rel=1e-6) == -10.0

    conn.close()


def test_hifo_cap_uses_user_position_cost_when_purchases_and_token_both_inflated():
    """
    Si Σ purchases et invested×SOL sont tous deux gonflés (ex. ~77 $) mais que l’utilisateur a saisi
    le coût réel de position (ex. 59,50 $), le plafond HIFO doit suivre ce montant.
    """
    wallet = "77777777777777777777777777777777"
    mint = "TokenMint7777777777777777777777777777777777"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _mk_schema(conn)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO tokens (id, name, address, wallet_address, current_tokens, current_value, current_price,
            invested_amount, purchased_tokens, sol_usd_at_buy, user_position_cost_usd)
        VALUES (1, 'MEME', ?, ?, 0, 0, 0, 0.77, 100.0, 100.0, 59.5)
        """,
        (mint, wallet),
    )
    c.execute(
        """
        INSERT INTO purchases (id, token_id, wallet_address, purchase_timestamp, tokens_bought, sol_spent, sol_usd_at_buy, purchase_date, transaction_signature)
        VALUES (1, 1, ?, 1000, 100.0, 0.77, 100.0, '2024-01-01', 'sigb7')
        """,
        (wallet,),
    )
    c.execute(
        """
        INSERT INTO sales (id, token_id, tokens_sold, sol_received, sol_usd_at_sale, sale_timestamp, sale_date, transaction_signature)
        VALUES (1, 1, 100.0, 0.5893, 100.0, 2000, '2024-01-02', 'sigs7')
        """,
    )
    conn.commit()

    gain, _lots = m._hifo_gain_per_sale_and_lots(c, wallet, 100.0)
    g1 = gain[1]
    assert pytest.approx(g1["buy_usd"], rel=1e-3) == 59.5
    assert pytest.approx(g1["sell_usd"], rel=1e-3) == 58.93
    assert pytest.approx(g1["pnl_usd"], rel=1e-3) == -0.57

    conn.close()


def test_hifo_cap_floor_when_min_cap_absurd_vs_sale_and_lots():
    """
    min(purchase, token, user) peut tomber très bas (ex. user_position erroné ~8,55 $) alors que
    Σ achats et lots ≈ 59,50 $ et la vente quasi intégrale ≈ 58,93 $ : ne pas afficher un gain fictif.
    """
    wallet = "55555555555555555555555555555555"
    mint = "TokenMint5555555555555555555555555555555555"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _mk_schema(conn)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO tokens (id, name, address, wallet_address, current_tokens, current_value, current_price,
            invested_amount, purchased_tokens, sol_usd_at_buy, user_position_cost_usd)
        VALUES (1, 'RIZZ', ?, ?, 0, 0, 0, 0.77, 100.0, 100.0, 8.55)
        """,
        (mint, wallet),
    )
    c.execute(
        """
        INSERT INTO purchases (id, token_id, wallet_address, purchase_timestamp, tokens_bought, sol_spent, sol_usd_at_buy, purchase_date, transaction_signature)
        VALUES (1, 1, ?, 1000, 100.0, 0.595, 100.0, '2024-01-01', 'sigb5')
        """,
        (wallet,),
    )
    c.execute(
        """
        INSERT INTO sales (id, token_id, tokens_sold, sol_received, sol_usd_at_sale, sale_timestamp, sale_date, transaction_signature)
        VALUES (1, 1, 100.0, 0.5893, 100.0, 2000, '2024-01-02', 'sigs5')
        """,
    )
    conn.commit()

    gain, _lots = m._hifo_gain_per_sale_and_lots(c, wallet, 100.0)
    g1 = gain[1]
    assert g1["buy_usd"] > 55.0, "le coût ne doit pas suivre le user_position ~8,55 $"
    assert pytest.approx(g1["sell_usd"], rel=1e-3) == 58.93
    assert g1["pnl_usd"] is not None and g1["pnl_usd"] < 0.5

    conn.close()


def test_hifo_exit_reconciliation_without_user_position_cost():
    """
    Même cas « ~77 $ en BDD mais vente ~59 $ » sans coût manuel : le plafond sortie intégrale
    (recettes vs coût gonflé) doit réduire automatiquement le coût HIFO.
    """
    wallet = "66666666666666666666666666666666"
    mint = "TokenMint6666666666666666666666666666666666"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _mk_schema(conn)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO tokens (id, name, address, wallet_address, current_tokens, current_value, current_price,
            invested_amount, purchased_tokens, sol_usd_at_buy)
        VALUES (1, 'MEME', ?, ?, 0, 0, 0, 0.77, 100.0, 100.0)
        """,
        (mint, wallet),
    )
    c.execute(
        """
        INSERT INTO purchases (id, token_id, wallet_address, purchase_timestamp, tokens_bought, sol_spent, sol_usd_at_buy, purchase_date, transaction_signature)
        VALUES (1, 1, ?, 1000, 100.0, 0.77, 100.0, '2024-01-01', 'sigb6')
        """,
        (wallet,),
    )
    c.execute(
        """
        INSERT INTO sales (id, token_id, tokens_sold, sol_received, sol_usd_at_sale, sale_timestamp, sale_date, transaction_signature)
        VALUES (1, 1, 100.0, 0.5893, 100.0, 2000, '2024-01-02', 'sigs6')
        """,
    )
    conn.commit()

    gain, _lots = m._hifo_gain_per_sale_and_lots(c, wallet, 100.0)
    g1 = gain[1]
    assert g1["buy_usd"] < 65.0, "le coût ne doit plus être ~77 $"
    assert g1["buy_usd"] > 58.5
    assert pytest.approx(g1["sell_usd"], rel=1e-3) == 58.93
    assert g1["pnl_usd"] is not None and g1["pnl_usd"] > -2.0 and g1["pnl_usd"] < 0.5

    conn.close()


def test_hifo_merges_split_purchase_rows_same_signature():
    """Deux lignes purchases (même signature) = un seul lot ; pas de double coût."""
    wallet = "44444444444444444444444444444444"
    mint = "TokenMint4444444444444444444444444444444444"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _mk_schema(conn)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO tokens (id, name, address, wallet_address, current_tokens, current_value, current_price,
            invested_amount, purchased_tokens, sol_usd_at_buy)
        VALUES (1, 'MEME', ?, ?, 0, 0, 0, 0.6, 100.0, 100.0)
        """,
        (mint, wallet),
    )
    sig = "same_sig_split_444"
    c.execute(
        """
        INSERT INTO purchases (id, token_id, wallet_address, purchase_timestamp, tokens_bought, sol_spent, sol_usd_at_buy, purchase_date, transaction_signature)
        VALUES (1, 1, ?, 1000, 50.0, 0.3, 100.0, '2024-01-01', ?)
        """,
        (wallet, sig),
    )
    c.execute(
        """
        INSERT INTO purchases (id, token_id, wallet_address, purchase_timestamp, tokens_bought, sol_spent, sol_usd_at_buy, purchase_date, transaction_signature)
        VALUES (2, 1, ?, 1000, 50.0, 0.3, 100.0, '2024-01-01', ?)
        """,
        (wallet, sig),
    )
    c.execute(
        """
        INSERT INTO sales (id, token_id, tokens_sold, sol_received, sol_usd_at_sale, sale_timestamp, sale_date, transaction_signature)
        VALUES (1, 1, 100.0, 0.59, 100.0, 2000, '2024-01-02', 'sigs4')
        """,
    )
    conn.commit()

    gain, _lots = m._hifo_gain_per_sale_and_lots(c, wallet, 100.0)
    g1 = gain[1]
    assert pytest.approx(g1["buy_usd"], rel=1e-6) == 60.0
    assert pytest.approx(g1["sell_usd"], rel=1e-6) == 59.0
    assert pytest.approx(g1["pnl_usd"], rel=1e-6) == -1.0

    conn.close()


def test_hifo_ignores_stale_low_token_cap_when_purchases_match_sale():
    """
    `tokens.invested_amount` peut rester bas alors que `purchases` reflète le vrai swap (~59 $).
    Le min() ne doit pas écraser le coût avec ~18 $ (faux gain massif).
    """
    wallet = "22222222222222222222222222222222"
    mint = "TokenMint2222222222222222222222222222222222"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _mk_schema(conn)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO tokens (id, name, address, wallet_address, current_tokens, current_value, current_price,
            invested_amount, purchased_tokens, sol_usd_at_buy)
        VALUES (1, 'Rizz', ?, ?, 0, 0, 0, 0.1862, 100.0, 100.0)
        """,
        (mint, wallet),
    )
    c.execute(
        """
        INSERT INTO purchases (id, token_id, wallet_address, purchase_timestamp, tokens_bought, sol_spent, sol_usd_at_buy, purchase_date, transaction_signature)
        VALUES (1, 1, ?, 1000, 100.0, 0.595, 100.0, '2024-01-01', 'sigriz')
        """,
        (wallet,),
    )
    c.execute(
        """
        INSERT INTO sales (id, token_id, tokens_sold, sol_received, sol_usd_at_sale, sale_timestamp, sale_date, transaction_signature)
        VALUES (1, 1, 100.0, 0.5893, 100.0, 2000, '2024-01-02', 'sellriz')
        """,
    )
    conn.commit()

    gain, _lots = m._hifo_gain_per_sale_and_lots(c, wallet, 100.0)
    g1 = gain[1]
    assert g1["buy_usd"] > 50.0, "ne doit pas tomber sur le plafond token ~18 $"
    assert pytest.approx(g1["buy_usd"], rel=1e-3) == 59.5
    assert pytest.approx(g1["sell_usd"], rel=1e-3) == 58.93
    assert pytest.approx(g1["pnl_usd"], rel=1e-3) == -0.57

    conn.close()


def test_hifo_prorata_purchase_when_lots_not_eligible_for_sale():
    """
    Si tous les lots sont « après » la vente (timestamps incohérents), le fallback token serait faux ;
    on doit utiliser le prorata Σ purchases USD / tokens achetés.
    """
    wallet = "11111111111111111111111111111112"
    mint = "TokenMint1111111111111111111111111111111211"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _mk_schema(conn)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO tokens (id, name, address, wallet_address, current_tokens, current_value, current_price,
            invested_amount, purchased_tokens, sol_usd_at_buy)
        VALUES (1, 'RIZZ', ?, ?, 0, 0, 0, 0.1862, 100.0, 100.0)
        """,
        (mint, wallet),
    )
    c.execute(
        """
        INSERT INTO purchases (id, token_id, wallet_address, purchase_timestamp, tokens_bought, sol_spent, sol_usd_at_buy, purchase_date, transaction_signature)
        VALUES (1, 1, ?, 3000000000, 100.0, 0.595, 100.0, '2024-01-01', 'sigfuture')
        """,
        (wallet,),
    )
    c.execute(
        """
        INSERT INTO sales (id, token_id, tokens_sold, sol_received, sol_usd_at_sale, sale_timestamp, sale_date, transaction_signature)
        VALUES (1, 1, 100.0, 0.5893, 100.0, 2000, '2024-06-01', 'sigsale')
        """,
    )
    conn.commit()

    gain, _lots = m._hifo_gain_per_sale_and_lots(c, wallet, 100.0)
    g1 = gain[1]
    assert pytest.approx(g1["buy_usd"], rel=1e-3) == 59.5
    assert pytest.approx(g1["sell_usd"], rel=1e-3) == 58.93
    assert pytest.approx(g1["pnl_usd"], rel=1e-3) == -0.57

    conn.close()


def test_repair_duplicate_purchase_rows_merges_sqlite():
    wallet = "33333333333333333333333333333333"
    mint = "TokenMint3333333333333333333333333333333333"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _mk_schema(conn)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO tokens (id, name, address, wallet_address, current_tokens, current_value, current_price)
        VALUES (1, 'MEME', ?, ?, 0, 0, 0)
        """,
        (mint, wallet),
    )
    sig = "dup_repair_sig"
    c.execute(
        """
        INSERT INTO purchases (id, token_id, wallet_address, purchase_timestamp, tokens_bought, sol_spent, sol_usd_at_buy, purchase_date, transaction_signature)
        VALUES (1, 1, ?, 1000, 40.0, 0.2, 100.0, '2024-01-01', ?)
        """,
        (wallet, sig),
    )
    c.execute(
        """
        INSERT INTO purchases (id, token_id, wallet_address, purchase_timestamp, tokens_bought, sol_spent, sol_usd_at_buy, purchase_date, transaction_signature)
        VALUES (2, 1, ?, 1000, 60.0, 0.4, 100.0, '2024-01-01', ?)
        """,
        (wallet, sig),
    )
    conn.commit()
    n = m._repair_duplicate_purchase_rows(conn)
    assert n == 1
    rows = c.execute("SELECT COUNT(*) AS n FROM purchases WHERE token_id = 1").fetchone()
    assert int(rows["n"]) == 1
    r = c.execute("SELECT tokens_bought, sol_spent FROM purchases WHERE token_id = 1").fetchone()
    assert pytest.approx(float(r["tokens_bought"]), rel=1e-6) == 100.0
    assert pytest.approx(float(r["sol_spent"]), rel=1e-6) == 0.6

    conn.close()
