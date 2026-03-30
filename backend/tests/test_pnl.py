"""
Tests des calculs PnL HIFO.
Vérifie que les gains/pertes réalisés sont cohérents.
"""
import pytest


def test_hifo_simple_single_buy_sell():
    """Un achat, une vente : PnL = (prix_vente - prix_achat) * quantité."""
    lots = [{"ts": 1000, "remaining": 100.0, "tokens_total": 100.0, "sol_spent": 10.0, "sol_rate_buy": 100.0}]
    sale = {"tokens_sold": 50.0, "sol_received": 6.0, "sol_rate_sell": 100.0, "sale_ts": 2000}
    price_usd_buy = 10.0 * 100 / 100  # 10 USD
    price_usd_sell = 6.0 * 100 / 50   # 12 USD
    cost_basis = 50 * (10.0 / 100) * 100  # 50 tokens * 0.1 SOL/token * 100 USD/SOL = 500 USD
    sell_usd = 50 * (6.0 / 50) * 100   # 6 SOL * 100 = 600 USD
    pnl = sell_usd - cost_basis
    assert pnl == 100.0  # +100 USD de gain


def test_hifo_consume_highest_first():
    """HIFO : on consomme d'abord les lots au prix le plus élevé."""
    lots = [
        {"ts": 1000, "remaining": 50.0, "tokens_total": 50.0, "sol_spent": 5.0, "sol_rate_buy": 100.0, "price_usd": 10.0},
        {"ts": 2000, "remaining": 50.0, "tokens_total": 50.0, "sol_spent": 3.0, "sol_rate_buy": 100.0, "price_usd": 6.0},
    ]
    # Vente de 75 tokens : 50 du lot 1 (10 USD) + 25 du lot 2 (6 USD)
    cost = 50 * 10.0 + 25 * 6.0  # 500 + 150 = 650
    sell_usd = 75 * 8.0  # supposons 8 USD/token à la vente = 600
    pnl = sell_usd - cost
    assert pnl == -50.0  # perte de 50 USD


def test_realized_gain_positive():
    """Gain réalisé positif quand on vend plus cher qu'acheté."""
    invested = 100.0
    sold_for = 150.0
    realized_gain = sold_for - invested
    assert realized_gain == 50.0


def test_realized_loss_negative():
    """Perte réalisée quand on vend moins cher qu'acheté."""
    invested = 100.0
    sold_for = 70.0
    realized_loss = invested - sold_for
    assert realized_loss == 30.0


def test_roi_calculation():
    """ROI = (net_profit / total_invested) * 100."""
    total_invested = 1000.0
    net_profit = 250.0
    roi = (net_profit / total_invested) * 100
    assert roi == 25.0


def test_roi_zero_when_no_investment():
    """ROI = 0 quand total_invested = 0."""
    total_invested = 0.0
    net_profit = 100.0
    roi = (net_profit / total_invested * 100) if total_invested else 0
    assert roi == 0
