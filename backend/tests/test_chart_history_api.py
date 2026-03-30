"""
Vérifie les endpoints d’historique pour les graphiques (période / pas de wallet).
"""
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import main as m  # noqa: E402


@pytest.fixture
def client():
    return TestClient(m.app)


def test_wallet_pnl_history_empty_without_wallet(client):
    assert client.get("/api/wallet-pnl-history").json() == []
    assert client.get("/api/wallet-pnl-history?wallet=").json() == []


@patch.object(m, "_get_sol_usd_price", new_callable=AsyncMock, return_value=150.0)
def test_wallet_pnl_history_accepts_days_param(_mock_sol, client):
    """Ne doit pas planter avec days=7 ; réponse = liste (éventuellement vide)."""
    r = client.get(
        "/api/wallet-pnl-history?wallet=So11111111111111111111111111111111111111112&days=7"
    )
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    for row in body:
        assert "date" in row
        assert "net_pnl_usd" in row


@patch.object(m, "_get_sol_usd_price", new_callable=AsyncMock, return_value=150.0)
def test_wallet_pnl_history_days_zero_like_full(_mock_sol, client):
    r0 = client.get(
        "/api/wallet-pnl-history?wallet=So11111111111111111111111111111111111111112&days=0"
    )
    r_all = client.get(
        "/api/wallet-pnl-history?wallet=So11111111111111111111111111111111111111112"
    )
    assert r0.status_code == 200 and r_all.status_code == 200
    assert isinstance(r0.json(), list) and isinstance(r_all.json(), list)


def test_portfolio_history_returns_list(client):
    r = client.get("/api/portfolio-history")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_portfolio_history_days_window(client):
    r = client.get("/api/portfolio-history?days=7")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    for row in body:
        assert "date" in row and "value" in row


def test_portfolio_history_days_zero_full(client):
    m._charts_cache.clear()
    r0 = client.get("/api/portfolio-history?days=0")
    m._charts_cache.clear()
    r_omit = client.get("/api/portfolio-history")
    assert r0.status_code == 200 and r_omit.status_code == 200
    assert isinstance(r0.json(), list) and isinstance(r_omit.json(), list)


def test_portfolio_history_with_wallet_param(client):
    m._charts_cache.clear()
    r = client.get(
        "/api/portfolio-history?wallet=So11111111111111111111111111111111111111112"
    )
    assert r.status_code == 200
    assert isinstance(r.json(), list)
