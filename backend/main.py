from fastapi import FastAPI, HTTPException, Query, Request, Body, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from datetime import datetime, timezone
import sqlite3
import math
import httpx
import asyncio
import os
import time
import csv
import io
from contextlib import contextmanager
from collections import defaultdict
from dotenv import load_dotenv

from config import (
    ALLOWED_ORIGINS,
    API_KEY,
    BIRDEYE_API_KEY,
    CORS_ALLOW_HEADERS,
    CORS_ALLOW_METHODS,
    ENV_NAME_HELIUS_API_KEY,
    ENV_NAME_SERVICE_API_KEY,
    HELIUS_API_KEY,
    IS_PROD,
    JUPITER_API_KEY,
    JUPITER_PRICE_V3_FALLBACK,
    JUPITER_PRICE_V3_LITE,
    REQUIRE_API_KEY,
    TRUSTED_HOSTS,
    USE_POSTGRES,
    USE_TRUSTED_HOST,
)
from db_backend import (
    get_pg_connection,
    init_postgres_schema,
    is_unique_constraint_error,
    postgres_table_columns,
    wipe_all_postgres_data,
)
import auth_service

# Charger le .env depuis le dossier du fichier (chemin absolu, indépendant du cwd)
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# Mint natif SOL / wSOL (même adresse sur Solana)
SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_SOL = 1_000_000_000
# Prix Jupiter v3 : lite-api en priorité, secours api.jup.ag (voir config / variables d’env)
def _jupiter_price_headers() -> dict:
    h = {"User-Agent": "MemeCoinTracker/1.0"}
    if JUPITER_API_KEY:
        h["x-api-key"] = JUPITER_API_KEY
    return h


def _jupiter_parse_prices_json(payload: dict, mints: List[str]) -> dict[str, float]:
    """
    Jupiter Price v2 : {"data": {mint: {"price": ...}}}
    Price v3 (lite-api) : {mint: {"usdPrice": ...}} sans enveloppe "data".
    """
    out: dict[str, float] = {}
    if not payload or not isinstance(payload, dict):
        return out
    block = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(block, dict):
        return out
    for addr in mints:
        node = block.get(addr)
        if not isinstance(node, dict):
            continue
        try:
            p = float(node.get("usdPrice") or node.get("price") or 0)
        except (TypeError, ValueError):
            p = 0.0
        if p > 0:
            out[addr] = p
    return out


# Tokens à ignorer : vide = tout tracer (USDC, USDT, mSOL, meme coins, etc.)
BLACKLISTED_MINTS: set[str] = set()

# À l’import Helius : ne pas traiter wSOL comme achat/vente de « token » (c’est du SOL wrappé, pas un meme).
_IMPORT_IGNORE_SPL_MINTS: frozenset[str] = frozenset({SOL_MINT})

# Stables / quote souvent reçus ou envoyés comme jambe intermédiaire (Jupiter, etc.). Ne pas répartir
# sol_spent / sol_recv dessus quand un autre jeton non-stable est dans la même tx — sinon le coût
# est divisé par 2 (ex. ~50 € affichés comme ~25 €).
_SWAP_INTERMEDIATE_STABLE_MINTS: frozenset[str] = frozenset(
    {
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
        "2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo",  # PayPal USD (PYUSD)
        "USDH1SM1ojwWUga67PGrgFWUHrytwVQHD9sjXHZkhf",  # USDH
        "9zNQRsGLjNKwCUU5Gq5LR8beUCPzQMVMqKAi3SSZh54u",  # FDUSD
        "2uYLdN8wjW2VdvRsTWqpXVovPLV89sSZUGNfKAQdjZz",  # EURC
    }
)

# Comptes Jito tip (MEV) — exclure des transferts natifs « sortants » du wallet
JITO_TIP_ACCOUNTS: frozenset[str] = frozenset(
    {
        "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
        "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
        "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
        "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1sTaC4qeRBz",
        "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
        "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
        "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
        "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
    }
)


def _native_sol_out_amounts_lamports(wallet_address: str, native_transfers: list) -> list[int]:
    """Montants lamports de chaque transfert natif sortant du wallet (hors tips Jito)."""
    out: list[int] = []
    for nt in native_transfers:
        if (
            nt.get("fromUserAccount") == wallet_address
            and nt.get("toUserAccount") != wallet_address
            and nt.get("toUserAccount") not in JITO_TIP_ACCOUNTS
        ):
            try:
                a = int(nt.get("amount", 0) or 0)
            except (TypeError, ValueError):
                continue
            if a > 0:
                out.append(a)
    return out


def _estimate_swap_sol_spent_lamports(amounts: list[int]) -> int:
    """
    Helius agrège parfois plusieurs gros envois SOL dans la même signature (swap + autre instruction).
    Sommer tout surestime le coût → prix d'achat / token trop haut. On garde souvent le plus gros leg
    quand il domine clairement ; sinon on garde la somme (vrais swaps multi-leg).
    """
    if not amounts:
        return 0
    if len(amounts) == 1:
        return amounts[0]
    sorted_a = sorted(amounts, reverse=True)
    total = sum(sorted_a)
    mx = sorted_a[0]
    second = sorted_a[1]
    TIP_LAMPORTS = 1_500_000  # ~0.0015 SOL — tips typiques
    RENT_CEILING = 5_000_000  # ~0.005 SOL — création ATA / petites réserves
    non_tiny = [x for x in sorted_a if x >= TIP_LAMPORTS]
    if len(non_tiny) == 1:
        return non_tiny[0]
    if len(non_tiny) >= 2 and non_tiny[1] <= RENT_CEILING:
        rest = total - non_tiny[1]
        if rest > 0 and non_tiny[0] >= int(rest * 0.92):
            return non_tiny[0]
    if second > 0 and mx >= int(second * 1.45) and mx >= int(total * 0.52):
        return mx
    return total


def _estimate_swap_wsol_spent_sol(amounts_sol: list[float]) -> float:
    """Même idée que les envois natifs : plusieurs lignes wSOL sortantes → ne pas tout sommer aveuglément."""
    if not amounts_sol:
        return 0.0
    lam: list[int] = []
    for x in amounts_sol:
        try:
            v = float(x)
        except (TypeError, ValueError):
            continue
        if v <= 0:
            continue
        lam.append(max(1, int(round(v * LAMPORTS_PER_SOL))))
    if not lam:
        return 0.0
    return _estimate_swap_sol_spent_lamports(lam) / LAMPORTS_PER_SOL


def _helius_merge_token_transfers_by_mint(transfers: list) -> list:
    """
    Helius peut éclater le même mint en plusieurs lignes ; sans fusion, sol_spent est divisé par n
    (même effet visuel qu’un swap à moitié prix).
    """
    if len(transfers) <= 1:
        return transfers
    sums: dict[str, float] = defaultdict(float)
    first_row: dict[str, dict] = {}
    order: list[str] = []
    for t in transfers:
        m = (t.get("mint") or "").strip()
        if not m:
            continue
        sums[m] += float(t.get("tokenAmount", 0) or 0)
        if m not in first_row:
            first_row[m] = t
            order.append(m)
    out: list = []
    for m in order:
        row = dict(first_row[m])
        row["mint"] = m
        row["tokenAmount"] = sums[m]
        out.append(row)
    return out


def _row_get(row, key: str, default=None):
    """Lecture d'une colonne sqlite3.Row (pas de méthode .get())."""
    try:
        v = row[key]
        return default if v is None else v
    except (KeyError, IndexError, TypeError):
        return default


# Nombre de tx récentes Helius à comparer (évite de croire « à jour » si seule la 1ère sig matche)
HELIUS_RECENT_TX_WINDOW = 30


def _tx_signature_recorded_in_db(conn: sqlite3.Connection, signature: str) -> bool:
    """
    True si la signature est déjà prise en compte (suivi import ou présente en achats/ventes).
    Les ventes n'étaient pas toujours dans imported_tx → sans ce check, needs-import restait faux
    alors que des swaps récents manquaient encore.
    """
    if not signature or not str(signature).strip():
        return True
    sig = str(signature).strip()
    c = conn.cursor()
    for sql, params in (
        ("SELECT 1 FROM imported_tx WHERE signature = ? LIMIT 1", (sig,)),
        ("SELECT 1 FROM purchases WHERE transaction_signature = ? LIMIT 1", (sig,)),
        ("SELECT 1 FROM sales WHERE transaction_signature = ? LIMIT 1", (sig,)),
    ):
        if c.execute(sql, params).fetchone():
            return True
    return False


def _lot_eligible_for_sale(lot_ts_floor: int, lot_slot: int, sale_ts_ceiling: int, sale_slot: int) -> bool:
    """
    Un lot d'achat est éligible pour une vente si l'achat (borne basse) est avant ou au même moment
    que la vente (borne haute), sur (ts, slot). Borne basse/haute tiennent compte des dates TEXT
    quand les timestamps unix manquent (évite d’attribuer une vente à des lots « futurs » ou hors fil).
    """
    stc = int(sale_ts_ceiling or 0)
    if stc <= 0:
        return True
    ltf = int(lot_ts_floor or 0)
    if ltf <= 0:
        return True  # lots manuels / sans date exploitable
    lot_slot = lot_slot or 0
    sale_slot = sale_slot or 0
    return (ltf, lot_slot) <= (stc, sale_slot)


def _wallet_hifo_fingerprint(cursor, wallet: str) -> str:
    """Empreinte des achats/ventes du wallet — si elle change, le cache HIFO est invalide."""
    cursor.execute(
        """
        SELECT
          COALESCE((SELECT COUNT(*) FROM purchases WHERE wallet_address = ?), 0),
          COALESCE((SELECT COUNT(*) FROM sales s JOIN tokens t ON s.token_id = t.id WHERE t.wallet_address = ?), 0),
          COALESCE((SELECT MAX(p.id) FROM purchases p WHERE p.wallet_address = ?), 0),
          COALESCE((SELECT MAX(s.id) FROM sales s JOIN tokens t ON s.token_id = t.id WHERE t.wallet_address = ?), 0),
          COALESCE((SELECT SUM(p.sol_spent) FROM purchases p WHERE p.wallet_address = ?), 0),
          COALESCE((SELECT SUM(s.sol_received) FROM sales s JOIN tokens t ON s.token_id = t.id WHERE t.wallet_address = ?), 0)
        """,
        (wallet, wallet, wallet, wallet, wallet, wallet),
    )
    r = cursor.fetchone()
    return ":".join(str(x if x is not None else 0) for x in r)


def _invalidate_wallet_hifo_cache(conn, wallet_address: str):
    """Invalide les PnL HIFO stockés pour ce wallet (nouvel import, vente manuelle, etc.)."""
    if not wallet_address or not str(wallet_address).strip():
        return
    w = str(wallet_address).strip()
    c = conn.cursor()
    c.execute("DELETE FROM wallet_hifo_cache WHERE wallet_address = ?", (w,))
    c.execute(
        """
        UPDATE sales SET hifo_buy_cost_usd = NULL, hifo_pnl_usd = NULL
        WHERE token_id IN (SELECT id FROM tokens WHERE wallet_address = ?)
        """,
        (w,),
    )
    conn.commit()


def _hifo_gain_per_sale_and_lots(cursor, wallet: str, sol_usd: float) -> tuple[dict, dict]:
    """
    Simule toutes les ventes HIFO et retourne (gain_per_sale, lots_by_token) avec lots restants mis à jour.
    lots_by_token : token_id -> list de lots { remaining, tokens_total, sol_spent, sol_rate_buy, ... }.
    """
    from collections import defaultdict

    wallet_filter = "AND t.wallet_address = ?"
    params = (wallet,)

    cursor.execute(
        f"""
            SELECT s.id as sale_id, t.id as token_id,
                   s.tokens_sold as token_amount,
                   s.sol_received as sol_amount, s.sol_usd_at_sale,
                   COALESCE(s.sale_timestamp, 0) as sale_ts,
                   COALESCE(s.sale_slot, 0) as sale_slot,
                   s.sale_date as sale_date
            FROM sales s
            JOIN tokens t ON s.token_id = t.id
            WHERE 1=1 {wallet_filter}
            ORDER BY s.sale_date DESC
        """,
        params,
    )
    sells_raw = [dict(r) for r in cursor.fetchall()]

    cursor.execute(
        f"""
                SELECT p.token_id, p.purchase_timestamp, COALESCE(p.purchase_slot, 0) as purchase_slot,
                       p.tokens_bought, p.sol_spent,
                       COALESCE(NULLIF(p.sol_usd_at_buy, 0), ?) as sol_rate_buy,
                       p.purchase_date as purchase_date,
                       'helius' as source
                FROM purchases p
                INNER JOIN tokens t ON p.token_id = t.id
                WHERE p.tokens_bought > 0 AND p.sol_spent > 0
                  AND t.wallet_address = ?
                
                UNION ALL
                
                SELECT t.id as token_id, 0 as purchase_timestamp, 0 as purchase_slot,
                       t.purchased_tokens as tokens_bought,
                       t.invested_amount as sol_spent,
                       COALESCE(NULLIF(t.sol_usd_at_buy, 0), ?) as sol_rate_buy,
                       t.purchase_date as purchase_date,
                       'manual' as source
                FROM tokens t
                WHERE t.purchased_tokens > 0 
                  AND t.invested_amount > 0
                  AND t.id NOT IN (SELECT DISTINCT token_id FROM purchases)
                  {wallet_filter}
            """,
        (sol_usd, wallet, sol_usd, wallet),
    )

    lots_by_token: dict = defaultdict(list)
    raw_lots = [dict(p) for p in cursor.fetchall()]

    raw_lots.sort(
        key=lambda x: (
            x["token_id"],
            -(x["sol_spent"] / max(x["tokens_bought"], 0.001) * x["sol_rate_buy"]),
            _lot_ts_for_hifo(x.get("purchase_timestamp"), x.get("purchase_date")),
        )
    )

    for p in raw_lots:
        lots_by_token[p["token_id"]].append(
            {
                "ts": _lot_ts_for_hifo(p.get("purchase_timestamp"), p.get("purchase_date")),
                "slot": p.get("purchase_slot", 0) or 0,
                "remaining": p["tokens_bought"],
                "tokens_total": p["tokens_bought"],
                "sol_spent": p["sol_spent"],
                "sol_rate_buy": p["sol_rate_buy"],
                "price_usd": (p["sol_spent"] / p["tokens_bought"]) * p["sol_rate_buy"] if p["tokens_bought"] else 0,
            }
        )

    sells_chrono = sorted(sells_raw, key=_sale_sort_key_for_hifo)

    cursor.execute(
        """
        SELECT id, invested_amount, purchased_tokens, sol_usd_at_buy
        FROM tokens
        WHERE purchased_tokens > 0 AND invested_amount > 0
          AND id NOT IN (SELECT DISTINCT token_id FROM purchases)
          AND wallet_address = ?
        """,
        (wallet,),
    )
    for tok in cursor.fetchall():
        token_id = tok["id"]
        if token_id not in lots_by_token or len(lots_by_token[token_id]) == 0:
            lots_by_token[token_id].append(
                {
                    "ts": 0,
                    "slot": 0,
                    "remaining": tok["purchased_tokens"],
                    "tokens_total": tok["purchased_tokens"],
                    "sol_spent": tok["invested_amount"],
                    "sol_rate_buy": tok["sol_usd_at_buy"] or sol_usd,
                    "price_usd": (tok["invested_amount"] / tok["purchased_tokens"]) * (tok["sol_usd_at_buy"] or sol_usd)
                    if tok["purchased_tokens"]
                    else 0,
                }
            )

    token_ids_sold = list({s["token_id"] for s in sells_raw})
    token_fallback = {}
    if token_ids_sold:
        ph = ",".join("?" * len(token_ids_sold))
        cursor.execute(
            f"SELECT id, invested_amount, purchased_tokens, sol_usd_at_buy FROM tokens WHERE id IN ({ph})",
            token_ids_sold,
        )
        token_fallback = {r["id"]: dict(r) for r in cursor.fetchall()}

    gain_per_sale: dict = {}
    for sale in sells_chrono:
        token_id = sale["token_id"]
        tokens_sold = sale["token_amount"] or 0
        sale_ts_c = _sale_ts_ceiling_for_hifo(sale.get("sale_ts"), sale.get("sale_date"))
        sale_slot = sale.get("sale_slot", 0) or 0
        sol_rate_s = sale.get("sol_usd_at_sale") or sol_usd
        sell_usd = (sale["sol_amount"] or 0) * sol_rate_s

        token_lots = lots_by_token.get(token_id, [])
        eligible = sorted(
            [l for l in token_lots if _lot_eligible_for_sale(l["ts"], l.get("slot", 0), sale_ts_c, sale_slot)],
            key=lambda l: l["price_usd"],
            reverse=True,
        )
        buy_usd_cost = 0.0
        tokens_left = tokens_sold
        for lot in eligible:
            if tokens_left <= 0:
                break
            consume = min(lot["remaining"], tokens_left)
            ratio = consume / lot["tokens_total"] if lot["tokens_total"] else 0
            buy_usd_cost += lot["sol_spent"] * ratio * lot["sol_rate_buy"]
            lot["remaining"] -= consume
            tokens_left -= consume

        if buy_usd_cost <= 0 and tokens_sold > 0:
            tok = token_fallback.get(token_id)
            if tok and (tok.get("purchased_tokens") or 0) > 0:
                inv = tok.get("invested_amount") or 0
                rate = tok.get("sol_usd_at_buy") or sol_usd
                if inv > 0 and rate > 0:
                    buy_usd_cost = tokens_sold * (inv * rate / tok["purchased_tokens"])

        if buy_usd_cost <= 0 and tokens_sold > 0:
            profit = None
        else:
            profit = sell_usd - buy_usd_cost
        gain_per_sale[sale["sale_id"]] = {
            "sell_usd": round(sell_usd, 4),
            "buy_usd": round(buy_usd_cost, 4),
            "pnl_usd": round(profit, 4) if profit is not None else None,
        }

    return gain_per_sale, lots_by_token


def _compute_hifo_gain_per_sale(cursor, wallet: str, sol_usd: float) -> dict:
    """Même logique que /api/all-transactions (skip_hifo=0)."""
    g, _lots = _hifo_gain_per_sale_and_lots(cursor, wallet, sol_usd)
    return g


def _hifo_remaining_cost_usd(lots_by_token: dict, token_id: int) -> float:
    s = 0.0
    for l in lots_by_token.get(token_id, []) or []:
        rem = float(l.get("remaining") or 0)
        if rem <= 0:
            continue
        tot = float(l.get("tokens_total") or 0)
        if tot <= 0:
            continue
        s += (rem / tot) * float(l.get("sol_spent") or 0) * float(l.get("sol_rate_buy") or 0)
    return s


def _hifo_realized_gain_loss_from_persisted_sales(cursor, wallet: str) -> tuple[float, float] | None:
    """
    Gains / pertes **figés** : somme des sales.hifo_pnl_usd (écrits au recalcul HIFO).
    Ne dépend pas du cours SOL actuel. Retourne None si la colonne n'existe pas ou si au moins
    une vente du wallet n'a pas encore de hifo_pnl_usd — dans ce cas le dashboard retombe sur
    le calcul live (sensible au SOL spot pour les taux manquants).
    """
    w = (wallet or "").strip()
    if not w:
        return (0.0, 0.0)
    try:
        cursor.execute(
            """
            SELECT s.hifo_pnl_usd AS pnl
            FROM sales s
            JOIN tokens t ON s.token_id = t.id
            WHERE t.wallet_address = ?
            """,
            (w,),
        )
    except sqlite3.OperationalError:
        return None
    rows = cursor.fetchall()
    if not rows:
        return (0.0, 0.0)
    rg = rl = 0.0
    for r in rows:
        pnl = r["pnl"]
        if pnl is None:
            return None
        pnl = float(pnl)
        if pnl > 0:
            rg += pnl
        elif pnl < 0:
            rl += abs(pnl)
    return (rg, rl)


def _hifo_dashboard_gain_loss_net(
    cursor, wallet: str, sol_usd: float
) -> tuple[float, float, float, float, float]:
    """
    total_gain / total_loss = P/L **latent** (varie avec cours / valorisation), aligné sur les cartes token :
    coût saisi (user_position_cost_usd) ou coût auto achats/ventes si dispo, sinon coût HIFO des lots.
    realized_* = P/L **figé** sur ventes (HIFO en base ou live).
    net_total = (ug + rg) − (ul + rl).
    Retourne (total_gain, total_loss, net_total, realized_gain_only, realized_loss_only).
    """
    gain_per_sale, lots_by_token = _hifo_gain_per_sale_and_lots(cursor, wallet, sol_usd)
    persisted_rg_rl = _hifo_realized_gain_loss_from_persisted_sales(cursor, wallet)
    if persisted_rg_rl is not None:
        rg, rl = persisted_rg_rl
    else:
        rg = rl = 0.0
        for d in gain_per_sale.values():
            pnl = d.get("pnl_usd")
            if pnl is None:
                continue
            if pnl > 0:
                rg += pnl
            else:
                rl += abs(pnl)

    wf = "AND wallet_address = :wallet"
    cursor.execute(
        f"""
        SELECT id, current_tokens, current_value, current_price, address, user_position_cost_usd
        FROM tokens
        WHERE 1=1 {wf} AND {_token_non_wsol_clause('tokens')}
        """,
        {"wallet": wallet, "wsol_mint": SOL_MINT},
    )
    rows = list(cursor.fetchall())
    tids = [int(dict(r)["id"]) for r in rows]
    try:
        auto_cost_map, auto_pos_map = _remaining_avg_cost_and_pos_by_token_ids(cursor, tids, sol_usd)
    except Exception as e:
        print(f"[!] dashboard auto position cost: {e}")
        auto_cost_map, auto_pos_map = {}, {}
    ug = ul = 0.0
    for row in rows:
        r = dict(row)
        tid = int(r["id"])
        ct = float(r["current_tokens"] or 0)
        if ct <= 1e-12:
            continue
        cv = float(r["current_value"] or 0)
        cp = float(r["current_price"] or 0)
        market = cv if cv > 0 else ct * cp
        display_cost = _display_position_cost_usd_total(
            tid,
            ct,
            r.get("address"),
            r.get("user_position_cost_usd"),
            auto_cost_map,
            auto_pos_map,
        )
        if display_cost is not None:
            ux = market - display_cost
        else:
            rem_cost = _hifo_remaining_cost_usd(lots_by_token, tid)
            sum_lot_rem = sum(
                float(l.get("remaining") or 0) for l in (lots_by_token.get(tid) or [])
            )
            if sum_lot_rem > 1e-12 and abs(sum_lot_rem - ct) > max(1e-9, 1e-6 * max(ct, 1.0)):
                rem_cost *= ct / sum_lot_rem
            ux = market - rem_cost
        if ux > 0:
            ug += ux
        else:
            ul += abs(ux)

    total_gain = ug
    total_loss = ul
    net_total = (ug + rg) - (ul + rl)
    return total_gain, total_loss, net_total, rg, rl


def _hifo_per_token_gain_loss_and_open_avg(
    cursor, wallet: str, sol_usd: float
) -> tuple[dict[int, dict[str, float]], dict[int, float]]:
    """
    P/L latent par token + prix moyen USD/token des **lots encore détenus** (même coût HIFO que le P/L latent).
    Le second dict sert d’affichage « prix d’achat » cohérent après vente totale puis rachat (pas le VWAP de tout l’historique).
    """
    _gain_per_sale, lots_by_token = _hifo_gain_per_sale_and_lots(cursor, wallet, sol_usd)

    cursor.execute(
        f"""
        SELECT id, current_tokens, current_value, current_price
        FROM tokens
        WHERE wallet_address = :wallet AND {_token_non_wsol_clause('tokens')}
        """,
        {"wallet": wallet, "wsol_mint": SOL_MINT},
    )
    out: dict[int, dict[str, float]] = {}
    open_avg_usd: dict[int, float] = {}
    for row in cursor.fetchall():
        tid = int(row["id"])
        ct = float(row["current_tokens"] or 0)
        latent_pnl_pct: float | None
        if ct > 1e-12:
            cv = float(row["current_value"] or 0)
            cp = float(row["current_price"] or 0)
            market = cv if cv > 0 else ct * cp
            rem_cost = _hifo_remaining_cost_usd(lots_by_token, tid)
            sum_lot_rem = sum(
                float(l.get("remaining") or 0) for l in (lots_by_token.get(tid) or [])
            )
            if sum_lot_rem > 1e-12 and abs(sum_lot_rem - ct) > max(1e-9, 1e-6 * max(ct, 1.0)):
                rem_cost *= ct / sum_lot_rem
            ux = market - rem_cost
            latent_pnl_pct = (100.0 * ux / rem_cost) if rem_cost > 1e-9 else None
            if rem_cost > 1e-9:
                open_avg_usd[tid] = rem_cost / ct
        else:
            ux = 0.0
            latent_pnl_pct = None
        ug = max(0.0, ux)
        ul = abs(min(0.0, ux))
        out[tid] = {"gain": ug, "loss": ul, "net": ug - ul, "latent_pnl_pct": latent_pnl_pct}
    return out, open_avg_usd


def _parsed_manual_user_position_cost_usd(raw) -> float | None:
    if raw is None:
        return None
    try:
        v = float(raw)
        if math.isfinite(v) and v > 1e-9:
            return v
    except (TypeError, ValueError):
        pass
    return None


def _display_position_cost_usd_total(
    tid: int,
    ct: float,
    address: str | None,
    user_position_cost_raw,
    auto_cost_by_tid: dict[int, float],
    auto_pos_by_tid: dict[int, float],
) -> float | None:
    """
    Coût total USD pour le P/L latent « affiché » (saisie manuelle ou auto achats/ventes).
    None → utiliser le coût HIFO (lots restants) à la place.
    """
    if ct <= 1e-12:
        return None
    if str(address or "") == str(SOL_MINT):
        return None
    mv = _parsed_manual_user_position_cost_usd(user_position_cost_raw)
    if mv is not None:
        return mv
    ac = auto_cost_by_tid.get(tid)
    if ac is None or ac <= 1e-9:
        return None
    sp = auto_pos_by_tid.get(tid)
    ucost = float(ac)
    if sp is not None and sp > 1e-12 and abs(sp - ct) > max(1e-9, 1e-6 * max(sp, ct)):
        if sp > ct:
            ucost *= ct / sp
    return ucost


def _apply_display_position_cost_usd(t: dict, ucost: float) -> None:
    """Met à jour prix d'achat USD/token, gain/loss et % latent à partir d'un coût total USD de position."""
    ct = float(t.get("current_tokens") or 0)
    if ct <= 1e-12 or not math.isfinite(ucost) or ucost <= 1e-9:
        return
    cv = float(t.get("current_value") or 0)
    cp = float(t.get("current_price") or 0)
    market = cv if cv > 0 else ct * cp
    ux = market - ucost
    t["purchase_price_usd"] = ucost / ct
    t["latent_pnl_pct"] = round((100.0 * ux / ucost), 4) if ucost > 1e-9 else None
    t["gain"] = max(0.0, ux)
    t["loss"] = abs(min(0.0, ux))


def _remaining_avg_cost_and_pos_by_token_ids(
    cursor, token_ids: list[int], sol_usd_fallback: float
) -> tuple[dict[int, float], dict[int, float]]:
    """
    (coût USD restant, position token simulée) par token_id : uniquement les événements **après
    la dernière fois** où la position simulée est tombée à zéro, puis coût moyen résiduel
    (réduction proportionnelle à chaque vente). Évite d’empiler d’anciens achats quand tu as tout
    vendu puis racheté. Si la position n’a jamais été à zéro dans les données, on garde tout l’historique.
    """
    if not token_ids:
        return {}, {}
    ids = sorted({int(x) for x in token_ids})
    ph = ",".join("?" * len(ids))
    cursor.execute(
        f"""
        SELECT token_id, tokens_bought, sol_spent,
               COALESCE(NULLIF(sol_usd_at_buy, 0), ?) AS rate,
               COALESCE(purchase_timestamp, 0) AS ts, id
        FROM purchases
        WHERE token_id IN ({ph}) AND tokens_bought > 0 AND sol_spent > 0
        ORDER BY token_id, ts ASC, id ASC
        """,
        (sol_usd_fallback, *ids),
    )
    by_tid: dict[int, list[tuple]] = defaultdict(list)
    for r in cursor.fetchall():
        d = dict(r)
        tid = int(d["token_id"])
        rate = float(d["rate"] or sol_usd_fallback)
        usd = float(d["sol_spent"] or 0) * rate
        qty = float(d["tokens_bought"] or 0)
        ts = int(d["ts"] or 0)
        rid = int(d["id"])
        by_tid[tid].append((ts, 0, "buy", qty, usd, rid))

    cursor.execute(
        f"""
        SELECT token_id, tokens_sold, COALESCE(sale_timestamp, 0) AS ts, id
        FROM sales
        WHERE token_id IN ({ph})
        ORDER BY token_id, ts ASC, id ASC
        """,
        ids,
    )
    for r in cursor.fetchall():
        d = dict(r)
        tid = int(d["token_id"])
        qty = float(d["tokens_sold"] or 0)
        ts = int(d["ts"] or 0)
        rid = int(d["id"])
        by_tid[tid].append((ts, 1, "sell", qty, 0.0, rid))

    out_cost: dict[int, float] = {}
    out_pos: dict[int, float] = {}
    for tid in ids:
        events = sorted(by_tid.get(tid, []), key=lambda e: (e[0], e[1], e[5]))
        run_start = 0
        pos_scan = 0.0
        for i, ev in enumerate(events):
            _ts, _ord, kind, qty, usd, _rid = ev
            if kind == "buy":
                pos_scan += qty
            else:
                s = min(qty, pos_scan)
                pos_scan -= s
                if pos_scan < 1e-12:
                    pos_scan = 0.0
                    run_start = i + 1
        tail = events[run_start:]
        pos = 0.0
        cost = 0.0
        for ev in tail:
            _ts, _ord, kind, qty, usd, _rid = ev
            if kind == "buy":
                pos += qty
                cost += usd
            else:
                s = min(qty, pos)
                if pos > 1e-12:
                    cost *= (pos - s) / pos
                pos -= s
                if pos < 1e-12:
                    pos = 0.0
                    cost = 0.0
        if pos > 1e-8 and cost > 1e-9:
            out_cost[tid] = cost
            out_pos[tid] = pos
    return out_cost, out_pos


def _overlay_position_cost_display(
    t: dict, auto_cost_by_tid: dict[int, float], auto_pos_by_tid: dict[int, float]
) -> None:
    """
    Carte token : 1) user_position_cost_usd saisi 2) sinon coût dérivé des lignes achats/ventes
    (moyenne restante) 3) sinon garder HIFO / VWAP déjà appliqués.
    """
    ct = float(t.get("current_tokens") or 0)
    if ct <= 1e-12:
        return
    tid = int(t["id"])
    ucost = _display_position_cost_usd_total(
        tid,
        ct,
        t.get("address"),
        t.get("user_position_cost_usd"),
        auto_cost_by_tid,
        auto_pos_by_tid,
    )
    if ucost is None:
        return
    t["position_cost_display_source"] = (
        "manual" if _parsed_manual_user_position_cost_usd(t.get("user_position_cost_usd")) is not None else "auto_txn"
    )
    _apply_display_position_cost_usd(t, ucost)


def _hifo_per_token_gain_loss_dict(cursor, wallet: str, sol_usd: float) -> dict[int, dict[str, float]]:
    """
    P/L **latent** uniquement (positions encore ouvertes) : valeur actuelle − coût HIFO des lots restants.
    Token entièrement vendu → gain/loss/net à 0 (l’historique des ventes est dans les transactions / détail HIFO).
    """
    hmap, _ = _hifo_per_token_gain_loss_and_open_avg(cursor, wallet, sol_usd)
    return hmap


def _sync_tokens_gain_loss_hifo_for_wallets(conn, sol_usd: float, wallet_addresses: list) -> None:
    """Écrit gain/loss HIFO en BDD pour chaque wallet listé (adresses uniques, non vides)."""
    seen = set()
    c = conn.cursor()
    for w in wallet_addresses:
        w = (w or "").strip()
        if len(w) < 20 or w in seen:
            continue
        seen.add(w)
        try:
            hmap = _hifo_per_token_gain_loss_dict(c, w, sol_usd)
            for tid, h in hmap.items():
                c.execute(
                    "UPDATE tokens SET gain=?, loss=? WHERE id=? AND wallet_address=?",
                    (h["gain"], h["loss"], tid, w),
                )
        except Exception as e:
            print(f"[!] sync HIFO gain/loss tokens wallet {w[:10]}…: {e}")
    conn.commit()


def _persist_wallet_hifo(conn, wallet: str, sol_usd: float):
    """Calcule le HIFO (aligné sur all-transactions), écrit chaque vente + ligne agrégée wallet."""
    if not wallet or not str(wallet).strip():
        return
    w = str(wallet).strip()
    c = conn.cursor()
    gain_per_sale = _compute_hifo_gain_per_sale(c, w, sol_usd)
    realized_gain = 0.0
    realized_loss = 0.0
    for sid, d in gain_per_sale.items():
        pnl = d.get("pnl_usd")
        buy = d.get("buy_usd")
        if pnl is not None:
            if pnl > 0:
                realized_gain += pnl
            else:
                realized_loss += abs(pnl)
        c.execute(
            "UPDATE sales SET hifo_buy_cost_usd = ?, hifo_pnl_usd = ? WHERE id = ?",
            (buy, pnl, sid),
        )
    fp = _wallet_hifo_fingerprint(c, w)
    c.execute(
        """
        INSERT INTO wallet_hifo_cache (wallet_address, realized_gain, realized_loss, fingerprint, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(wallet_address) DO UPDATE SET
            realized_gain = excluded.realized_gain,
            realized_loss = excluded.realized_loss,
            fingerprint = excluded.fingerprint,
            updated_at = CURRENT_TIMESTAMP
        """,
        (w, realized_gain, realized_loss, fp),
    )


# === GLOBAL CACHE ===
_sol_price_cache = {"price": None, "timestamp": 0, "ttl": 1800, "last_valid_price": 180.0}  # Cache 30min + fallback
_sol_price_lock = asyncio.Lock()

# Cache Dashboard : TTL 60s (très stable, positions changent lentement, prix SOL aussi)
_dashboard_cache = {}  # Format: {wallet_key: {"data": Dashboard, "timestamp": float}}
_dashboard_cache_lock = asyncio.Lock()

# Cache prix batch : TTL 15s (évite refetch si double-clic Actualiser)
_price_batch_cache: dict = {}  # {frozenset(addrs): {"prices": dict, "ts": float}}
_price_batch_cache_ttl = 20  # court : évite de garder des prix à 0 après timeouts API

# Cache prix par token : TTL 60s
_price_cache: dict = {}  # {token_address: {"price": float, "timestamp": float}}
_price_cache_lock = asyncio.Lock()

# Rate-limit auth (mémoire processus — multi-instance = somme des limites)
_auth_strict_buckets: defaultdict[str, list[float]] = defaultdict(list)
_auth_loose_buckets: defaultdict[str, list[float]] = defaultdict(list)
_AUTH_STRICT_MAX = 8
_AUTH_LOOSE_MAX = 40
_AUTH_RL_SEC = 60

# Toutes les routes /api/ sauf health (anti-abus basique)
_api_rl_buckets: defaultdict[str, list[float]] = defaultdict(list)
_API_RL_MAX = 400
_API_RL_SEC = 60


def _client_ip(request: Request) -> str:
    xf = request.headers.get("x-forwarded-for")
    if xf:
        return xf.split(",")[0].strip()[:45]
    if request.client:
        return request.client.host or "unknown"
    return "unknown"


class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method != "POST":
            return await call_next(request)
        path = request.url.path
        if not path.startswith("/api/auth/"):
            return await call_next(request)
        ip = _client_ip(request)
        now = time.time()
        if path in ("/api/auth/login", "/api/auth/register"):
            b = _auth_strict_buckets[ip]
            b[:] = [t for t in b if now - t < _AUTH_RL_SEC]
            if len(b) >= _AUTH_STRICT_MAX:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Trop de tentatives. Réessayez dans une minute."},
                )
            b.append(now)
        else:
            lb = _auth_loose_buckets[ip]
            lb[:] = [t for t in lb if now - t < _AUTH_RL_SEC]
            if len(lb) >= _AUTH_LOOSE_MAX:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Trop de requêtes auth. Réessayez dans une minute."},
                )
            lb.append(now)
        return await call_next(request)


class ApiRateLimitMiddleware(BaseHTTPMiddleware):
    """Limite globale /api/ par IP (OPTIONS et /api/health exclus)."""

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)
        path = request.url.path
        if path.startswith("/api/") and path != "/api/health":
            ip = _client_ip(request)
            now = time.time()
            b = _api_rl_buckets[ip]
            b[:] = [t for t in b if now - t < _API_RL_SEC]
            if len(b) >= _API_RL_MAX:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Trop de requêtes. Pause courte puis réessayez."},
                )
            b.append(now)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if "server" in response.headers:
            del response.headers["server"]
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "accelerometer=(), camera=(), geolocation=(), microphone=(), payment=(), usb=()",
        )
        proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").lower()
        if IS_PROD and proto == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        if IS_PROD:
            response.headers.setdefault(
                "Content-Security-Policy",
                (
                    "default-src 'self'; "
                    "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
                    "https://cdn.tailwindcss.com https://cdn.jsdelivr.net https://unpkg.com; "
                    "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
                    "img-src 'self' data: https: blob:; "
                    "font-src 'self' https://cdnjs.cloudflare.com data:; "
                    "connect-src 'self' https: wss:; "
                    "frame-ancestors 'none'; "
                    "base-uri 'self'; "
                    "form-action 'self'"
                ),
            )
        return response


app = FastAPI(
    title="Meme Coin Tracker API",
    docs_url=None if IS_PROD else "/docs",
    redoc_url=None if IS_PROD else "/redoc",
    openapi_url=None if IS_PROD else "/openapi.json",
)

# GZip — compression des réponses API (réduit la bande passante)
app.add_middleware(GZipMiddleware, minimum_size=500)

# CORS — origines explicites ; Render : RENDER_EXTERNAL_URL ajouté dans config.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=CORS_ALLOW_METHODS,
    allow_headers=CORS_ALLOW_HEADERS,
)

app.add_middleware(AuthRateLimitMiddleware)
app.add_middleware(ApiRateLimitMiddleware)
if USE_TRUSTED_HOST:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=TRUSTED_HOSTS)
# En dernier = enveloppe la plus externe : en-têtes sur toutes les réponses (dont 429)
app.add_middleware(SecurityHeadersMiddleware)


def _check_api_key(request: Request) -> bool:
    """Vérifie X-API-Key si REQUIRE_API_KEY est activé."""
    if not REQUIRE_API_KEY:
        return True
    key = request.headers.get("X-API-Key", "")
    return key == API_KEY


def _api_path_requires_service_api_key(path: str) -> bool:
    """True si la route /api/* exige X-API-Key quand API_KEY est défini en prod."""
    if not path.startswith("/api/"):
        return False
    # Health check Render : pas de clé (sinon le service reste « unhealthy »).
    if path.rstrip("/") == "/api/health":
        return False
    return True


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    if REQUIRE_API_KEY and _api_path_requires_service_api_key(request.url.path):
        if not _check_api_key(request):
            return JSONResponse(status_code=401, content={"detail": "API key manquante ou invalide"})
    return await call_next(request)

# === MODELS ===
class Token(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    name: str
    address: str
    detection_date: Optional[str] = None
    comments: Optional[str] = None
    event: Optional[str] = None
    mcap_target: Optional[str] = None
    purchase_date: Optional[str] = None
    current_tokens: Optional[float] = 0
    purchased_tokens: Optional[float] = 0
    purchase_price: Optional[float] = 0
    current_price: Optional[float] = 0
    loss: Optional[float] = 0
    gain: Optional[float] = 0
    # % du P/L latent vs coût HIFO des lots restants (null si coût ~0 ou indispo)
    latent_pnl_pct: Optional[float] = None
    current_value: Optional[float] = 0
    invested_amount: Optional[float] = 0
    sold_tokens: Optional[float] = 0
    sold_date: Optional[str] = None
    sold_count: Optional[float] = 0
    sold_price: Optional[float] = 0
    sold_amount: Optional[float] = 0
    price_is_stale: Optional[bool] = False
    price_warning: Optional[str] = None
    # Coût total USD que tu attribues à la position ouverte (optionnel) — carte token uniquement
    user_position_cost_usd: Optional[float] = None

class Sale(BaseModel):
    token_id: int
    sale_date: str
    tokens_sold: float
    sale_price: float
    sale_amount: float

class Dashboard(BaseModel):
    total_risked: float
    current_amount: float
    withdrawn_amount: float
    # current_amount + withdrawn_amount - total_risked
    # total_risked = montant saisi manuellement (wallet_reference_capital) ; 0 si non défini.
    # net_total (P/L combiné) peut différer du flux si le total dépensé saisi ne reflète pas les achats réels.
    flow_net_usd: float
    # total_gain / total_loss = latent seulement ; net_total = latent + (realized_gain − realized_loss).
    total_gain: float
    total_loss: float
    net_total: float
    realized_gain: float
    realized_loss: float
    # True si realized_* / coûts par vente non calculés (requête avec skip_hifo)
    hifo_pending: bool = False
    last_sale_token: Optional[str] = None
    last_sale_amount_sol: Optional[float] = None
    last_sale_date: Optional[str] = None
    sol_price_usd: Optional[float] = None
    wallet_sol_balance: Optional[float] = None  # Solde SOL on-chain (évite 1 appel API séparé)
    # reference_capital_usd = total dépensé manuel ; transfer_* restent à 0 (plus d’agrégat Helius sur le dashboard).
    tracked_purchases_usd: float = 0.0
    reference_capital_usd: float = 0.0
    # Réservés (non remplis sur le dashboard ; plus utilisés comme base du total dépensé).
    transfer_sol_recu: float = 0.0
    transfer_sol_envoye: float = 0.0
    transfer_flow_net_usd: float = 0.0
    transfer_basis_usd: float = 0.0
    # "manual" | "unset"
    total_risked_source: str = "unset"


class ReferenceCapitalBody(BaseModel):
    wallet_address: str
    amount_usd: float


class ReferenceCapitalAddBody(BaseModel):
    wallet_address: str
    add_usd: float


class DatabaseWipeBody(BaseModel):
    """Corps attendu pour POST /api/database/wipe-all-data — phrase exacte obligatoire."""

    confirm: str


class AuthRegisterBody(BaseModel):
    username: str
    password: str


class AuthLoginBody(BaseModel):
    username: str
    password: str


class AuthSavedWalletBody(BaseModel):
    address: str
    label: Optional[str] = None
    follows: Optional[bool] = None  # défaut True à l’insert si omis


class AuthPatchWalletBody(BaseModel):
    address: str
    label: Optional[str] = None
    follows: Optional[bool] = None


class AuthWalletSyncDoneBody(BaseModel):
    address: str


class AuthSetActiveBody(BaseModel):
    address: str


def _parse_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    t = parts[1].strip()
    return t or None


def _require_auth_user(authorization: Optional[str] = Header(None)) -> dict:
    token = _parse_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Non connecté")
    with get_db() as conn:
        user = auth_service.get_user_by_token(conn, token)
    if not user:
        raise HTTPException(status_code=401, detail="Session expirée ou invalide")
    return user


# === DATABASE ===
_backend_dir_for_db = os.path.dirname(os.path.abspath(__file__))
_project_root_for_db = os.path.dirname(_backend_dir_for_db)
_default_sqlite_db = os.path.join(_project_root_for_db, "data", "meme_coins.db")
DB_PATH = os.path.expandvars(os.path.expanduser(os.getenv("SQLITE_DB_PATH", _default_sqlite_db)))
_sqlite_dir = os.path.dirname(os.path.abspath(DB_PATH))
if _sqlite_dir:
    os.makedirs(_sqlite_dir, exist_ok=True)

@contextmanager
def get_db():
    if USE_POSTGRES:
        with get_pg_connection() as conn:
            yield conn
        return
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")   # équilibré perf / durabilité
    # cache_size négatif = kilo-octets (≈ 64 Mo) ; mmap = lecture quasi instantanée des pages chaudes
    conn.execute("PRAGMA cache_size=-64000")
    conn.execute("PRAGMA mmap_size=134217728")  # 128 Mo
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def _bool_to_sql_int(v: Optional[bool]) -> int:
    """Colonne price_is_stale : INTEGER côté Postgres ; bool Python → 0/1 (SQLite accepte aussi)."""
    return 1 if v else 0


def _purchase_vwap_usd_by_token(cursor, wallet: str, sol_fallback: float) -> dict[int, float]:
    """Coût moyen USD/token depuis les lots purchases (aligné Dex / explorer si sol_spent est bon)."""
    w = (wallet or "").strip()
    if len(w) < 20:
        return {}
    cursor.execute(
        """
        SELECT token_id,
            COALESCE(SUM(sol_spent * COALESCE(NULLIF(sol_usd_at_buy, 0), ?)), 0) AS usd,
            COALESCE(SUM(tokens_bought), 0) AS tb
        FROM purchases
        WHERE wallet_address = ? AND tokens_bought > 0 AND sol_spent > 0
        GROUP BY token_id
        """,
        (sol_fallback, w),
    )
    out: dict[int, float] = {}
    for r in cursor.fetchall():
        try:
            tid = int(r["token_id"])
        except (TypeError, ValueError):
            continue
        tb = float(r["tb"] or 0)
        if tb <= 0:
            continue
        usd = float(r["usd"] or 0)
        if usd > 0:
            out[tid] = usd / tb
    return out


def _purchase_vwap_usd_for_token_id(cursor, token_id: int, sol_fallback: float) -> Optional[float]:
    cursor.execute(
        """
        SELECT COALESCE(SUM(sol_spent * COALESCE(NULLIF(sol_usd_at_buy, 0), ?)), 0) AS usd,
               COALESCE(SUM(tokens_bought), 0) AS tb
        FROM purchases
        WHERE token_id = ? AND tokens_bought > 0 AND sol_spent > 0
        """,
        (sol_fallback, token_id),
    )
    r = cursor.fetchone()
    if not r:
        return None
    tb = float(r["tb"] or 0)
    if tb <= 0:
        return None
    usd = float(r["usd"] or 0)
    return (usd / tb) if usd > 0 else None


def wipe_all_database_data(conn: sqlite3.Connection) -> list[str]:
    """
    Supprime toutes les lignes de chaque table utilisateur (schéma et index inchangés).
    Réinitialise les compteurs AUTOINCREMENT (sqlite_sequence).
    """
    if USE_POSTGRES:
        c = conn.cursor()
        cleared = wipe_all_postgres_data(c)
        conn.commit()
        return cleared
    c = conn.cursor()
    c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    tables = [r[0] for r in c.fetchall()]
    c.execute("PRAGMA foreign_keys=OFF")
    cleared: list[str] = []
    for t in tables:
        if t == "sqlite_sequence":
            continue
        try:
            c.execute(f'DELETE FROM "{t}"')
            cleared.append(t)
        except sqlite3.OperationalError:
            pass
    try:
        c.execute("DELETE FROM sqlite_sequence")
    except sqlite3.OperationalError:
        pass
    c.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    return cleared


def init_db():
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Table des tokens
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                address TEXT NOT NULL,
                detection_date TEXT,
                comments TEXT,
                event TEXT,
                mcap_target TEXT,
                purchase_date TEXT,
                current_tokens REAL DEFAULT 0,
                purchased_tokens REAL DEFAULT 0,
                purchase_price REAL DEFAULT 0,
                current_price REAL DEFAULT 0,
                loss REAL DEFAULT 0,
                gain REAL DEFAULT 0,
                current_value REAL DEFAULT 0,
                invested_amount REAL DEFAULT 0,
                sold_tokens REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Table des ventes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id INTEGER NOT NULL,
                sale_date TEXT NOT NULL,
                tokens_sold REAL NOT NULL,
                sale_price REAL NOT NULL,
                sale_amount REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (token_id) REFERENCES tokens (id)
            )
        """)
        
        # Table des prix historiques
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id INTEGER NOT NULL,
                price REAL NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (token_id) REFERENCES tokens (id)
            )
        """)

        # Table des paramètres (clé/valeur)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # Table des achats individuels (historique complet)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id INTEGER NOT NULL,
                purchase_date TEXT NOT NULL,
                purchase_timestamp INTEGER,
                tokens_bought REAL NOT NULL,
                purchase_price REAL NOT NULL,
                sol_spent REAL NOT NULL,
                transaction_signature TEXT NOT NULL,
                sol_usd_at_buy REAL DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (token_id) REFERENCES tokens (id),
                UNIQUE(token_id, transaction_signature)
            )
        """)

        conn.commit()


def _migrate_tokens_wallet_scoped_unique(conn):
    """
    Ancien schéma : UNIQUE sur `address` → un seul enregistrement par mint dans toute la BDD ;
    l'import/sync d'un second wallet échouait pour USDC, Portal ETH, etc.
    Nouveau : UNIQUE(address, wallet_address) après normalisation de wallet_address.
    """
    c = conn.cursor()
    row = c.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tokens'"
    ).fetchone()
    if not row or not row[0]:
        return
    compact = "".join(row[0].split()).lower()
    if "unique(address,wallet_address)" in compact or "unique(wallet_address,address)" in compact:
        return

    try:
        c.execute("SELECT value FROM settings WHERE key = 'wallet_address'")
        wr = c.fetchone()
        default_w = (wr and wr["value"] and str(wr["value"]).strip()) or None
        if default_w:
            c.execute(
                "UPDATE tokens SET wallet_address = ? WHERE wallet_address IS NULL OR TRIM(COALESCE(wallet_address,'')) = ''",
                (default_w,),
            )
        c.execute("UPDATE tokens SET wallet_address = COALESCE(wallet_address, '')")
    except Exception:
        c.execute("UPDATE tokens SET wallet_address = COALESCE(wallet_address, '')")

    c.execute(
        """
        SELECT address, wallet_address, COUNT(*) AS n FROM tokens
        WHERE address IS NOT NULL AND address != ''
        GROUP BY address, wallet_address HAVING COUNT(*) > 1
        """
    )
    if c.fetchone():
        print("[!] tokens: doublons (address, wallet_address) — migration UNIQUE ignorée")
        return

    c.execute("PRAGMA foreign_keys=OFF")
    try:
        c.execute("DROP TABLE IF EXISTS tokens_new")
        c.execute(
            """
            CREATE TABLE tokens_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                address TEXT NOT NULL,
                detection_date TEXT,
                comments TEXT,
                event TEXT,
                mcap_target TEXT,
                purchase_date TEXT,
                current_tokens REAL DEFAULT 0,
                purchased_tokens REAL DEFAULT 0,
                purchase_price REAL DEFAULT 0,
                current_price REAL DEFAULT 0,
                loss REAL DEFAULT 0,
                gain REAL DEFAULT 0,
                current_value REAL DEFAULT 0,
                invested_amount REAL DEFAULT 0,
                sold_tokens REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                sol_usd_at_buy REAL DEFAULT NULL,
                wallet_address TEXT NOT NULL DEFAULT '',
                price_is_stale BOOLEAN DEFAULT 0,
                price_warning TEXT DEFAULT NULL,
                UNIQUE(address, wallet_address)
            )
            """
        )
        c.execute(
            """
            INSERT INTO tokens_new (
                id, name, address, detection_date, comments, event, mcap_target, purchase_date,
                current_tokens, purchased_tokens, purchase_price, current_price, loss, gain, current_value,
                invested_amount, sold_tokens, created_at, updated_at,
                sol_usd_at_buy, wallet_address, price_is_stale, price_warning
            )
            SELECT
                id, name, address, detection_date, comments, event, mcap_target, purchase_date,
                current_tokens, purchased_tokens, purchase_price, current_price, loss, gain, current_value,
                invested_amount, sold_tokens, created_at, updated_at,
                sol_usd_at_buy, COALESCE(wallet_address, ''), COALESCE(price_is_stale, 0), price_warning
            FROM tokens
            """
        )
        c.execute("DROP TABLE tokens")
        c.execute("ALTER TABLE tokens_new RENAME TO tokens")
        conn.commit()
        print("[OK] Migration tokens: UNIQUE(address, wallet_address) — plusieurs wallets, même mint")
    except Exception as e:
        conn.rollback()
        print(f"[!] Migration tokens wallet-scoped: {e}")
    finally:
        c.execute("PRAGMA foreign_keys=ON")


def _migrate_purchases_token_signature_unique(conn):
    """
    Ancien schéma : UNIQUE sur transaction_signature → une seule ligne d’achat par tx ;
    si un swap crédite 2 jetons non-stables, le 2ᵉ était ignoré (INSERT OR IGNORE) alors que
    les soldes token étaient quand même mis à jour — incohérence et PnL faux.
    Nouveau : UNIQUE(token_id, transaction_signature).
    """
    c = conn.cursor()
    row = c.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='purchases'"
    ).fetchone()
    if not row or not row[0]:
        return
    sl = " ".join(row[0].split())
    if "UNIQUE(token_id, transaction_signature)" in sl.replace(" ", ""):
        return
    if "transaction_signature TEXT UNIQUE" not in sl:
        return

    c.execute("PRAGMA table_info(purchases)")
    col_names = [r["name"] for r in c.fetchall()]
    required = [
        "id",
        "token_id",
        "purchase_date",
        "purchase_timestamp",
        "tokens_bought",
        "purchase_price",
        "sol_spent",
        "transaction_signature",
        "sol_usd_at_buy",
        "wallet_address",
        "created_at",
        "purchase_slot",
    ]
    if any(x not in col_names for x in required):
        print("[!] purchases: colonnes inattendues — migration UNIQUE(token_id, sig) ignorée")
        return

    c.execute("PRAGMA foreign_keys=OFF")
    try:
        c.execute("DROP TABLE IF EXISTS purchases_new")
        c.execute(
            """
            CREATE TABLE purchases_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id INTEGER NOT NULL,
                purchase_date TEXT NOT NULL,
                purchase_timestamp INTEGER,
                tokens_bought REAL NOT NULL,
                purchase_price REAL NOT NULL,
                sol_spent REAL NOT NULL,
                transaction_signature TEXT NOT NULL,
                sol_usd_at_buy REAL DEFAULT NULL,
                wallet_address TEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                purchase_slot INTEGER DEFAULT 0,
                FOREIGN KEY (token_id) REFERENCES tokens (id),
                UNIQUE(token_id, transaction_signature)
            )
            """
        )
        ins_cols = ", ".join(required)
        c.execute(
            f"""
            INSERT INTO purchases_new ({ins_cols})
            SELECT
                id, token_id, purchase_date, purchase_timestamp,
                tokens_bought, purchase_price, sol_spent, transaction_signature,
                sol_usd_at_buy, wallet_address, created_at, COALESCE(purchase_slot, 0)
            FROM purchases
            """
        )
        c.execute("DROP TABLE purchases")
        c.execute("ALTER TABLE purchases_new RENAME TO purchases")
        conn.commit()
        print(
            "[OK] Migration purchases: UNIQUE(token_id, transaction_signature) — plusieurs jetons / même tx"
        )
    except Exception as e:
        conn.rollback()
        print(f"[!] Migration purchases token+sig: {e}")
    finally:
        c.execute("PRAGMA foreign_keys=ON")


# === API ROUTES ===

_WALLET_SOL_FLOW_CACHE_TTL_SEC = 6 * 3600
# Incrémenter après changement de règle d’agrégation (ex. inclure les SWAP) pour forcer un refetch Helius.
WALLET_SOL_FLOW_AGG_VERSION = 2


def _ensure_wallet_sol_flow_schema(conn) -> None:
    if USE_POSTGRES:
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet_sol_flow (
                wallet_address TEXT PRIMARY KEY,
                sol_recu DOUBLE PRECISION NOT NULL DEFAULT 0,
                sol_envoye DOUBLE PRECISION NOT NULL DEFAULT 0,
                pages_scanned INTEGER NOT NULL DEFAULT 0,
                updated_ts DOUBLE PRECISION NOT NULL DEFAULT 0,
                flow_agg_version INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        cols = postgres_table_columns(c, "wallet_sol_flow")
        if cols and "flow_agg_version" not in cols:
            try:
                c.execute(
                    "ALTER TABLE wallet_sol_flow ADD COLUMN flow_agg_version INTEGER NOT NULL DEFAULT 0"
                )
            except Exception:
                pass
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wallet_sol_flow (
            wallet_address TEXT PRIMARY KEY,
            sol_recu REAL NOT NULL DEFAULT 0,
            sol_envoye REAL NOT NULL DEFAULT 0,
            pages_scanned INTEGER NOT NULL DEFAULT 0,
            updated_ts REAL NOT NULL DEFAULT 0
        )
        """
    )
    cols = [r[1] for r in conn.execute("PRAGMA table_info(wallet_sol_flow)").fetchall()]
    if "flow_agg_version" not in cols:
        try:
            conn.execute(
                "ALTER TABLE wallet_sol_flow ADD COLUMN flow_agg_version INTEGER NOT NULL DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass


def _invalidate_charts_cache():
    """Invalide le cache des graphiques (gains-history, portfolio-history)."""
    global _charts_cache
    _charts_cache.clear()

def _dashboard_cache_keys_for_wallet(wallet: str) -> tuple:
    """Variantes cache : plein HIFO vs skip_hifo (chargement rapide)."""
    return (f"wallet_{wallet}", f"wallet_{wallet}__lite")


def _invalidate_dashboard_cache(wallet: Optional[str] = None):
    """Invalide le cache du dashboard pour un wallet ou tous les wallets"""
    global _dashboard_cache
    if wallet:
        for cache_key in _dashboard_cache_keys_for_wallet(wallet):
            _dashboard_cache.pop(cache_key, None)
    else:
        _dashboard_cache.clear()


def _get_reference_capital_usd(conn: sqlite3.Connection, wallet: str) -> float:
    """Total dépensé manuel (USD) — 0 = non défini."""
    w = (wallet or "").strip()
    if not w:
        return 0.0
    c = conn.cursor()
    c.execute(
        "SELECT amount_usd FROM wallet_reference_capital WHERE wallet_address = ?",
        (w,),
    )
    r = c.fetchone()
    if not r:
        return 0.0
    try:
        v = float(r["amount_usd"] or 0)
        return v if v > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


# --- Snapshots P/L par wallet (graphique « évolution du gain ») ---
_SNAPSHOT_MIN_INTERVAL_SEC = 900  # 15 min entre deux points si le P/L bouge peu
_SNAPSHOT_MIN_ABS_DELTA_USD = 1.0


def _token_non_wsol_clause(alias: str = "tokens") -> str:
    """Même périmètre que update_all_prices : pas de ligne adresse vide, pas de wSOL."""
    return (
        f"{alias}.address != '' AND COALESCE({alias}.address, '') != :wsol_mint"
    )


def _sum_tracked_invested_usd_for_wallet(cursor, wallet: str, sol_usd: float) -> float:
    """
    Total dépensé (USD) = somme par token du coût d’achat, aligné sur update_all_prices :
    Σ(sol_spent × taux USD/SOL) sur purchases si > 0, sinon invested_amount × taux ou USD (même règle que les cartes gain/perte).
    """
    cursor.execute(
        f"""
        SELECT id, invested_amount, sol_usd_at_buy
        FROM tokens
        WHERE wallet_address = :wallet AND {_token_non_wsol_clause('tokens')}
        """,
        {"wallet": wallet, "wsol_mint": SOL_MINT},
    )
    rows = cursor.fetchall()
    if not rows:
        return 0.0
    ids = [int(r["id"]) for r in rows]
    ph = ",".join("?" * len(ids))
    cursor.execute(
        f"""
        SELECT token_id,
               COALESCE(SUM(sol_spent * COALESCE(NULLIF(sol_usd_at_buy, 0), ?)), 0) AS tot
        FROM purchases
        WHERE token_id IN ({ph}) AND tokens_bought > 0 AND sol_spent > 0
        GROUP BY token_id
        """,
        [sol_usd] + ids,
    )
    pmap = {int(r["token_id"]): float(r["tot"] or 0) for r in cursor.fetchall()}
    total = 0.0
    for r in rows:
        tid = int(r["id"])
        pup = pmap.get(tid, 0.0)
        amt = float(r["invested_amount"] or 0)
        sol_at = r["sol_usd_at_buy"]
        if sol_at:
            fb = amt * (float(sol_at) or sol_usd)
        else:
            fb = amt
        # Même branchement que update_all_prices : dès qu’il y a des lignes purchases valides, leur somme fait foi.
        total += pup if pup > 0 else fb
    return total


def _parse_activity_ts_candidate(value) -> Optional[datetime]:
    """Interprète created_at SQLite, unix (s ou ms), date TEXT, ISO."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and float(value) > 0:
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s[:26], fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _day_start_end_unix(date_hint) -> tuple[int, int]:
    """Début / fin de journée UTC pour une date d’activité (TEXT ou datetime)."""
    dt = _parse_activity_ts_candidate(date_hint)
    if dt is None:
        return (0, 0)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    end = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return (int(start.timestamp()), int(end.timestamp()))


def _lot_ts_for_hifo(purchase_ts: int | float | None, purchase_date) -> int:
    """
    Borne basse d’instant d’achat pour HIFO : unix si présent, sinon minuit UTC du purchase_date.
    """
    try:
        ts = int(purchase_ts or 0)
    except (TypeError, ValueError):
        ts = 0
    if ts > int(1e12):
        ts = int(ts / 1000)
    if ts > 0:
        return ts
    start, _ = _day_start_end_unix(purchase_date)
    return start


def _sale_ts_ceiling_for_hifo(sale_ts: int | float | None, sale_date) -> int:
    """
    Borne haute d’instant de vente pour HIFO : unix si présent, sinon fin de journée UTC du sale_date.
    """
    try:
        ts = int(sale_ts or 0)
    except (TypeError, ValueError):
        ts = 0
    if ts > int(1e12):
        ts = int(ts / 1000)
    if ts > 0:
        return ts
    _, end = _day_start_end_unix(sale_date)
    return end


def _sale_sort_key_for_hifo(sale: dict) -> tuple:
    """Tri chronologique des ventes (timestamp, sinon milieu de journée issue de sale_date)."""
    try:
        ts = int(sale.get("sale_ts") or 0)
    except (TypeError, ValueError):
        ts = 0
    if ts > int(1e12):
        ts = int(ts / 1000)
    if ts > 0:
        return (ts, int(sale.get("sale_slot") or 0), int(sale.get("sale_id") or 0))
    start, end = _day_start_end_unix(sale.get("sale_date"))
    mid = (start + end) // 2 if start > 0 and end > 0 else 0
    return (mid, int(sale.get("sale_slot") or 0), int(sale.get("sale_id") or 0))


def _dt_to_sqlite_recorded_at(dt: datetime) -> str:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _wallet_portfolio_origin_recorded_at(cursor: sqlite3.Cursor, wallet: str) -> Optional[str]:
    """
    Plus ancienne trace connue du portefeuille en base (token, achats/ventes importés,
    enregistrement du wallet). Approximation de « création » côté données disponibles.
    """
    w = (wallet or "").strip()
    if not w:
        return None
    candidates: List[datetime] = []

    cursor.execute("SELECT MIN(created_at) AS t FROM tokens WHERE wallet_address = ?", (w,))
    row = cursor.fetchone()
    if row and row["t"] is not None:
        d = _parse_activity_ts_candidate(row["t"])
        if d:
            candidates.append(d)

    cursor.execute(
        """
        SELECT MIN(p.purchase_timestamp) AS ts FROM purchases p
        INNER JOIN tokens t ON p.token_id = t.id
        WHERE t.wallet_address = ? AND COALESCE(p.purchase_timestamp, 0) > 0
        """,
        (w,),
    )
    row = cursor.fetchone()
    if row and row["ts"] is not None:
        d = _parse_activity_ts_candidate(int(row["ts"]))
        if d:
            candidates.append(d)

    cursor.execute(
        """
        SELECT MIN(s.sale_timestamp) AS ts FROM sales s
        INNER JOIN tokens t ON s.token_id = t.id
        WHERE t.wallet_address = ? AND COALESCE(s.sale_timestamp, 0) > 0
        """,
        (w,),
    )
    row = cursor.fetchone()
    if row and row["ts"] is not None:
        d = _parse_activity_ts_candidate(int(row["ts"]))
        if d:
            candidates.append(d)

    cursor.execute(
        """
        SELECT MIN(p.purchase_date) AS d FROM purchases p
        INNER JOIN tokens t ON p.token_id = t.id
        WHERE t.wallet_address = ? AND TRIM(COALESCE(p.purchase_date,'')) != ''
        """,
        (w,),
    )
    row = cursor.fetchone()
    if row and row["d"] is not None:
        d = _parse_activity_ts_candidate(row["d"])
        if d:
            candidates.append(d)

    cursor.execute(
        """
        SELECT MIN(s.sale_date) AS d FROM sales s
        INNER JOIN tokens t ON s.token_id = t.id
        WHERE t.wallet_address = ? AND TRIM(COALESCE(s.sale_date,'')) != ''
        """,
        (w,),
    )
    row = cursor.fetchone()
    if row and row["d"] is not None:
        d = _parse_activity_ts_candidate(row["d"])
        if d:
            candidates.append(d)

    cursor.execute("SELECT created_at FROM wallets WHERE address = ?", (w,))
    row = cursor.fetchone()
    if row and row["created_at"] is not None:
        d = _parse_activity_ts_candidate(row["created_at"])
        if d:
            candidates.append(d)

    if not candidates:
        return None
    return _dt_to_sqlite_recorded_at(min(candidates))


def _wallet_portfolio_origin_date_only(cursor: sqlite3.Cursor, wallet: str) -> Optional[str]:
    """Date calendaire YYYY-MM-DD de l'origine (pour filtrer l'évolution portfolio)."""
    full = _wallet_portfolio_origin_recorded_at(cursor, wallet)
    if not full or len(full) < 10:
        return None
    return full[:10]


def _maybe_realign_wallet_pnl_origin(conn, wallet: str) -> None:
    """Recule le premier snapshot si une activité plus ancienne est connue en base."""
    w = (wallet or "").strip()
    if not w:
        return
    c = conn.cursor()
    origin = _wallet_portfolio_origin_recorded_at(c, w)
    if not origin:
        return
    c.execute(
        """
        SELECT id, recorded_at FROM wallet_pnl_snapshots
        WHERE wallet_address = ?
        ORDER BY datetime(recorded_at) ASC, id ASC
        LIMIT 1
        """,
        (w,),
    )
    row = c.fetchone()
    if not row:
        return
    c.execute(
        "SELECT CASE WHEN datetime(?) < datetime(?) THEN 1 ELSE 0 END AS sooner",
        (origin, row["recorded_at"]),
    )
    if int(c.fetchone()["sooner"] or 0) != 1:
        return
    c.execute(
        "UPDATE wallet_pnl_snapshots SET recorded_at = ? WHERE id = ?",
        (origin, row["id"]),
    )
    conn.commit()


def _wallet_pnl_aggregate(cursor, wallet: str, sol_usd: float) -> dict:
    """Valeurs pour snapshots ; net_total ici = colonnes gain/loss en BDD (latent stocké), pas le net combiné du dashboard."""
    wf = "AND wallet_address = :wallet"
    ti = _sum_tracked_invested_usd_for_wallet(cursor, wallet, sol_usd)
    cursor.execute(
        f"""
        SELECT
            COALESCE(SUM(CASE WHEN {_token_non_wsol_clause()} THEN COALESCE(current_value, 0) ELSE 0 END), 0) as current_value_usd,
            COALESCE(SUM(CASE WHEN {_token_non_wsol_clause()} THEN COALESCE(gain, 0) ELSE 0 END), 0) as latent_gain_usd,
            COALESCE(SUM(CASE WHEN {_token_non_wsol_clause()} THEN COALESCE(loss, 0) ELSE 0 END), 0) as latent_loss_usd
        FROM tokens WHERE 1=1 {wf}
        """,
        {"wallet": wallet, "wsol_mint": SOL_MINT},
    )
    stats = cursor.fetchone()
    cv = float(stats["current_value_usd"] or 0)
    lg = float(stats["latent_gain_usd"] or 0)
    ll = float(stats["latent_loss_usd"] or 0)
    cursor.execute(
        """
        SELECT COALESCE(SUM(s.sol_received * COALESCE(NULLIF(s.sol_usd_at_sale,0), :sol_now)), 0) AS withdrawn_usd
        FROM sales s JOIN tokens t ON s.token_id = t.id
        WHERE t.wallet_address = :wallet
        """,
        {"sol_now": sol_usd, "wallet": wallet},
    )
    wd = float(cursor.fetchone()["withdrawn_usd"] or 0)
    net = lg - ll
    return {
        "total_invested_usd": ti,
        "current_value_usd": cv,
        "withdrawn_usd": wd,
        "net_total": net,
        "flow_net_usd": cv + wd - ti,
    }


def _record_wallet_pnl_snapshot(conn, wallet: str, sol_usd: float, force: bool = False) -> None:
    """Enregistre un point (net P/L) pour le graphique. Premier passage : origine = plus ancienne activité connue en base."""
    w = (wallet or "").strip()
    if not w:
        return
    c = conn.cursor()
    agg = _wallet_pnl_aggregate(c, w, sol_usd)
    _, _, net, _, _ = _hifo_dashboard_gain_loss_net(c, w, sol_usd)

    c.execute("SELECT COUNT(*) AS n FROM wallet_pnl_snapshots WHERE wallet_address = ?", (w,))
    n = int(c.fetchone()["n"] or 0)

    if n == 0:
        start_t = _wallet_portfolio_origin_recorded_at(c, w)
        if not start_t:
            c.execute("SELECT MIN(created_at) AS t FROM tokens WHERE wallet_address = ?", (w,))
            start_row = c.fetchone()
            start_t = start_row["t"] if start_row and start_row["t"] else None
        if start_t:
            c.execute(
                """
                INSERT INTO wallet_pnl_snapshots (wallet_address, recorded_at, net_pnl_usd, total_invested_usd, current_value_usd, withdrawn_usd)
                VALUES (?, ?, 0, 0, 0, 0)
                """,
                (w, start_t),
            )

    if not force and n > 0:
        c.execute(
            """
            SELECT net_pnl_usd FROM wallet_pnl_snapshots
            WHERE wallet_address = ? ORDER BY datetime(recorded_at) DESC, id DESC LIMIT 1
            """,
            (w,),
        )
        last = c.fetchone()
        last_net = float(last["net_pnl_usd"]) if last else None
        c.execute(
            """
            SELECT 1 FROM wallet_pnl_snapshots
            WHERE wallet_address = ? AND datetime(recorded_at) > datetime('now', ?) LIMIT 1
            """,
            (w, f"-{_SNAPSHOT_MIN_INTERVAL_SEC} seconds"),
        )
        recent = c.fetchone()
        if recent and last_net is not None and abs(net - last_net) < _SNAPSHOT_MIN_ABS_DELTA_USD:
            return

    c.execute(
        """
        INSERT INTO wallet_pnl_snapshots (wallet_address, recorded_at, net_pnl_usd, total_invested_usd, current_value_usd, withdrawn_usd)
        VALUES (?, datetime('now'), ?, ?, ?, ?)
        """,
        (w, net, agg["total_invested_usd"], agg["current_value_usd"], agg["withdrawn_usd"]),
    )
    conn.commit()


def _ensure_wallet_pnl_snapshots(conn, wallet: str, sol_usd: float) -> None:
    """Si aucun point pour ce wallet, crée la courbe initiale (origine + maintenant)."""
    w = (wallet or "").strip()
    if not w:
        return
    c = conn.cursor()
    c.execute("SELECT COUNT(*) AS n FROM wallet_pnl_snapshots WHERE wallet_address = ?", (w,))
    if int(c.fetchone()["n"] or 0) > 0:
        return
    _record_wallet_pnl_snapshot(conn, w, sol_usd, force=True)


@app.on_event("startup")
async def startup_event():
    if USE_POSTGRES:
        with get_pg_connection() as conn:
            c = conn.cursor()
            init_postgres_schema(c)
            auth_service.ensure_auth_tables(c)
            conn.commit()
        print("[OK] Base PostgreSQL initialisee (schema + index)")
    else:
        init_db()
        # Migrations douces : colonnes ajoutées après la création initiale
        with get_db() as conn:
            c = conn.cursor()
            # sales : colonnes historiques
            c.execute("PRAGMA table_info(sales)")
            sale_cols = [r["name"] for r in c.fetchall()]
            for col, typedef in [
                ("transaction_signature", "TEXT"),
                ("sale_timestamp", "INTEGER"),
                ("sale_slot", "INTEGER DEFAULT 0"),
                ("sol_received", "REAL DEFAULT 0"),
                ("sol_usd_at_sale", "REAL DEFAULT NULL"),
                ("hifo_buy_cost_usd", "REAL DEFAULT NULL"),
                ("hifo_pnl_usd", "REAL DEFAULT NULL"),
            ]:
                if col not in sale_cols:
                    c.execute(f"ALTER TABLE sales ADD COLUMN {col} {typedef}")
            # tokens : colonnes ajoutées après la création initiale
            c.execute("PRAGMA table_info(tokens)")
            token_cols = [r["name"] for r in c.fetchall()]
            if "sol_usd_at_buy" not in token_cols:
                c.execute("ALTER TABLE tokens ADD COLUMN sol_usd_at_buy REAL DEFAULT NULL")
            if "wallet_address" not in token_cols:
                c.execute("ALTER TABLE tokens ADD COLUMN wallet_address TEXT DEFAULT NULL")
            if "price_is_stale" not in token_cols:
                c.execute("ALTER TABLE tokens ADD COLUMN price_is_stale BOOLEAN DEFAULT 0")
            if "price_warning" not in token_cols:
                c.execute("ALTER TABLE tokens ADD COLUMN price_warning TEXT DEFAULT NULL")
            if "user_position_cost_usd" not in token_cols:
                c.execute("ALTER TABLE tokens ADD COLUMN user_position_cost_usd REAL DEFAULT NULL")
            # Migration : attribuer le wallet de settings aux tokens qui n'en ont pas
            try:
                c.execute("SELECT value FROM settings WHERE key = 'wallet_address'")
                settings_row = c.fetchone()
                if settings_row and settings_row["value"]:
                    c.execute("UPDATE tokens SET wallet_address = ? WHERE wallet_address IS NULL OR wallet_address = ''",
                              (settings_row["value"],))
            except Exception:
                pass
            _migrate_tokens_wallet_scoped_unique(conn)
            # Table de déduplication des imports
            c.execute("""
                CREATE TABLE IF NOT EXISTS imported_tx (
                    signature   TEXT PRIMARY KEY,
                    tx_type     TEXT NOT NULL,
                    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS wallet_hifo_cache (
                    wallet_address TEXT PRIMARY KEY,
                    realized_gain REAL NOT NULL DEFAULT 0,
                    realized_loss REAL NOT NULL DEFAULT 0,
                    fingerprint TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS wallet_pnl_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wallet_address TEXT NOT NULL,
                    recorded_at TIMESTAMP NOT NULL,
                    net_pnl_usd REAL NOT NULL,
                    total_invested_usd REAL DEFAULT 0,
                    current_value_usd REAL DEFAULT 0,
                    withdrawn_usd REAL DEFAULT 0
                )
            """)
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_wallet_pnl_wallet_time ON wallet_pnl_snapshots(wallet_address, recorded_at)"
            )
            # Table des achats individuels (migration douce)
            c.execute("""
                CREATE TABLE IF NOT EXISTS purchases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_id INTEGER NOT NULL,
                    purchase_date TEXT NOT NULL,
                    purchase_timestamp INTEGER,
                    tokens_bought REAL NOT NULL,
                    purchase_price REAL NOT NULL,
                    sol_spent REAL NOT NULL,
                    transaction_signature TEXT NOT NULL,
                    sol_usd_at_buy REAL DEFAULT NULL,
                    wallet_address TEXT DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (token_id) REFERENCES tokens (id),
                    UNIQUE(token_id, transaction_signature)
                )
            """)
            # Migration douce : ajouter wallet_address aux purchases existantes
            c.execute("PRAGMA table_info(purchases)")
            purchase_cols = [r["name"] for r in c.fetchall()]
            if "wallet_address" not in purchase_cols:
                c.execute("ALTER TABLE purchases ADD COLUMN wallet_address TEXT DEFAULT NULL")
                c.execute("""
                    UPDATE purchases SET wallet_address = (
                        SELECT t.wallet_address FROM tokens t WHERE t.id = purchases.token_id
                    ) WHERE wallet_address IS NULL
                """)
            if "purchase_slot" not in purchase_cols:
                c.execute("ALTER TABLE purchases ADD COLUMN purchase_slot INTEGER DEFAULT 0")
    
            _migrate_purchases_token_signature_unique(conn)
            
            # === CRÉER LES INDEXES POUR MAXIMISER LA PERFORMANCE ===
            indexes = [
                # Indexes simples
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
                # Composites : requêtes dashboard / initial-load / import (wallet + tri / jointures)
                ("idx_sales_token_date", "sales", "(token_id, sale_date)"),
                ("idx_purchases_token_timestamp", "purchases", "(token_id, purchase_timestamp)"),
                ("idx_tokens_wallet_created", "tokens", "(wallet_address, created_at)"),
                ("idx_tokens_wallet_mint", "tokens", "(wallet_address, address)"),
                ("idx_purchases_wallet_ts", "purchases", "(wallet_address, purchase_timestamp)"),
                ("idx_sales_token_ts", "sales", "(token_id, sale_timestamp)"),
                ("idx_price_history_token_ts", "price_history", "(token_id, timestamp)"),
            ]
            for idx_name, table_name, column_spec in indexes:
                c.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table_name} {column_spec}")
    
            # Index partiels : sous-requêtes SUM / EXISTS sur achats « réels » (moins de lignes à parcourir)
            for partial_sql in (
                "CREATE INDEX IF NOT EXISTS idx_purchases_token_active ON purchases(token_id) WHERE tokens_bought > 0 AND sol_spent > 0",
                "CREATE INDEX IF NOT EXISTS idx_purchases_wallet_active ON purchases(wallet_address, token_id) WHERE tokens_bought > 0 AND sol_spent > 0",
            ):
                c.execute(partial_sql)
    
            # Statistiques pour l’optimiseur de requêtes (après création d’index)
            try:
                c.execute("ANALYZE tokens")
                c.execute("ANALYZE purchases")
                c.execute("ANALYZE sales")
                c.execute("ANALYZE price_history")
                c.execute("PRAGMA optimize")
            except sqlite3.Error:
                pass
    
            # === Tables investisseur long terme ===
            c.execute("""
                CREATE TABLE IF NOT EXISTS token_targets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_id INTEGER NOT NULL,
                    wallet_address TEXT,
                    mcap_target TEXT,
                    tp_price REAL,
                    sl_price REAL,
                    alert_enabled INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (token_id) REFERENCES tokens (id)
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS token_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_id INTEGER NOT NULL,
                    note_date TEXT NOT NULL,
                    content TEXT,
                    event_type TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (token_id) REFERENCES tokens (id)
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS wallets (
                    address TEXT PRIMARY KEY,
                    label TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS wallet_reference_capital (
                    wallet_address TEXT PRIMARY KEY,
                    amount_usd REAL NOT NULL DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            auth_service.ensure_auth_tables(c)
            c.execute("CREATE INDEX IF NOT EXISTS idx_token_targets_token ON token_targets(token_id)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_token_notes_token ON token_notes(token_id)")
    
            conn.commit()
        print("[OK] Base de donnees initialisee + index / ANALYZE / PRAGMA perf")
    
    # Pré-charger le prix SOL (BLOQUANT - attendre avant de démarrer le serveur)
    try:
        print("[...] Chargement du prix SOL/USD...")
        price = await _get_sol_usd_price()
        print(f"[OK] Prix SOL precharge: ${price:.2f}/USD")
    except Exception as e:
        print(f"[!] Erreur precharge prix: {e}")
        print("[!] Le serveur continuera avec un prix par défaut")


@app.get("/api/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


# Dashboard
@app.get("/api/dashboard", response_model=Dashboard)
async def get_dashboard(
    wallet: Optional[str] = Query(None),
    no_cache: bool = Query(False, description="Bypass cache (après Actualiser)"),
    skip_hifo: bool = Query(False, description="Ne pas simuler HIFO (rapide ; lit le cache BDD si à jour)"),
):
    """
    Dashboard avec cache par wallet.
    skip_hifo=1 : pas de simulation live pour la liste tx ; cache HIFO pour hifo_pending.
    Les cartes Gain/Perte **figés** utilisent sales.hifo_pnl_usd dès que chaque vente en a une
    (figé au recalcul) — plus le cours SOL du jour. Sinon repli sur calcul live.
    no_cache=1 pour forcer des données fraîches (SOL, etc.).
    Sans wallet : zéros (ne jamais agréger toute la BDD — la SPA affichait des totaux incohérents).
    """
    global _dashboard_cache, _dashboard_cache_lock

    if wallet is None or not str(wallet).strip():
        try:
            sol_usd = await _get_sol_usd_price()
        except Exception:
            sol_usd = None
        return Dashboard(
            total_risked=0.0,
            current_amount=0.0,
            withdrawn_amount=0.0,
            flow_net_usd=0.0,
            total_gain=0.0,
            total_loss=0.0,
            net_total=0.0,
            realized_gain=0.0,
            realized_loss=0.0,
            hifo_pending=False,
            last_sale_token=None,
            last_sale_amount_sol=None,
            last_sale_date=None,
            sol_price_usd=sol_usd,
            wallet_sol_balance=None,
            tracked_purchases_usd=0.0,
            reference_capital_usd=0.0,
            transfer_sol_recu=0.0,
            transfer_sol_envoye=0.0,
            transfer_flow_net_usd=0.0,
            transfer_basis_usd=0.0,
            total_risked_source="unset",
        )

    cache_key = f"wallet_{wallet}__lite" if skip_hifo else f"wallet_{wallet}"
    now = time.time()

    # Bypass cache si demandé (après Actualiser)
    if no_cache and cache_key in _dashboard_cache:
        del _dashboard_cache[cache_key]

    # Vérifier cache rapide (sans lock)
    if cache_key in _dashboard_cache:
        cached = _dashboard_cache[cache_key]
        ttl = float(cached.get("ttl") or 12)
        if cached["data"] is not None and (now - cached["timestamp"]) < ttl:
            return cached["data"]

    # Calculer (avec lock pour éviter doublons)
    async with _dashboard_cache_lock:
        # Re-check après acquisition du lock
        if cache_key in _dashboard_cache:
            cached = _dashboard_cache[cache_key]
            ttl = float(cached.get("ttl") or 12)
            if cached["data"] is not None and (now - cached["timestamp"]) < ttl:
                return cached["data"]
        
        try:
            tasks = [_get_sol_usd_price()]
            if wallet:
                tasks.append(_fetch_wallet_sol_balance(wallet))
            results = await asyncio.gather(*tasks)
            sol_usd = results[0]
            wallet_sol = results[1] if wallet else None
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))

        with get_db() as conn:
            cursor = conn.cursor()
            # S'assurer que les colonnes slot existent (migration si backend pas redémarré)
            try:
                cursor.execute("SELECT sale_slot FROM sales LIMIT 1")
            except sqlite3.OperationalError:
                try:
                    cursor.execute("ALTER TABLE sales ADD COLUMN sale_slot INTEGER DEFAULT 0")
                    conn.commit()
                except sqlite3.OperationalError:
                    pass  # Colonne existe peut-être déjà
            try:
                cursor.execute("SELECT purchase_slot FROM purchases LIMIT 1")
            except sqlite3.OperationalError:
                try:
                    cursor.execute("ALTER TABLE purchases ADD COLUMN purchase_slot INTEGER DEFAULT 0")
                    conn.commit()
                except sqlite3.OperationalError:
                    pass

            wallet_filter = "AND wallet_address = :wallet" if wallet else ""

            # Total dépensé : même logique que update_all_prices (voir _sum_tracked_invested_usd_for_wallet).
            total_invested_usd = _sum_tracked_invested_usd_for_wallet(cursor, wallet, sol_usd)
            cursor.execute(
                f"""
                SELECT
                    COALESCE(SUM(CASE WHEN {_token_non_wsol_clause()} THEN COALESCE(current_value, 0) ELSE 0 END), 0) as current_value_usd
                FROM tokens WHERE 1=1 {wallet_filter}
                """,
                {"wallet": wallet, "wsol_mint": SOL_MINT},
            )
            stats = cursor.fetchone()
            current_value_usd = stats["current_value_usd"] or 0

            # Retraits
            sales_wallet_filter = "AND t.wallet_address = :wallet" if wallet else ""
            cursor.execute(f"""
                SELECT COALESCE(SUM(s.sol_received * COALESCE(NULLIF(s.sol_usd_at_sale,0), :sol_now)), 0) AS withdrawn_usd
                FROM sales s JOIN tokens t ON s.token_id = t.id
                WHERE 1=1 {sales_wallet_filter}
            """, {"sol_now": sol_usd, "wallet": wallet})
            withdrawn_usd = cursor.fetchone()['withdrawn_usd'] or 0

            # Gains/Pertes cartes = latent ; net = latent + (gain figé − perte figée).
            total_gain_usd, total_loss_usd, net_total, realized_gain, realized_loss = _hifo_dashboard_gain_loss_net(
                cursor, wallet, sol_usd
            )
            if skip_hifo:
                fp = _wallet_hifo_fingerprint(cursor, wallet)
                cursor.execute(
                    "SELECT fingerprint FROM wallet_hifo_cache WHERE wallet_address = ?",
                    (wallet,),
                )
                wh = cursor.fetchone()
                hifo_pending = (not wh) or (wh["fingerprint"] != fp)
            else:
                hifo_pending = False

            # Dernière vente
            cursor.execute(f"""
                SELECT s.sol_received, s.sale_date, t.name FROM sales s
                JOIN tokens t ON s.token_id = t.id
                WHERE 1=1 {sales_wallet_filter}
                ORDER BY s.sale_date DESC, s.created_at DESC LIMIT 1
            """, {"wallet": wallet})
            last_sale = cursor.fetchone()
            last_sale_token = last_sale['name'] if last_sale else None
            last_sale_amount = last_sale['sol_received'] if last_sale else None
            last_sale_date = last_sale['sale_date'] if last_sale else None

            reference_usd = _get_reference_capital_usd(conn, wallet)
            tracked_usd = float(total_invested_usd or 0)

        flow_recu = 0.0
        flow_envoye = 0.0
        transfer_flow_net_usd = 0.0
        transfer_basis_usd = 0.0

        capital_basis = float(reference_usd) if reference_usd > 0 else 0.0
        risk_src = "manual" if capital_basis > 0 else "unset"
        flow_net_usd = current_value_usd + withdrawn_usd - capital_basis

        dashboard_result = Dashboard(
            total_risked=capital_basis,
            current_amount=current_value_usd,
            withdrawn_amount=withdrawn_usd,
            flow_net_usd=flow_net_usd,
            total_gain=total_gain_usd,
            total_loss=total_loss_usd,
            net_total=net_total,
            realized_gain=realized_gain,
            realized_loss=realized_loss,
            hifo_pending=hifo_pending,
            last_sale_token=last_sale_token,
            last_sale_amount_sol=last_sale_amount,
            last_sale_date=last_sale_date,
            sol_price_usd=sol_usd,
            wallet_sol_balance=float(wallet_sol) if wallet_sol is not None else None,
            tracked_purchases_usd=tracked_usd,
            reference_capital_usd=reference_usd,
            transfer_sol_recu=flow_recu,
            transfer_sol_envoye=flow_envoye,
            transfer_flow_net_usd=transfer_flow_net_usd,
            transfer_basis_usd=transfer_basis_usd,
            total_risked_source=risk_src,
        )

        # TTL : skip_hifo — cache long (les realized viennent de la BDD ; pas de rafraîchissement agressif)
        if skip_hifo and not hifo_pending:
            _dash_ttl = 600.0
        elif skip_hifo:
            _dash_ttl = 600.0
        else:
            _dash_ttl = 90.0

        _dashboard_cache[cache_key] = {
            "data": dashboard_result,
            "timestamp": time.time(),
            "ttl": _dash_ttl,
        }
        return dashboard_result

# === CHARGEMENT COMBINÉ (1 appel au lieu de 3) ===
@app.get("/api/initial-load")
async def initial_load(
    wallet: Optional[str] = Query(None),
    tx_limit: int = Query(100, ge=0, le=500),
    skip_txs: bool = Query(False),
    no_cache: bool = Query(False, description="Bypass cache dashboard (après Actualiser)"),
    skip_hifo: bool = Query(False, description="Pas de HIFO sur dashboard + liste tx (rapide)"),
):
    """Retourne dashboard + tokens + transactions. skip_txs=1 sans liste tx ; skip_hifo=1 sans simulation HIFO."""
    if no_cache and wallet:
        _invalidate_dashboard_cache(wallet)
    if skip_txs or tx_limit == 0:
        dash, toks = await asyncio.gather(
            get_dashboard(wallet, no_cache=no_cache, skip_hifo=skip_hifo),
            get_tokens(wallet),
        )
        return {
            "dashboard": dash.model_dump() if hasattr(dash, "model_dump") else dash,
            "tokens": toks,
            "transactions": [],
        }
    dash, toks, txs = await asyncio.gather(
        get_dashboard(wallet, no_cache=no_cache, skip_hifo=skip_hifo),
        get_tokens(wallet),
        get_all_transactions(wallet, tx_limit, skip_hifo=skip_hifo),
    )
    return {
        "dashboard": dash.model_dump() if hasattr(dash, "model_dump") else dash,
        "tokens": toks,
        "transactions": txs,
    }


# Tokens CRUD
@app.get("/api/tokens")
async def get_tokens(wallet: Optional[str] = Query(None)):
    if wallet is None or not str(wallet).strip():
        return []
    try:
        sol_usd = await _get_sol_usd_price()
    except RuntimeError:
        sol_usd = 150.0
    with get_db() as conn:
        cursor = conn.cursor()
        wallet_filter = "AND t.wallet_address = ?"
        params = (wallet,)
        # Prix 24h en 1 requête (évite N+1)
        cursor.execute(f"""
            SELECT ph.token_id, ph.price
            FROM price_history ph
            INNER JOIN (
                SELECT token_id, MAX(timestamp) as max_ts
                FROM price_history
                WHERE timestamp <= datetime('now', '-23 hours')
                GROUP BY token_id
            ) sub ON ph.token_id = sub.token_id AND ph.timestamp = sub.max_ts
            INNER JOIN tokens t ON t.id = ph.token_id
            WHERE 1=1 {wallet_filter}
        """, params)
        price_24h = {r["token_id"]: r["price"] for r in cursor.fetchall()}
        cursor.execute("SELECT * FROM tokens WHERE wallet_address = ? ORDER BY created_at DESC", (wallet,))
        token_rows = list(cursor.fetchall())
        result = []
        wkey = str(wallet).strip()
        vwap_usd = _purchase_vwap_usd_by_token(cursor, wkey, sol_usd)
        try:
            hifo_map, open_avg_usd = _hifo_per_token_gain_loss_and_open_avg(cursor, wkey, sol_usd)
        except Exception as e:
            print(f"[!] get_tokens HIFO overlay: {e}")
            hifo_map = {}
            open_avg_usd = {}
        tid_non_wsol = [
            int(dict(tr)["id"])
            for tr in token_rows
            if str(dict(tr).get("address") or "") != str(SOL_MINT)
        ]
        try:
            auto_cost_map, auto_pos_map = _remaining_avg_cost_and_pos_by_token_ids(
                cursor, tid_non_wsol, sol_usd
            )
        except Exception as e:
            print(f"[!] get_tokens auto position cost: {e}")
            auto_cost_map, auto_pos_map = {}, {}
        for token in token_rows:
            t = dict(token)
            t["price_24h_ago"] = price_24h.get(t["id"])
            sol_at_buy = t.get("sol_usd_at_buy")
            tid = int(t["id"])
            ct = float(t.get("current_tokens") or 0)
            oa = open_avg_usd.get(tid)
            if (
                ct > 1e-12
                and oa is not None
                and oa > 0
                and str(t.get("address") or "") != str(SOL_MINT)
            ):
                t["purchase_price_usd"] = oa
            else:
                vw = vwap_usd.get(tid)
                if vw is not None and vw > 0:
                    t["purchase_price_usd"] = vw
                else:
                    t["purchase_price_usd"] = (t.get("purchase_price") or 0) * sol_at_buy if sol_at_buy else (t.get("purchase_price") or 0)
            hid = hifo_map.get(int(t["id"]))
            if hid is not None and str(t.get("address") or "") != str(SOL_MINT):
                t["gain"] = hid["gain"]
                t["loss"] = hid["loss"]
                lp = hid.get("latent_pnl_pct")
                if lp is not None and isinstance(lp, (int, float)) and math.isfinite(float(lp)):
                    t["latent_pnl_pct"] = round(float(lp), 4)
            _overlay_position_cost_display(t, auto_cost_map, auto_pos_map)
            result.append(t)
        return result

@app.get("/api/tokens/{token_id}", response_model=Token)
async def get_token(token_id: int):
    try:
        sol_usd = await _get_sol_usd_price()
    except RuntimeError:
        sol_usd = 150.0
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tokens WHERE id = ?", (token_id,))
        token = cursor.fetchone()
        if not token:
            raise HTTPException(status_code=404, detail="Token non trouvé")
        t = dict(token)
        wa = (t.get("wallet_address") or "").strip()
        ct = float(t.get("current_tokens") or 0)
        hifo_map_one: dict[int, dict[str, float]] = {}
        open_avg_usd: dict[int, float] = {}
        if len(wa) >= 20 and str(t.get("address") or "") != str(SOL_MINT):
            try:
                hifo_map_one, open_avg_usd = _hifo_per_token_gain_loss_and_open_avg(cursor, wa, sol_usd)
            except Exception as e:
                print(f"[!] get_token HIFO: {e}")
        oa = open_avg_usd.get(token_id)
        if ct > 1e-12 and oa is not None and oa > 0 and str(t.get("address") or "") != str(SOL_MINT):
            t["purchase_price_usd"] = oa
        else:
            vw = _purchase_vwap_usd_for_token_id(cursor, token_id, sol_usd)
            if vw is not None and vw > 0:
                t["purchase_price_usd"] = vw
            else:
                sol_at_buy = t.get("sol_usd_at_buy")
                t["purchase_price_usd"] = (t.get("purchase_price") or 0) * sol_at_buy if sol_at_buy else (t.get("purchase_price") or 0)
        if len(wa) >= 20 and str(t.get("address") or "") != str(SOL_MINT):
            hid = hifo_map_one.get(token_id)
            if hid is not None:
                t["gain"] = hid["gain"]
                t["loss"] = hid["loss"]
                lp = hid.get("latent_pnl_pct")
                if lp is not None and isinstance(lp, (int, float)) and math.isfinite(float(lp)):
                    t["latent_pnl_pct"] = round(float(lp), 4)
        try:
            auto_cost_map, auto_pos_map = _remaining_avg_cost_and_pos_by_token_ids(
                cursor, [token_id], sol_usd
            )
        except Exception as e:
            print(f"[!] get_token auto position cost: {e}")
            auto_cost_map, auto_pos_map = {}, {}
        _overlay_position_cost_display(t, auto_cost_map, auto_pos_map)
        return t

@app.get("/api/tokens/{token_id}/purchases")
async def get_token_purchases(token_id: int):
    """Retourne l'historique des achats individuels d'un token."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, purchase_date, tokens_bought, purchase_price, sol_spent,
                   sol_usd_at_buy, transaction_signature
            FROM purchases
            WHERE token_id = ?
            ORDER BY purchase_date ASC
        """, (token_id,))
        rows = cursor.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            sol_usd = d.get('sol_usd_at_buy') or 0
            price_sol = d.get('purchase_price') or 0
            d['price_usd'] = price_sol * sol_usd if sol_usd else price_sol
            d['total_usd'] = (d.get('sol_spent') or 0) * sol_usd if sol_usd else (d.get('sol_spent') or 0)
            result.append(d)
        return result

@app.get("/api/purchases")
async def get_all_purchases(wallet: Optional[str] = Query(None)):
    """Retourne tous les achats groupés par token_id pour un wallet (un seul appel)."""
    with get_db() as conn:
        cursor = conn.cursor()
        if wallet:
            cursor.execute("""
                SELECT p.token_id, p.purchase_date, p.tokens_bought, p.purchase_price,
                       p.sol_spent, p.sol_usd_at_buy, p.transaction_signature
                FROM purchases p
                WHERE p.wallet_address = ?
                ORDER BY p.token_id, p.purchase_date ASC
            """, (wallet,))
        else:
            cursor.execute("""
                SELECT p.token_id, p.purchase_date, p.tokens_bought, p.purchase_price,
                       p.sol_spent, p.sol_usd_at_buy, p.transaction_signature
                FROM purchases p
                ORDER BY p.token_id, p.purchase_date ASC
            """)
        rows = cursor.fetchall()
        result = {}
        for r in rows:
            d = dict(r)
            sol_usd = d.get('sol_usd_at_buy') or 0
            price_sol = d.get('purchase_price') or 0
            d['price_usd'] = price_sol * sol_usd if sol_usd else price_sol
            d['total_usd'] = (d.get('sol_spent') or 0) * sol_usd if sol_usd else (d.get('sol_spent') or 0)
            tid = d.pop('token_id')
            result.setdefault(tid, []).append(d)
        return result

@app.post("/api/tokens", response_model=Token)
async def create_token(token: Token):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'wallet_address'")
        wr = cursor.fetchone()
        wallet_a = (wr and wr["value"] and str(wr["value"]).strip()) or ""
        try:
            cursor.execute("""
                INSERT INTO tokens (
                    name, address, detection_date, comments, event, mcap_target,
                    purchase_date, current_tokens, purchased_tokens, purchase_price,
                    current_price, loss, gain, current_value, invested_amount, sold_tokens,
                    wallet_address
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                token.name, token.address, token.detection_date, token.comments,
                token.event, token.mcap_target, token.purchase_date, token.current_tokens,
                token.purchased_tokens, token.purchase_price, token.current_price,
                token.loss, token.gain, token.current_value, token.invested_amount,
                token.sold_tokens, wallet_a,
            ))
            conn.commit()
            token.id = cursor.lastrowid
            # Invalider le cache du dashboard
            _invalidate_dashboard_cache()
            return token
        except Exception as e:
            if is_unique_constraint_error(e):
                raise HTTPException(status_code=400, detail="Cette adresse existe déjà")
            raise

@app.put("/api/tokens/{token_id}", response_model=Token)
async def update_token(token_id: int, token: Token):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE tokens SET
                name=?, address=?, detection_date=?, comments=?, event=?, mcap_target=?,
                purchase_date=?, current_tokens=?, purchased_tokens=?, purchase_price=?,
                current_price=?, loss=?, gain=?, current_value=?, invested_amount=?,
                sold_tokens=?, user_position_cost_usd=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (
            token.name, token.address, token.detection_date, token.comments,
            token.event, token.mcap_target, token.purchase_date, token.current_tokens,
            token.purchased_tokens, token.purchase_price, token.current_price,
            token.loss, token.gain, token.current_value, token.invested_amount,
            token.sold_tokens, token.user_position_cost_usd, token_id
        ))
        conn.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Token non trouvé")
        # Invalider le cache du dashboard
        _invalidate_dashboard_cache()
        token.id = token_id
        return token

@app.delete("/api/tokens/{token_id}")
async def delete_token(token_id: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT wallet_address FROM tokens WHERE id=?", (token_id,))
        wr = cursor.fetchone()
        cursor.execute("DELETE FROM tokens WHERE id=?", (token_id,))
        conn.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Token non trouvé")
        wa = wr["wallet_address"] if wr else None
        if wa:
            _invalidate_wallet_hifo_cache(conn, wa)
        _invalidate_dashboard_cache(wa if wa else None)
        return {"message": "Token supprimé"}

# Ventes
@app.post("/api/sales")
async def add_sale(sale: Sale):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT wallet_address FROM tokens WHERE id = ?", (sale.token_id,))
        tw = cursor.fetchone()
        cursor.execute("""
            INSERT INTO sales (token_id, sale_date, tokens_sold, sale_price, sale_amount)
            VALUES (?, ?, ?, ?, ?)
        """, (sale.token_id, sale.sale_date, sale.tokens_sold, sale.sale_price, sale.sale_amount))
        conn.commit()
        if tw and tw["wallet_address"]:
            _invalidate_wallet_hifo_cache(conn, tw["wallet_address"])
        _invalidate_dashboard_cache(tw["wallet_address"] if tw and tw["wallet_address"] else None)
        return {"message": "Vente enregistrée", "id": cursor.lastrowid}

@app.get("/api/sales/{token_id}")
async def get_sales(token_id: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sales WHERE token_id=? ORDER BY sale_date DESC", (token_id,))
        sales = cursor.fetchall()
        return [dict(sale) for sale in sales]

@app.get("/api/tokens/{token_id}/transactions")
async def get_token_transactions(token_id: int):
    """
    Retourne tous les achats et ventes d'un token avec le gain/perte HIFO par vente.
    """
    try:
        sol_usd = await _get_sol_usd_price()
    except Exception:
        sol_usd = 0

    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT wallet_address FROM tokens WHERE id = ?",
            (token_id,),
        )
        tw = cursor.fetchone()
        wa = (tw["wallet_address"] or "").strip() if tw else ""

        # ── Achats ──────────────────────────────────────────────────────
        cursor.execute("""
            SELECT id, purchase_date as tx_date, tokens_bought as token_amount,
                   purchase_price as price_sol, sol_spent as sol_amount,
                   sol_usd_at_buy, COALESCE(purchase_timestamp, 0) as tx_timestamp,
                   transaction_signature
            FROM purchases
            WHERE token_id = ?
            ORDER BY purchase_date ASC
        """, (token_id,))
        buys_raw = [dict(r) for r in cursor.fetchall()]

        # ── Ventes ──────────────────────────────────────────────────────
        cursor.execute("""
            SELECT id as sale_id, sale_date as tx_date, tokens_sold as token_amount,
                   sale_price as price_sol, sol_received as sol_amount,
                   sol_usd_at_sale, COALESCE(sale_timestamp, 0) as sale_ts,
                   COALESCE(sale_slot, 0) as sale_slot,
                   COALESCE(sale_timestamp, 0) as tx_timestamp,
                   transaction_signature
            FROM sales
            WHERE token_id = ?
            ORDER BY sale_date ASC
        """, (token_id,))
        sells_raw = [dict(r) for r in cursor.fetchall()]

        # ── PnL HIFO : même moteur que le dashboard et « toutes les ventes »
        gain_per_sale: dict = {}
        if wa:
            gain_per_sale = _compute_hifo_gain_per_sale(cursor, wa, sol_usd)
        else:
            cursor.execute("""
                SELECT purchase_timestamp, purchase_date, COALESCE(purchase_slot, 0) as purchase_slot,
                       tokens_bought, sol_spent,
                       COALESCE(NULLIF(sol_usd_at_buy, 0), ?) as sol_rate_buy
                FROM purchases
                WHERE token_id = ? AND tokens_bought > 0 AND sol_spent > 0
                ORDER BY (sol_spent / tokens_bought) * COALESCE(NULLIF(sol_usd_at_buy, 0), ?) DESC,
                         purchase_timestamp ASC
            """, (sol_usd, token_id, sol_usd))
            lots = []
            for p in cursor.fetchall():
                lots.append({
                    "ts":           _lot_ts_for_hifo(p.get("purchase_timestamp"), p.get("purchase_date")),
                    "slot":         _row_get(p, "purchase_slot", 0) or 0,
                    "remaining":    p["tokens_bought"],
                    "tokens_total": p["tokens_bought"],
                    "sol_spent":    p["sol_spent"],
                    "sol_rate_buy": p["sol_rate_buy"],
                    "price_usd":    (p["sol_spent"] / p["tokens_bought"]) * p["sol_rate_buy"] if p["tokens_bought"] else 0,
                })
            for s in sells_raw:
                if not s.get("sale_date") and s.get("tx_date"):
                    s["sale_date"] = s["tx_date"]
            sells_chrono = sorted(sells_raw, key=_sale_sort_key_for_hifo)
            for sale in sells_chrono:
                tokens_left = sale["token_amount"] or 0
                sale_ts_c = _sale_ts_ceiling_for_hifo(sale.get("sale_ts"), sale.get("sale_date") or sale.get("tx_date"))
                sale_slot   = sale.get("sale_slot", 0) or 0
                sol_rate_s  = sale.get("sol_usd_at_sale") or sol_usd
                sell_usd    = (sale["sol_amount"] or 0) * sol_rate_s
                eligible = sorted(
                    [l for l in lots if _lot_eligible_for_sale(l["ts"], l.get("slot", 0), sale_ts_c, sale_slot)],
                    key=lambda l: l["price_usd"], reverse=True
                )
                buy_usd_cost = 0.0
                for lot in eligible:
                    if tokens_left <= 0:
                        break
                    consume = min(lot["remaining"], tokens_left)
                    ratio   = consume / lot["tokens_total"] if lot["tokens_total"] else 0
                    buy_usd_cost    += lot["sol_spent"] * ratio * lot["sol_rate_buy"]
                    lot["remaining"] -= consume
                    tokens_left      -= consume
                profit = sell_usd - buy_usd_cost
                gain_per_sale[sale["sale_id"]] = {
                    "sell_usd": round(sell_usd, 4),
                    "buy_usd":  round(buy_usd_cost, 4),
                    "pnl_usd":  round(profit, 4),
                }

        # ── Construction du résultat ─────────────────────────────────────
        result = []
        for b in buys_raw:
            sol_rate = b.get("sol_usd_at_buy") or sol_usd
            result.append({
                "tx_type":      "buy",
                "tx_date":      b["tx_date"],
                "tx_timestamp": b.get("tx_timestamp") or 0,
                "token_amount": b["token_amount"],
                "price_usd":    (b["price_sol"] or 0) * sol_rate,
                "amount_usd":   (b["sol_amount"] or 0) * sol_rate,
                "pnl_usd":      None,
                "cost_usd":     None,
                "signature":    b.get("transaction_signature"),
            })
        for s in sells_raw:
            sol_rate = s.get("sol_usd_at_sale") or sol_usd
            pnl = gain_per_sale.get(s["sale_id"], {})
            result.append({
                "tx_type":      "sell",
                "tx_date":      s["tx_date"],
                "tx_timestamp": s.get("tx_timestamp") or 0,
                "token_amount": s["token_amount"],
                "price_usd":    (s["price_sol"] or 0) * sol_rate,
                "amount_usd":   (s["sol_amount"] or 0) * sol_rate,
                "pnl_usd":      pnl.get("pnl_usd"),
                "cost_usd":     pnl.get("buy_usd"),
                "signature":    s.get("transaction_signature"),
            })

        # ✅ CORRECTION: Tri par timestamp (chronologie précise)
        result.sort(key=lambda x: x.get("tx_timestamp") or 0, reverse=True)
        return result

@app.get("/api/all-sales")
async def get_all_sales():
    """Récupère toutes les ventes avec les informations des tokens"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.*, t.name as token_name
            FROM sales s
            JOIN tokens t ON s.token_id = t.id
            ORDER BY s.sale_date DESC
        """)
        sales = cursor.fetchall()
        result = []
        for sale in sales:
            s = dict(sale)
            sol_usd_at_sale = s.get('sol_usd_at_sale')
            sol_recv = s.get('sol_received') or s.get('sale_amount') or 0
            if sol_usd_at_sale:  # Vente Helius — montants en SOL
                s['sale_amount_usd'] = sol_recv * sol_usd_at_sale
                s['sale_price_usd'] = (s.get('sale_price') or 0) * sol_usd_at_sale
            else:  # Vente manuelle — montants déjà en USD
                s['sale_amount_usd'] = s.get('sale_amount') or 0
                s['sale_price_usd'] = s.get('sale_price') or 0
            result.append(s)
        return result

# Wallet Solana endpoints

# Sauvegarde/lecture de l'adresse wallet active en BDD
@app.get("/api/settings/wallet")
async def get_wallet_setting():
    """Récupère l'adresse wallet sauvegardée en base"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = 'wallet_address'")
        row = cursor.fetchone()
        return {"wallet_address": row["value"] if row else None}

@app.post("/api/settings/wallet")
async def save_wallet_setting(data: dict):
    """Sauvegarde l'adresse wallet active en base"""
    address = data.get("wallet_address", "").strip()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO settings (key, value) VALUES ('wallet_address', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (address,)
        )
        conn.commit()
    # Enregistrer le wallet dans la table wallets pour multi-wallet
    if address:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO wallets (address, label) VALUES (?, ?)",
                (address, f"Wallet {address[:6]}...{address[-4:]}")
            )
            conn.commit()
    # Invalider le cache du dashboard pour ce wallet et les anciens wallets
    _invalidate_dashboard_cache()  # Efface tout le cache pour être sûr
    return {"status": "ok", "wallet_address": address}


@app.get("/api/settings/reference-capital")
async def get_reference_capital_setting(wallet: str = Query(..., description="Adresse Solana")):
    """Total dépensé manuel en USD. 0 = non défini (carte à 0 $ jusqu’à saisie)."""
    w = (wallet or "").strip()
    if len(w) < 20:
        raise HTTPException(status_code=400, detail="Adresse wallet invalide")
    with get_db() as conn:
        v = _get_reference_capital_usd(conn, w)
    return {"wallet_address": w, "amount_usd": v}


@app.post("/api/settings/reference-capital")
async def set_reference_capital_setting(body: ReferenceCapitalBody):
    """Définit le total dépensé (USD) ou efface (amount_usd = 0). Valeur fixe jusqu’à prochaine modification ou POST /add."""
    w = (body.wallet_address or "").strip()
    if len(w) < 20:
        raise HTTPException(status_code=400, detail="Adresse wallet invalide")
    amt = float(body.amount_usd or 0)
    if amt < 0:
        raise HTTPException(status_code=400, detail="Le montant doit être ≥ 0")
    with get_db() as conn:
        c = conn.cursor()
        if amt <= 0:
            c.execute("DELETE FROM wallet_reference_capital WHERE wallet_address = ?", (w,))
        else:
            c.execute(
                """
                INSERT INTO wallet_reference_capital (wallet_address, amount_usd, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(wallet_address) DO UPDATE SET
                    amount_usd = excluded.amount_usd,
                    updated_at = datetime('now')
                """,
                (w, amt),
            )
        conn.commit()
    _invalidate_dashboard_cache(w)
    return {"ok": True, "wallet_address": w, "amount_usd": amt if amt > 0 else 0.0}


@app.post("/api/settings/reference-capital/add")
async def add_reference_capital_setting(body: ReferenceCapitalAddBody):
    """Ajoute un montant au total dépensé (ex. nouveau dépôt de 50 $)."""
    w = (body.wallet_address or "").strip()
    if len(w) < 20:
        raise HTTPException(status_code=400, detail="Adresse wallet invalide")
    add = float(body.add_usd or 0)
    if add <= 0:
        raise HTTPException(status_code=400, detail="Le montant à ajouter doit être > 0")
    with get_db() as conn:
        cur = _get_reference_capital_usd(conn, w)
        new_amt = cur + add
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO wallet_reference_capital (wallet_address, amount_usd, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(wallet_address) DO UPDATE SET
                amount_usd = excluded.amount_usd,
                updated_at = datetime('now')
            """,
            (w, new_amt),
        )
        conn.commit()
    _invalidate_dashboard_cache(w)
    return {"ok": True, "wallet_address": w, "amount_usd": new_amt, "added_usd": add}


# --- Compte local (pseudo + mot de passe) + adresses suivies ---
@app.post("/api/auth/register")
async def auth_register(body: AuthRegisterBody):
    with get_db() as conn:
        uid, err = auth_service.register_user(conn, body.username, body.password)
        if err:
            raise HTTPException(status_code=400, detail=err)
        token = auth_service.create_session(conn, uid)
        c = conn.cursor()
        c.execute("SELECT username FROM users WHERE id = ?", (uid,))
        un = c.fetchone()["username"]
    return {"token": token, "username": un}


@app.post("/api/auth/login")
async def auth_login(body: AuthLoginBody):
    with get_db() as conn:
        uid, err = auth_service.verify_login(conn, body.username, body.password)
        if err:
            raise HTTPException(status_code=401, detail=err)
        token = auth_service.create_session(conn, uid)
        c = conn.cursor()
        c.execute("SELECT username FROM users WHERE id = ?", (uid,))
        un = c.fetchone()["username"]
    return {"token": token, "username": un}


@app.get("/api/auth/me")
async def auth_me(user: dict = Depends(_require_auth_user)):
    with get_db() as conn:
        wallets = auth_service.list_saved_wallets(conn, user["id"])
    return {
        "username": user["username"],
        "active_wallet": user.get("active_wallet_address"),
        "wallets": wallets,
    }


@app.post("/api/auth/logout")
async def auth_logout(authorization: Optional[str] = Header(None)):
    token = _parse_bearer_token(authorization)
    if token:
        with get_db() as conn:
            auth_service.delete_session(conn, token)
    return {"ok": True}


@app.post("/api/auth/wallets")
async def auth_save_wallet_route(body: AuthSavedWalletBody, user: dict = Depends(_require_auth_user)):
    addr = (body.address or "").strip()
    if len(addr) < 32:
        raise HTTPException(status_code=400, detail="Adresse invalide")
    fl = True if body.follows is None else bool(body.follows)
    with get_db() as conn:
        auth_service.add_saved_wallet(conn, user["id"], addr, body.label, fl)
    return {"ok": True}


@app.patch("/api/auth/wallets")
async def auth_patch_wallet_route(body: AuthPatchWalletBody, user: dict = Depends(_require_auth_user)):
    addr = (body.address or "").strip()
    if len(addr) < 32:
        raise HTTPException(status_code=400, detail="Adresse invalide")
    raw = body.model_dump(exclude_unset=True)
    raw.pop("address", None)
    if not raw:
        raise HTTPException(status_code=400, detail="Rien à modifier (label ou follows)")
    with get_db() as conn:
        ok = auth_service.patch_saved_wallet(conn, user["id"], addr, raw)
    if not ok:
        raise HTTPException(status_code=404, detail="Adresse non trouvée sur ce compte")
    return {"ok": True}


@app.post("/api/auth/wallets/sync-done")
async def auth_wallet_sync_done_route(body: AuthWalletSyncDoneBody, user: dict = Depends(_require_auth_user)):
    addr = (body.address or "").strip()
    if len(addr) < 32:
        raise HTTPException(status_code=400, detail="Adresse invalide")
    with get_db() as conn:
        if not auth_service.user_owns_saved_wallet(conn, user["id"], addr):
            raise HTTPException(status_code=404, detail="Adresse non enregistrée sur ce compte")
        auth_service.mark_saved_wallet_synced(conn, user["id"], addr)
    return {"ok": True}


@app.delete("/api/auth/wallets")
async def auth_delete_saved_wallet(
    address: str = Query(..., description="Adresse Solana à retirer de la liste"),
    user: dict = Depends(_require_auth_user),
):
    with get_db() as conn:
        ok = auth_service.remove_saved_wallet(conn, user["id"], address)
    return {"ok": ok}


@app.post("/api/auth/set-active-wallet")
async def auth_set_active_wallet_route(body: AuthSetActiveBody, user: dict = Depends(_require_auth_user)):
    addr = (body.address or "").strip()
    if len(addr) < 32:
        raise HTTPException(status_code=400, detail="Adresse invalide")
    with get_db() as conn:
        auth_service.set_active_wallet(conn, user["id"], addr)
        auth_service.add_saved_wallet(conn, user["id"], addr, None)
    return {"ok": True}


@app.get("/api/wallets")
async def list_wallets():
    """Liste tous les wallets enregistrés (multi-wallet)."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT address, label FROM wallets ORDER BY created_at DESC")
        rows = cursor.fetchall()
        # Ajouter aussi le wallet actif depuis settings
        cursor.execute("SELECT value FROM settings WHERE key = 'wallet_address'")
        active = cursor.fetchone()
        active_addr = active["value"] if active and active["value"] else None
        result = [{"address": r["address"], "label": r["label"], "active": r["address"] == active_addr} for r in rows]
        if active_addr and not any(r["address"] == active_addr for r in result):
            result.insert(0, {"address": active_addr, "label": f"Actif: {active_addr[:6]}...", "active": True})
        return result


@app.get("/api/audit")
async def audit_data(wallet: Optional[str] = Query(None)):
    """
    Contrôles d'intégrité : soldes négatifs, doublons de signatures, trous.
    Source de vérité : blockchain > BDD ; les achats/ventes doivent être cohérents.
    """
    issues = []
    with get_db() as conn:
        cursor = conn.cursor()
        wallet_filter = "AND wallet_address = ?" if wallet else ""
        params = (wallet,) if wallet else ()

        # 1. Soldes négatifs (current_tokens < 0)
        cursor.execute(f"SELECT id, name, address, current_tokens FROM tokens WHERE current_tokens < 0 {wallet_filter}", params)
        for r in cursor.fetchall():
            issues.append({"type": "negative_balance", "token_id": r["id"], "name": r["name"], "current_tokens": r["current_tokens"]})

        # 2. Doublons (même signature + même token) sur purchases — plusieurs lignes identiques
        cursor.execute("""
            SELECT transaction_signature, token_id, COUNT(*) as cnt FROM purchases
            WHERE transaction_signature IS NOT NULL AND transaction_signature != ''
            GROUP BY transaction_signature, token_id HAVING cnt > 1
        """)
        for r in cursor.fetchall():
            issues.append({
                "type": "duplicate_purchase_sig",
                "signature": r["transaction_signature"],
                "token_id": r["token_id"],
                "count": r["cnt"],
            })

        # 3. Doublons (même signature + même token) sur sales
        cursor.execute("""
            SELECT s.transaction_signature, s.token_id, COUNT(*) as cnt FROM sales s
            JOIN tokens t ON s.token_id = t.id
            WHERE s.transaction_signature IS NOT NULL AND s.transaction_signature != ''
            """ + (f" AND t.wallet_address = ?" if wallet else "") + """
            GROUP BY s.transaction_signature, s.token_id HAVING cnt > 1
        """, params if wallet else ())
        for r in cursor.fetchall():
            issues.append({
                "type": "duplicate_sale_sig",
                "signature": r["transaction_signature"],
                "token_id": r["token_id"],
                "count": r["cnt"],
            })

        # 4. Tokens sans wallet (si multi-wallet activé)
        if wallet:
            cursor.execute("SELECT id, name FROM tokens WHERE (wallet_address IS NULL OR wallet_address = '') AND id IN (SELECT DISTINCT token_id FROM purchases WHERE wallet_address = ?)", (wallet,))
            for r in cursor.fetchall():
                issues.append({"type": "token_missing_wallet", "token_id": r["id"], "name": r["name"]})

    return {"ok": len(issues) == 0, "issues": issues, "count": len(issues)}


WIPE_DB_CONFIRM_PHRASE = "EFFACER_TOUTES_LES_DONNEES"


@app.post("/api/database/wipe-all-data")
async def wipe_all_database_endpoint(
    body: DatabaseWipeBody,
    vacuum: bool = Query(False, description="VACUUM après coup (réduit la taille du fichier .db, plus lent)."),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """
    Vide toutes les tables applicatives (tokens, achats, ventes, auth, réglages, caches…).
    Les CREATE TABLE et index restent ; seules les lignes sont supprimées. Compteurs AUTOINCREMENT remis à zéro.
    """
    if (body.confirm or "").strip() != WIPE_DB_CONFIRM_PHRASE:
        raise HTTPException(
            status_code=400,
            detail=f'Confirmation invalide : JSON {{"confirm": "{WIPE_DB_CONFIRM_PHRASE}"}}',
        )
    if IS_PROD and API_KEY and (x_api_key or "").strip() != API_KEY:
        raise HTTPException(
            status_code=401,
            detail=f"En production, X-API-Key doit correspondre au secret {ENV_NAME_SERVICE_API_KEY} (Render / .env).",
        )

    with get_db() as conn:
        cleared = wipe_all_database_data(conn)

    if vacuum and not USE_POSTGRES:
        vconn = sqlite3.connect(DB_PATH, timeout=120)
        try:
            vconn.execute("VACUUM")
            vconn.commit()
        finally:
            vconn.close()

    _invalidate_dashboard_cache(None)
    _invalidate_charts_cache()

    return {
        "ok": True,
        "message": "Base vidée — schéma et index conservés.",
        "tables_cleared": cleared,
        "vacuum": vacuum,
    }


@app.get("/api/export/csv")
async def export_csv(wallet: Optional[str] = Query(None)):
    """Export CSV : tokens, positions, PnL HIFO (gain/loss alignés dashboard)."""
    try:
        sol_usd = await _get_sol_usd_price()
    except RuntimeError:
        sol_usd = 150.0
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["Token", "Address", "Current Tokens", "Current Value USD", "Invested USD", "Gain/Loss USD (HIFO)", "ROI %"])
    with get_db() as conn:
        cursor = conn.cursor()
        if wallet:
            cursor.execute("""
                SELECT id, name, address, current_tokens, current_value, invested_amount, gain, loss
                FROM tokens WHERE wallet_address = ?
            """, (wallet,))
        else:
            cursor.execute(
                "SELECT id, name, address, current_tokens, current_value, invested_amount, gain, loss FROM tokens"
            )
        rows = [dict(x) for x in cursor.fetchall()]
        hifo_by_id: dict = {}
        if wallet and str(wallet).strip():
            hifo_by_id = _hifo_per_token_gain_loss_dict(cursor, str(wallet).strip(), sol_usd)
        for r in rows:
            inv = r["invested_amount"] or 0
            hid = hifo_by_id.get(int(r["id"]))
            if hid is not None:
                pnl = hid["net"]
            else:
                pnl = (r["gain"] or 0) - (r["loss"] or 0)
                g, lo = r.get("gain") or 0, r.get("loss") or 0
            roi = (pnl / inv * 100) if inv else 0
            w.writerow([
                r["name"] or "", r["address"] or "",
                r["current_tokens"] or 0, r["current_value"] or 0, inv, pnl, round(roi, 2)
            ])
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=portfolio_export.csv"}
    )


@app.get("/api/tokens/{token_id}/targets")
async def get_token_targets(token_id: int):
    """Objectifs TP/SL et alertes pour un token."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM token_targets WHERE token_id = ?", (token_id,))
        return [dict(r) for r in cursor.fetchall()]


class TokenTargetCreate(BaseModel):
    mcap_target: Optional[str] = None
    tp_price: Optional[float] = None
    sl_price: Optional[float] = None
    alert_enabled: bool = True


@app.post("/api/tokens/{token_id}/targets")
async def create_token_target(token_id: int, data: TokenTargetCreate, wallet: Optional[str] = Query(None)):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO token_targets (token_id, wallet_address, mcap_target, tp_price, sl_price, alert_enabled)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (token_id, wallet, data.mcap_target, data.tp_price, data.sl_price, 1 if data.alert_enabled else 0))
        conn.commit()
        return {"id": cursor.lastrowid, "status": "ok"}


@app.get("/api/tokens/{token_id}/notes")
async def get_token_notes(token_id: int):
    """Journal de décisions / notes par token."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM token_notes WHERE token_id = ? ORDER BY note_date DESC", (token_id,))
        return [dict(r) for r in cursor.fetchall()]


class TokenNoteCreate(BaseModel):
    note_date: str
    content: str
    event_type: Optional[str] = None


@app.post("/api/tokens/{token_id}/notes")
async def create_token_note(token_id: int, data: TokenNoteCreate):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO token_notes (token_id, note_date, content, event_type) VALUES (?, ?, ?, ?)",
            (token_id, data.note_date, data.content, data.event_type or "note")
        )
        conn.commit()
        return {"id": cursor.lastrowid, "status": "ok"}


@app.post("/api/refresh-prices-only")
async def refresh_prices_only(wallet: Optional[str] = Query(None)):
    """Refresh partiel : uniquement les prix (sans import blockchain)."""
    await quick_refresh_prices(wallet)
    _invalidate_dashboard_cache(wallet)
    _invalidate_charts_cache()
    return {"status": "ok", "message": "Prix mis à jour"}


@app.post("/api/fix-wallet/{wallet_address}")
async def fix_wallet_data(wallet_address: str):
    """
    Corrige les soldes : recalc depuis purchases/sales → sync on-chain → HIFO → prix.
    """
    _invalidate_dashboard_cache(wallet_address)
    with get_db() as conn:
        _invalidate_wallet_hifo_cache(conn, wallet_address)
        conn.execute("""
            UPDATE tokens SET
                purchased_tokens = COALESCE(
                    (SELECT SUM(tokens_bought) FROM purchases WHERE token_id = tokens.id),
                    purchased_tokens
                ),
                sold_tokens = COALESCE(
                    (SELECT SUM(tokens_sold) FROM sales WHERE token_id = tokens.id),
                    0
                )
            WHERE wallet_address = ?
        """, (wallet_address,))
        conn.execute("""
            UPDATE tokens SET current_tokens = MAX(0.0,
                COALESCE(purchased_tokens, 0) - COALESCE(sold_tokens, 0)
            )
            WHERE wallet_address = ?
        """, (wallet_address,))
        conn.commit()
    try:
        await sync_balances_from_chain(wallet_address)
    except Exception:
        pass
    await recalculate_history(wallet_address)
    await update_all_prices(wallet_address)
    _invalidate_dashboard_cache(wallet_address)
    return {"status": "ok", "wallet": wallet_address, "message": "Données corrigées"}


async def quick_refresh_prices(wallet: Optional[str] = None):
    """Met à jour les prix sans import Helius."""
    with get_db() as conn:
        cursor = conn.cursor()
        wallet_filter = "AND wallet_address = ?" if wallet else ""
        cursor.execute(f"SELECT id, address FROM tokens WHERE 1=1 {wallet_filter}", (wallet,) if wallet else ())
        tokens = cursor.fetchall()
    for t in tokens:
        try:
            price_data = await get_token_price(t["address"])
            price = float(price_data.get("price") or 0)
            if price > 0:
                with get_db() as conn:
                    c = conn.cursor()
                    c.execute("UPDATE tokens SET current_price = ?, current_value = current_tokens * ? WHERE id = ?",
                              (price, price, t["id"]))
                    conn.commit()
        except Exception:
            pass


# SOL « enveloppé » (WSOL) : même valeur que le natif mais compte SPL — invisible pour getBalance RPC.
def _wsol_amount_from_helius_tokens(tokens: list) -> float:
    """Somme des soldes WSOL (tous les ATA du mint officiel)."""
    total = 0.0
    for t in tokens or []:
        if t.get("mint") != SOL_MINT:
            continue
        try:
            dec = int(t.get("decimals") if t.get("decimals") is not None else 9)
            raw = int(t.get("amount", 0) or 0)
            total += raw / (10**dec) if dec else float(raw)
        except (TypeError, ValueError):
            continue
    return total


async def _fetch_wallet_sol_balance(wallet_address: str) -> float:
    """
    Solde SOL affiché = natif + WSOL (wrapped).
    getBalance RPC seul sous-estime fortement car le SOL est souvent en WSOL (swaps, DeFi).
    """
    # 1) Helius balances (natif + tokens) — aligné sur sync-balances
    if HELIUS_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                # HELIUS_BASE défini plus bas dans le module ; OK à l’exécution
                r = await client.get(
                    f"{HELIUS_BASE}/addresses/{wallet_address}/balances",
                    params={"api-key": HELIUS_API_KEY},
                )
                if r.status_code == 200:
                    data = r.json()
                    native = float(data.get("nativeBalance", 0) or 0) / LAMPORTS_PER_SOL
                    wsol = _wsol_amount_from_helius_tokens(data.get("tokens", []))
                    return native + wsol
        except Exception:
            pass
    # 2) Fallback : RPC natif uniquement
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            rpc_urls = []
            if HELIUS_API_KEY:
                rpc_urls.append(f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
            rpc_urls += ["https://api.mainnet-beta.solana.com", "https://rpc.ankr.com/solana"]
            payload = {"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [wallet_address]}
            for url in rpc_urls:
                try:
                    r = await client.post(url, json=payload)
                    if r.status_code == 200 and "result" in r.json():
                        lamports = r.json().get("result", {}).get("value", 0)
                        return lamports / 1_000_000_000
                except Exception:
                    continue
    except Exception:
        pass
    return 0.0


@app.get("/api/wallet-sol-balance/{wallet_address}")
async def get_wallet_sol_balance(wallet_address: str):
    """Récupère le solde SOL d'un wallet via l'API Solana RPC"""
    balance = await _fetch_wallet_sol_balance(wallet_address)
    return {"wallet": wallet_address, "balance": balance}

# Cache en mémoire pour les prix historiques SOL (date → float)
_sol_history_cache: dict[str, float] = {}
# Limite la concurrence : évite des centaines d’appels Binance/Kraken en parallèle (timeouts / 4+ min).
_sol_history_fetch_sem = asyncio.Semaphore(16)


async def _get_sol_usd_at_date(date_str: str) -> float:
    """
    Retourne le prix SOL/USD à une date donnée (format YYYY-MM-DD).
    Sources dans l'ordre :
    1. Binance OHLCV SOLUSDT (gratuit, sans clé, données depuis 2020)
    2. Kraken OHLCV SOLUSD (gratuit, sans clé)
    3. CoinGecko /history (peut bloquer >1 an sur le plan gratuit)
    Met en cache pour éviter des appels répétés pour la même date.
    Si toutes les sources échouent, retourne 0.0.
    """
    if date_str in _sol_history_cache:
        return _sol_history_cache[date_str]

    d = datetime.strptime(date_str, "%Y-%m-%d")
    # Timestamps en millisecondes pour Binance (début et fin de journée UTC)
    start_ms = int(datetime(d.year, d.month, d.day, 0, 0, 0).timestamp() * 1000)
    end_ms   = int(datetime(d.year, d.month, d.day, 23, 59, 59).timestamp() * 1000)

    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1) Binance — SOLUSDT klines journalières (close price)
        try:
            resp = await client.get(
                "https://api.binance.com/api/v3/klines",
                params={
                    "symbol": "SOLUSDT",
                    "interval": "1d",
                    "startTime": start_ms,
                    "endTime": end_ms,
                    "limit": 1,
                }
            )
            if resp.status_code == 200:
                klines = resp.json()
                if klines and len(klines) > 0:
                    price = float(klines[0][4])  # index 4 = close price
                    if price > 0:
                        _sol_history_cache[date_str] = price
                        return price
        except Exception:
            pass

        # 2) Kraken — SOLUSD OHLC (since = timestamp Unix)
        try:
            since = int(d.timestamp())
            resp = await client.get(
                "https://api.kraken.com/0/public/OHLC",
                params={"pair": "SOLUSD", "interval": 1440, "since": since - 86400}
            )
            if resp.status_code == 200:
                result = resp.json().get("result", {})
                rows = result.get("SOLUSD") or result.get("XSOLUSD") or []
                target_day = d.strftime("%Y-%m-%d")
                for row in rows:
                    row_date = datetime.fromtimestamp(row[0], timezone.utc).strftime("%Y-%m-%d")
                    if row_date == target_day:
                        price = float(row[4])  # close price
                        if price > 0:
                            _sol_history_cache[date_str] = price
                            return price
        except Exception:
            pass

        # 2b) Binance — fenêtre élargie (plusieurs bougies 1d) si le créneau exact n’a rien renvoyé
        try:
            start_wide = start_ms - 4 * 86400000
            resp = await client.get(
                "https://api.binance.com/api/v3/klines",
                params={
                    "symbol": "SOLUSDT",
                    "interval": "1d",
                    "startTime": start_wide,
                    "limit": 10,
                },
            )
            if resp.status_code == 200:
                for k in resp.json() or []:
                    if not k:
                        continue
                    ot = int(k[0])
                    day_str = datetime.fromtimestamp(ot / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                    if day_str == date_str:
                        price = float(k[4])
                        if price > 0:
                            _sol_history_cache[date_str] = price
                            return price
        except Exception:
            pass

        # 3) CoinGecko — fallback (bloque parfois >1 an sur plan gratuit)
        try:
            cg_date = d.strftime("%d-%m-%Y")
            resp = await client.get(
                "https://api.coingecko.com/api/v3/coins/solana/history",
                params={"date": cg_date, "localization": "false"}
            )
            if resp.status_code == 200:
                price = float(resp.json()["market_data"]["current_price"]["usd"])
                if price > 0:
                    _sol_history_cache[date_str] = price
                    return price
        except Exception:
            pass

        # 3b) CoinGecko — market_chart/range sur 24h UTC (souvent OK quand /history bloque)
        try:
            d_utc = d.replace(tzinfo=timezone.utc)
            frm = int(d_utc.timestamp())
            to = frm + 86400
            resp = await client.get(
                "https://api.coingecko.com/api/v3/coins/solana/market_chart/range",
                params={"vs_currency": "usd", "from": frm, "to": to},
            )
            if resp.status_code == 200:
                prices = resp.json().get("prices") or []
                if prices:
                    avg = sum(float(p[1]) for p in prices) / len(prices)
                    if avg > 0:
                        _sol_history_cache[date_str] = avg
                        return avg
        except Exception:
            pass

    return 0.0  # inconnu — on ne stocke pas de faux prix


async def _get_sol_usd_price() -> float:
    """
    Prix SOL/USD avec cache 30min + appels API parallélisés (timeout court 3s).
    - Cache: expiré après 30 min (1800s) - minimise appels externes
    - Fallback: last_valid_price (ultra rapide si API échouent)
    - Timeouts: réduits à 3s par API (abandon rapide)
    - Parallélisation: 3 sources en même temps
    """
    global _sol_price_cache
    
    # Vérifier le cache d'abord (très rapide)
    now = time.time()
    if _sol_price_cache["price"] is not None and (now - _sol_price_cache["timestamp"]) < _sol_price_cache["ttl"]:
        return _sol_price_cache["price"]
    
    async with _sol_price_lock:
        # Double-check après avoir acquis le lock
        now = time.time()
        if _sol_price_cache["price"] is not None and (now - _sol_price_cache["timestamp"]) < _sol_price_cache["ttl"]:
            return _sol_price_cache["price"]
        
        async def _fetch_coingecko():
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    r = await client.get(
                        "https://api.coingecko.com/api/v3/simple/price",
                        params={"ids": "solana", "vs_currencies": "usd"},
                    )
                    if r.status_code == 200:
                        price = float(r.json()["solana"]["usd"])
                        if 5 < price < 10000:
                            return price
            except Exception:
                pass
            return None
        
        async def _fetch_jupiter():
            try:
                async with httpx.AsyncClient(timeout=3.0, headers=_jupiter_price_headers()) as client:
                    bases: list[str] = []
                    for b in (JUPITER_PRICE_V3_LITE, JUPITER_PRICE_V3_FALLBACK):
                        if b and b.rstrip("/") not in {x.rstrip("/") for x in bases}:
                            bases.append(b)
                    for base in bases:
                        try:
                            r = await client.get(f"{base}?ids={SOL_MINT}")
                            if r.status_code == 200:
                                parsed = _jupiter_parse_prices_json(r.json(), [SOL_MINT])
                                price = parsed.get(SOL_MINT, 0.0)
                                if 5 < price < 10000:
                                    return price
                        except Exception:
                            continue
            except Exception:
                pass
            return None
        
        async def _fetch_dexscreener():
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    r = await client.get(
                        f"https://api.dexscreener.com/latest/dex/tokens/{SOL_MINT}"
                    )
                    if r.status_code == 200:
                        pairs = [
                            p for p in (r.json().get("pairs") or [])
                            if p.get("chainId") == "solana"
                            and (p.get("quoteToken") or {}).get("symbol", "").upper() in ("USDC", "USDT")
                            and p.get("priceUsd")
                        ]
                        if pairs:
                            best = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
                            price = float(best["priceUsd"])
                            if 5 < price < 10000:
                                return price
            except Exception:
                pass
            return None
        
        # Paralléliser les 3 appels (timeout court = abandon rapide)
        results = await asyncio.gather(
            _fetch_coingecko(),
            _fetch_jupiter(),
            _fetch_dexscreener(),
            return_exceptions=True
        )

        for item in results:
            if isinstance(item, BaseException):
                continue
            if item is None:
                continue
            try:
                price = float(item)
            except (TypeError, ValueError):
                continue
            if 5 < price < 10000:
                _sol_price_cache["price"] = price
                _sol_price_cache["timestamp"] = time.time()
                _sol_price_cache["last_valid_price"] = price
                return price
        
        # Aucune source n'a répondu → retourner le dernier prix connu (ultra rapide fallback)
        return _sol_price_cache["last_valid_price"]


# Liquidité DexScreener : d’abord paires « sûres », puis relâché (meme / pump souvent < 50 $ de liq. affichée)
MIN_LIQUIDITY_USD_STRICT = 50.0
MIN_LIQUIDITY_USD_RELAXED = 5.0


def _dexscreener_pick_price_usd(pairs: list) -> tuple[float, str]:
    """
    Choisit le meilleur prix USD parmi les paires Solana.
    Essaie liquidité élevée d’abord, puis très petites pools (sinon beaucoup de memecoins à ~0 $).
    Retourne (price_usd, source_tag).
    """
    sol_pairs = [
        p for p in (pairs or [])
        if p.get("chainId") == "solana" and p.get("priceUsd")
    ]
    if not sol_pairs:
        return 0.0, ""

    def liq(p):
        try:
            return float((p.get("liquidity") or {}).get("usd") or 0)
        except (TypeError, ValueError):
            return 0.0

    for min_liq, tag in (
        (MIN_LIQUIDITY_USD_STRICT, "dexscreener"),
        (MIN_LIQUIDITY_USD_RELAXED, "dexscreener_low_liq"),
        (0.0, "dexscreener_any"),
    ):
        pool = [p for p in sol_pairs if liq(p) >= min_liq]
        if not pool:
            continue
        best = max(pool, key=liq)
        try:
            price = float(best.get("priceUsd") or 0)
        except (TypeError, ValueError):
            price = 0.0
        if price > 0:
            return price, tag
    return 0.0, ""


def _helius_token_ui_balance(t: dict) -> float:
    """Solde token depuis la réponse Helius balances (raw+decimals ou tokenAmount déjà humain)."""
    if not t:
        return 0.0
    ta = t.get("tokenAmount")
    if ta is not None and ta != "":
        try:
            v = float(ta)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    try:
        decimals = int(t.get("decimals") or 0)
    except (TypeError, ValueError):
        decimals = 0
    raw = t.get("amount", 0) or 0
    try:
        if isinstance(raw, str):
            raw_int = int(float(raw))
        else:
            raw_int = int(raw)
    except (TypeError, ValueError):
        raw_int = 0
    if decimals > 0 and raw_int >= 0:
        return raw_int / (10**decimals)
    return float(raw_int) if raw_int else 0.0


def _is_likely_network_dns_error(exc: BaseException) -> bool:
    """Échec DNS / pas de route réseau (ex. Windows [Errno 11001] getaddrinfo failed)."""
    e: Optional[BaseException] = exc
    seen: set = set()
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, OSError):
            return True
        if isinstance(e, httpx.ConnectError):
            return True
        msg = str(e).lower()
        if "getaddrinfo" in msg or "11001" in msg or "name or service not known" in msg:
            return True
        e = getattr(e, "__cause__", None) or getattr(e, "__context__", None)
    return False


async def _get_prices_batch(addresses: List[str]) -> dict:
    """
    Récupère les prix de plusieurs tokens. Priorité : Jupiter lite-api (sans clé) > Birdeye > DexScreener.
    """
    global _price_batch_cache
    result: dict = {}
    if not addresses:
        return result

    cache_key = frozenset(addresses)
    now = time.time()
    if cache_key in _price_batch_cache:
        cached = _price_batch_cache[cache_key]
        if now - cached["ts"] < _price_batch_cache_ttl:
            return dict(cached["prices"])

    # 1) Jupiter Price v3 : lite-api puis secours api.jup.ag (même schéma JSON ; timeouts plus larges pour l’hébergement)
    chunk_size = 50
    jupiter_net_logged = False
    jupiter_bases: List[str] = []
    for b in (JUPITER_PRICE_V3_LITE, JUPITER_PRICE_V3_FALLBACK):
        if b and b.rstrip("/") not in {x.rstrip("/") for x in jupiter_bases}:
            jupiter_bases.append(b)

    async def _jupiter_fetch_into(addrs: List[str], base_url: str) -> None:
        nonlocal jupiter_net_logged
        if not base_url or not addrs:
            return
        for i in range(0, len(addrs), chunk_size):
            chunk = addrs[i : i + chunk_size]
            ids_param = ",".join(chunk)
            try:
                async with httpx.AsyncClient(timeout=20.0, headers=_jupiter_price_headers()) as client:
                    r = await client.get(f"{base_url}?ids={ids_param}")
                    if r.status_code == 200:
                        for addr, p in _jupiter_parse_prices_json(r.json(), chunk).items():
                            if p > 0:
                                result[addr] = p
            except Exception as e:
                if _is_likely_network_dns_error(e):
                    if not jupiter_net_logged:
                        print(
                            "[!] Jupiter prix : pas d’accès réseau ou DNS (getaddrinfo). "
                            "Vérifiez Internet, le DNS, le pare-feu ou le VPN. "
                            f"Détail : {e}"
                        )
                        jupiter_net_logged = True
                    return
                print(f"Jupiter batch prix ({base_url}): {e}")

    if jupiter_bases:
        await _jupiter_fetch_into(addresses, jupiter_bases[0])
        if len(jupiter_bases) > 1:
            missing_j = [a for a in addresses if a not in result or result.get(a, 0) <= 0]
            if missing_j:
                await _jupiter_fetch_into(missing_j, jupiter_bases[1])

    # 2) Birdeye multi_price — manquants uniquement (si clé)
    missing = [a for a in addresses if a not in result or result.get(a, 0) <= 0]
    birdeye_net_logged = False
    if BIRDEYE_API_KEY and missing:
        for i in range(0, len(missing), 100):
            chunk = missing[i : i + 100]
            list_addr = ",".join(chunk)
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    r = await client.get(
                        "https://public-api.birdeye.so/defi/multi_price",
                        params={"list_address": list_addr},
                        headers={"X-API-KEY": BIRDEYE_API_KEY},
                    )
                    if r.status_code == 200:
                        j = r.json()
                        if j.get("success") and j.get("data"):
                            for addr, obj in j["data"].items():
                                if obj:
                                    p = float(obj.get("value") or obj.get("price") or 0)
                                    if p > 0:
                                        result[addr] = p
            except Exception as e:
                if _is_likely_network_dns_error(e):
                    if not birdeye_net_logged:
                        print(
                            "[!] Birdeye prix : même problème réseau/DNS que ci-dessus. "
                            f"Détail : {e}"
                        )
                        birdeye_net_logged = True
                    break
                print(f"Birdeye batch erreur: {e}")

    # 3) DexScreener — paires même faible liquidité (memecoins)
    missing = [a for a in addresses if a not in result or result.get(a, 0) <= 0]
    if missing:
        sem = asyncio.Semaphore(20)

        async def _fetch_one(addr: str) -> tuple:
            async with sem:
                try:
                    async with httpx.AsyncClient(timeout=4.0) as client:
                        resp = await client.get(
                            f"https://api.dexscreener.com/latest/dex/tokens/{addr}"
                        )
                        if resp.status_code == 200:
                            price, _tag = _dexscreener_pick_price_usd(resp.json().get("pairs") or [])
                            if price > 0:
                                return (addr, price)
                except Exception:
                    pass
                return (addr, 0.0)

        dex_results = await asyncio.gather(*[_fetch_one(a) for a in missing])
        for addr, price in dex_results:
            if price > 0:
                result[addr] = price

    _price_batch_cache[cache_key] = {"prices": result, "ts": time.time()}
    return result


# Prix en temps réel — DexScreener puis Jupiter (lite-api puis secours api.jup.ag)
@app.get("/api/price/{token_address}")
async def get_token_price(token_address: str):
    """
    Ordre : DexScreener → Jupiter price/v3 (lite puis fallback) → Birdeye.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0, headers=_jupiter_price_headers()) as client:
            # 1) DexScreener
            try:
                resp = await client.get(
                    f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
                    timeout=5.0
                )
                if resp.status_code == 200:
                    price, _src = _dexscreener_pick_price_usd(resp.json().get("pairs") or [])
                    if price > 0:
                        return {
                            "address": token_address,
                            "price": price,
                            "timestamp": datetime.now().isoformat(),
                            "source": "dexscreener",
                        }
            except Exception as e:
                print(f"DexScreener erreur : {e}")

            # 2) Jupiter Price v3
            jup_bases: List[str] = []
            for b in (JUPITER_PRICE_V3_LITE, JUPITER_PRICE_V3_FALLBACK):
                if b and b.rstrip("/") not in {x.rstrip("/") for x in jup_bases}:
                    jup_bases.append(b)
            for jb in jup_bases:
                try:
                    resp2 = await client.get(f"{jb}?ids={token_address}", timeout=8.0)
                    if resp2.status_code == 200:
                        parsed = _jupiter_parse_prices_json(resp2.json(), [token_address])
                        price = parsed.get(token_address, 0.0)
                        if price > 0:
                            return {
                                "address": token_address,
                                "price": price,
                                "timestamp": datetime.now().isoformat(),
                                "source": "jupiter_v3",
                            }
                except Exception as e:
                    print(f"Jupiter erreur ({jb}): {e}")
            
            # 3) Birdeye API (couverture quasi-complète Solana)
            if BIRDEYE_API_KEY:
                try:
                    resp3 = await client.get(
                        f"{BIRDEYE_BASE}/defi/price",
                        params={"address": token_address},
                        headers={"X-API-KEY": BIRDEYE_API_KEY},
                        timeout=5.0
                    )
                    if resp3.status_code == 200:
                        bdata = resp3.json()
                        if bdata.get("success") and bdata.get("data"):
                            d0 = bdata["data"]
                            price = float(d0.get("value") or d0.get("price") or 0)
                            if price > 0:
                                return {"address": token_address, "price": price,
                                        "timestamp": datetime.now().isoformat(),
                                        "source": "birdeye"}
                except Exception as e:
                    print(f"Birdeye erreur : {e}")
            
            # 4) RPC Direct : creusser la blockchain pour le prix (ULTRA fiable mais lent)
            try:
                # Cherche les paires Raydium/Orca pour essayer de calculer le prix
                # (nécessite des appels RPC complexes - on peut l'activer si besoin)
                pass
            except Exception as e:
                print(f"RPC Direct erreur : {e}")
            
            return {"address": token_address, "price": 0, "error": "Prix non disponible"}
    except Exception as e:
        return {"address": token_address, "price": 0, "error": str(e)}

# Mise à jour automatique des prix
@app.post("/api/update-prices")
async def update_all_prices(
    wallet: Optional[str] = Query(None),
    quick: bool = Query(
        False,
        description="Actualisation rapide : appels Jupiter/DexScreener uniquement pour les tokens encore détenus (current_tokens > 0). Les autres lignes sont recalculées en BDD sans API.",
    ),
):
    """
    Logique de calcul unifiée (tout en USD) :
      invested_amount  → stocké en SOL dans la BDD
      sol_received     → SOL reçu lors des ventes
      current_price    → USD/token (Jupiter ou DexScreener)
      current_value    → tokens_restants × current_price_usd
      invested_usd     → invested_sol × sol_usd
      sales_usd        → SUM(sol_received) × sol_usd
      gain/loss        → d’abord coût moyen (pour remplir la ligne), puis recalcul HIFO par wallet (écrit en BDD).
    Prix récupérés en batch (Jupiter + DexScreener parallèle) pour performance.
    quick=1 : moins d'appels réseau (idéal pour « Actualiser » sans nouvel import).
    """
    try:
        sol_usd = await _get_sol_usd_price()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    global _price_batch_cache
    _price_batch_cache.clear()

    # Exclure wSOL : même règle que le dashboard (évite double comptage avec le solde SOL natif)
    sel = (
        "SELECT id, address, name, wallet_address, current_tokens, invested_amount, sol_usd_at_buy, "
        "COALESCE(current_price, 0) AS current_price FROM tokens WHERE address != '' "
        "AND COALESCE(address, '') != ?"
    )
    with get_db() as conn:
        cursor = conn.cursor()
        if wallet:
            cursor.execute(sel + " AND wallet_address = ?", (SOL_MINT, wallet))
        else:
            cursor.execute(sel, (SOL_MINT,))
        tokens = [dict(r) for r in cursor.fetchall()]

    to_name_fix = [
        t
        for t in tokens
        if _token_name_is_placeholder(t.get("name"), (t.get("address") or ""))
    ]
    if to_name_fix:
        mints_nf = list({t["address"] for t in to_name_fix if t.get("address")})
        if mints_nf:
            resolved_nf = await _resolve_token_names(mints_nf, _helius_key_raw())
            with get_db() as conn:
                c = conn.cursor()
                for t in to_name_fix:
                    addr = t.get("address") or ""
                    nm = resolved_nf.get(addr)
                    if nm and nm.strip():
                        c.execute(
                            "UPDATE tokens SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (nm.strip()[:200], t["id"]),
                        )
                        t["name"] = nm.strip()[:200]
                conn.commit()

    held = [t for t in tokens if (t.get("current_tokens") or 0) > 0]
    fetch_addresses = [t["address"] for t in (held if quick else tokens)]

    token_ids = [t["id"] for t in tokens]
    invested_map: dict = {}
    sales_map: dict = {}

    if token_ids:
        placeholders = ",".join("?" * len(token_ids))
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT token_id, COALESCE(SUM(sol_spent * COALESCE(NULLIF(sol_usd_at_buy,0), ?)), 0) as tot
                FROM purchases WHERE token_id IN ({placeholders}) AND tokens_bought > 0 AND sol_spent > 0
                GROUP BY token_id
            """, [sol_usd] + token_ids)
            for r in cursor.fetchall():
                invested_map[r["token_id"]] = float(r["tot"] or 0)
            cursor.execute(f"""
                SELECT token_id, COALESCE(SUM(COALESCE(sol_received, 0) * COALESCE(NULLIF(sol_usd_at_sale,0), ?)), 0) as tot
                FROM sales WHERE token_id IN ({placeholders})
                GROUP BY token_id
            """, [sol_usd] + token_ids)
            for r in cursor.fetchall():
                sales_map[r["token_id"]] = float(r["tot"] or 0)

    prices_map = await _get_prices_batch(fetch_addresses)

    with get_db() as conn:
        cursor = conn.cursor()
        updated = 0
        for token in tokens:
            ct = float(token.get("current_tokens") or 0)
            if quick and ct <= 0:
                current_price_usd = float(token.get("current_price") or 0)
                price_is_stale = False
                price_warning = None
                if current_price_usd <= 0:
                    cursor.execute(
                        "SELECT price FROM price_history WHERE token_id = ? ORDER BY rowid DESC LIMIT 1",
                        (token["id"],),
                    )
                    last_price_row = cursor.fetchone()
                    if last_price_row:
                        current_price_usd = float(last_price_row["price"])
                        price_is_stale = True
                        price_warning = (
                            f"⚠️ Token disparu de DexScreener. Prix utilisant le dernier prix connu : ${current_price_usd:.10g}"
                        )
            else:
                current_price_usd = float(prices_map.get(token["address"]) or 0)
                price_is_stale = False
                price_warning = None

            if not (quick and ct <= 0) and current_price_usd <= 0:
                cursor.execute(
                    "SELECT price FROM price_history WHERE token_id = ? ORDER BY rowid DESC LIMIT 1",
                    (token["id"],),
                )
                last_price_row = cursor.fetchone()
                if last_price_row:
                    current_price_usd = float(last_price_row["price"])
                    price_is_stale = True
                    price_warning = f"⚠️ Token disparu de DexScreener. Prix utilisant le dernier prix connu : ${current_price_usd:.10g}"

            current_value_usd = (token.get("current_tokens") or 0) * current_price_usd

            invested_usd_total = invested_map.get(token["id"], 0.0)
            if invested_usd_total <= 0:
                amt, sol_at = token.get("invested_amount") or 0, token.get("sol_usd_at_buy")
                invested_usd_total = amt * (sol_at or sol_usd) if sol_at else amt
            sales_usd = sales_map.get(token["id"], 0.0)

            total_value_usd = current_value_usd + sales_usd
            profit_loss = total_value_usd - invested_usd_total
            gain = max(0.0, profit_loss)
            loss = abs(min(0.0, profit_loss))

            # TOUJOURS mettre à jour, même si price=0 (token mort/invendable = valeur 0)
            # Sans ce UPDATE systématique, l'ancienne current_value reste et gonfle le total
            cursor.execute("""
                UPDATE tokens
                SET current_price=?, current_value=?, gain=?, loss=?,
                    price_is_stale=?, price_warning=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            """, (current_price_usd, current_value_usd, gain, loss, _bool_to_sql_int(price_is_stale), price_warning, token['id']))

            # Historiser seulement si prix issu d'un fetch (évite doublons en boucle quick sur soldes nuls)
            if current_price_usd > 0 and not (quick and ct <= 0):
                cursor.execute(
                    "INSERT INTO price_history (token_id, price) VALUES (?, ?)",
                    (token['id'], current_price_usd)
                )
            updated += 1

        conn.commit()
        if wallet and str(wallet).strip():
            wallets_hifo = [str(wallet).strip()]
        else:
            wallets_hifo = sorted(
                {
                    (t.get("wallet_address") or "").strip()
                    for t in tokens
                    if (t.get("wallet_address") or "").strip()
                }
            )
        _sync_tokens_gain_loss_hifo_for_wallets(conn, sol_usd, wallets_hifo)
        msg = f"{updated} tokens mis à jour"
        if quick:
            msg += f" (rapide : {len(fetch_addresses)} prix API)"
        if wallet and str(wallet).strip():
            try:
                with get_db() as snap_conn:
                    _record_wallet_pnl_snapshot(
                        snap_conn, str(wallet).strip(), sol_usd, force=not quick
                    )
            except Exception as e:
                print(f"[!] Snapshot P/L wallet ignoré: {e}")
        return {"message": msg, "updated": updated, "api_price_lookups": len(fetch_addresses)}


class UpdatePricesForTokensBody(BaseModel):
    addresses: List[str] = []


@app.post("/api/update-prices-for-tokens")
async def update_prices_for_tokens(body: UpdatePricesForTokensBody = Body(...), wallet: Optional[str] = Query(None)):
    """
    Met à jour les prix uniquement pour les tokens dont les adresses sont fournies.
    Plus rapide qu'un update-prices complet quand on a ajouté peu de nouveaux tokens.
    """
    if not body.addresses:
        return {"message": "Aucune adresse fournie", "updated": 0}
    try:
        sol_usd = await _get_sol_usd_price()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    with get_db() as conn:
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(body.addresses))
        cursor.execute(
            f"SELECT id, address, current_tokens, invested_amount, sol_usd_at_buy, wallet_address FROM tokens "
            f"WHERE address IN ({placeholders}) AND address != '' AND COALESCE(address, '') != ?",
            [*body.addresses, SOL_MINT],
        )
        tokens = [dict(r) for r in cursor.fetchall()]
        if wallet:
            tokens = [t for t in tokens if t.get("wallet_address") == wallet]

    if not tokens:
        return {"message": "Aucun token trouvé", "updated": 0}

    token_ids = [t["id"] for t in tokens]
    invested_map: dict = {}
    sales_map: dict = {}
    placeholders = ",".join("?" * len(token_ids))
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT token_id, COALESCE(SUM(sol_spent * COALESCE(NULLIF(sol_usd_at_buy,0), ?)), 0) as tot
            FROM purchases WHERE token_id IN ({placeholders}) AND tokens_bought > 0 AND sol_spent > 0
            GROUP BY token_id
        """, [sol_usd] + token_ids)
        for r in cursor.fetchall():
            invested_map[r["token_id"]] = float(r["tot"] or 0)
        cursor.execute(f"""
            SELECT token_id, COALESCE(SUM(COALESCE(sol_received, 0) * COALESCE(NULLIF(sol_usd_at_sale,0), ?)), 0) as tot
            FROM sales WHERE token_id IN ({placeholders})
            GROUP BY token_id
        """, [sol_usd] + token_ids)
        for r in cursor.fetchall():
            sales_map[r["token_id"]] = float(r["tot"] or 0)

    addresses = [t["address"] for t in tokens]
    prices_map = await _get_prices_batch(addresses)

    with get_db() as conn:
        cursor = conn.cursor()
        updated = 0
        for token in tokens:
            current_price_usd = float(prices_map.get(token["address"]) or 0)
            price_is_stale = False
            price_warning = None
            if current_price_usd <= 0:
                cursor.execute(
                    "SELECT price FROM price_history WHERE token_id = ? ORDER BY rowid DESC LIMIT 1",
                    (token["id"],),
                )
                last_price_row = cursor.fetchone()
                if last_price_row:
                    current_price_usd = float(last_price_row["price"])
                    price_is_stale = True
                    price_warning = (
                        f"⚠️ Token disparu de DexScreener. Prix utilisant le dernier prix connu : ${current_price_usd:.10g}"
                    )

            current_value_usd = token["current_tokens"] * current_price_usd
            invested_usd_total = invested_map.get(token["id"], 0.0)
            if invested_usd_total <= 0:
                amt, sol_at = token.get("invested_amount") or 0, token.get("sol_usd_at_buy")
                invested_usd_total = amt * (sol_at or sol_usd) if sol_at else amt
            sales_usd = sales_map.get(token["id"], 0.0)
            total_value_usd = current_value_usd + sales_usd
            profit_loss = total_value_usd - invested_usd_total
            gain = max(0.0, profit_loss)
            loss = abs(min(0.0, profit_loss))

            cursor.execute("""
                UPDATE tokens SET current_price=?, current_value=?, gain=?, loss=?,
                    price_is_stale=?, price_warning=?, updated_at=CURRENT_TIMESTAMP WHERE id=?
            """, (current_price_usd, current_value_usd, gain, loss, _bool_to_sql_int(price_is_stale), price_warning, token["id"]))
            if current_price_usd > 0:
                cursor.execute("INSERT INTO price_history (token_id, price) VALUES (?, ?)", (token["id"], current_price_usd))
            updated += 1
        conn.commit()
        wallets_hifo = sorted(
            {(t.get("wallet_address") or "").strip() for t in tokens if (t.get("wallet_address") or "").strip()}
        )
        _sync_tokens_gain_loss_hifo_for_wallets(conn, sol_usd, wallets_hifo)
    return {"message": f"{updated} tokens mis à jour", "updated": updated}


@app.post("/api/recalculate-history")
async def recalculate_history(wallet: Optional[str] = Query(None)):
    """
    Simule tout le wallet en HIFO, écrit hifo_pnl_usd sur chaque vente + gain/loss latent sur les tokens.
    Les PnL de ventes passées sont ainsi figés en BDD (le dashboard les relit sans refaire varier au cours du SOL).
    Le latent des positions ouvertes continue de bouger avec les prix entre deux recalculs ; ce POST surtout après
    import, nouvelle vente, ou correction de données.
    """
    # Utilise uniquement le dernier taux SOL/USD stocké en BDD (pas d'appel réseau)
    with get_db() as _conn:
        _r = _conn.execute(
            "SELECT sol_usd_at_buy FROM purchases WHERE sol_usd_at_buy > 0 ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if not _r:
            _r2 = _conn.execute(
                "SELECT sol_usd_at_sale FROM sales WHERE sol_usd_at_sale > 0 ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            sol_usd = float(_r2["sol_usd_at_sale"]) if _r2 else 150.0
        else:
            sol_usd = float(_r["sol_usd_at_buy"])

    try:
        with get_db() as conn:
            cursor = conn.cursor()
            if wallet:
                cursor.execute(
                    "SELECT id, current_tokens, current_price, current_value, invested_amount, sol_usd_at_buy "
                    "FROM tokens WHERE wallet_address = ?", (wallet,)
                )
            else:
                cursor.execute(
                    "SELECT id, current_tokens, current_price, current_value, invested_amount, sol_usd_at_buy "
                    "FROM tokens"
                )
            tokens = cursor.fetchall()

            recalculated = 0
            details = []

            for token in tokens:
                token_id          = token["id"]
                current_value_usd = token["current_value"] or (token["current_tokens"] * token["current_price"]) if token["current_price"] else 0

                # ── Lots d'achat HIFO (prix décroissant, puis chronologique) ──────
                cursor.execute("""
                    SELECT purchase_timestamp, purchase_date, COALESCE(purchase_slot, 0) as purchase_slot,
                           tokens_bought, sol_spent,
                           COALESCE(NULLIF(sol_usd_at_buy, 0), ?) as sol_rate_buy
                    FROM purchases
                    WHERE token_id = ? AND tokens_bought > 0 AND sol_spent > 0
                    ORDER BY (sol_spent / tokens_bought) * COALESCE(NULLIF(sol_usd_at_buy, 0), ?) DESC,
                             purchase_timestamp ASC
                """, (sol_usd, token_id, sol_usd))
                purchase_lots = [dict(r) for r in cursor.fetchall()]
                for lot in purchase_lots:
                    lot["remaining"] = lot["tokens_bought"]
                    lot["ts"] = _lot_ts_for_hifo(lot.get("purchase_timestamp"), lot.get("purchase_date"))
                    lot["slot"] = lot.get("purchase_slot", 0) or 0
                    lot["price_usd"] = (
                        (lot["sol_spent"] / lot["tokens_bought"]) * lot["sol_rate_buy"]
                        if lot["tokens_bought"] else 0
                    )

                # ── Ventes chronologiques ─────────────────────────────────────────
                cursor.execute("""
                    SELECT tokens_sold, sol_received,
                           COALESCE(sale_timestamp, 0) as sale_ts,
                           COALESCE(sale_slot, 0) as sale_slot,
                           sale_date,
                           COALESCE(NULLIF(sol_usd_at_sale, 0), ?) as sol_rate_sell,
                           id as sale_id
                    FROM sales WHERE token_id = ?
                    ORDER BY COALESCE(sale_timestamp, 0) ASC
                """, (sol_usd, token_id))
                token_sales = [dict(r) for r in cursor.fetchall()]

                realized_cost_usd = 0.0
                sales_usd         = 0.0
                for s in token_sales:
                    tokens_left = s["tokens_sold"]
                    sale_ts_c = _sale_ts_ceiling_for_hifo(s.get("sale_ts"), s.get("sale_date"))
                    sale_slot   = _row_get(s, "sale_slot", 0) or 0
                    sales_usd  += (s["sol_received"] or 0) * s["sol_rate_sell"]

                    eligible = sorted(
                        [l for l in purchase_lots
                         if _lot_eligible_for_sale(l["ts"], l.get("slot", 0), sale_ts_c, sale_slot)],
                        key=lambda l: l["price_usd"], reverse=True
                    )
                    for lot in eligible:
                        if tokens_left <= 0:
                            break
                        consume = min(lot["remaining"], tokens_left)
                        ratio   = consume / lot["tokens_bought"] if lot["tokens_bought"] else 0
                        realized_cost_usd += lot["sol_spent"] * ratio * lot["sol_rate_buy"]
                        lot["remaining"]  -= consume
                        tokens_left       -= consume

                # ── Coût total vs coût vendu → coût restant ───────────────────────
                if purchase_lots:
                    total_invested_usd_token = sum(
                        l["sol_spent"] * l["sol_rate_buy"] for l in purchase_lots
                    )
                else:
                    # Fallback CMP (tokens manuels sans table purchases)
                    inv_amt    = token["invested_amount"] or 0
                    sol_at_buy = token["sol_usd_at_buy"]
                    total_invested_usd_token = inv_amt * sol_at_buy if sol_at_buy else inv_amt

                total_value_usd = current_value_usd + sales_usd
                profit_loss     = total_value_usd - total_invested_usd_token
                gain            = max(0.0, profit_loss)
                loss            = abs(min(0.0, profit_loss))

                cursor.execute("""
                    UPDATE tokens SET gain=?, loss=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                """, (gain, loss, token_id))

                details.append({
                    "token_id":       token_id,
                    "invested_usd":   round(total_invested_usd_token, 4),
                    "realized_cost":  round(realized_cost_usd, 4),
                    "current_value":  round(current_value_usd, 4),
                    "sales_usd":      round(sales_usd, 4),
                    "gain":           round(gain, 4),
                    "loss":           round(loss, 4),
                })
                recalculated += 1

            if wallet:
                _persist_wallet_hifo(conn, wallet, sol_usd)
            else:
                c2 = conn.cursor()
                c2.execute(
                    """
                    SELECT DISTINCT wallet_address FROM tokens
                    WHERE wallet_address IS NOT NULL AND TRIM(wallet_address) != ''
                      AND (
                        EXISTS (SELECT 1 FROM sales s WHERE s.token_id = tokens.id)
                        OR EXISTS (SELECT 1 FROM purchases p WHERE p.token_id = tokens.id)
                      )
                    """
                )
                for row in c2.fetchall():
                    wa = row["wallet_address"]
                    if wa:
                        _persist_wallet_hifo(conn, str(wa).strip(), sol_usd)

            conn.commit()
            if wallet:
                _sync_tokens_gain_loss_hifo_for_wallets(conn, sol_usd, [str(wallet).strip()])
            else:
                c_sync = conn.cursor()
                c_sync.execute(
                    "SELECT DISTINCT wallet_address FROM tokens WHERE wallet_address IS NOT NULL AND TRIM(wallet_address) != ''"
                )
                wl = [
                    str(r["wallet_address"]).strip()
                    for r in c_sync.fetchall()
                    if r["wallet_address"]
                ]
                _sync_tokens_gain_loss_hifo_for_wallets(conn, sol_usd, wl)

        if wallet:
            _invalidate_dashboard_cache(wallet)
            try:
                with get_db() as snap_conn:
                    _record_wallet_pnl_snapshot(snap_conn, str(wallet).strip(), sol_usd, force=True)
            except Exception as e:
                print(f"[!] Snapshot P/L après HIFO: {e}")
        else:
            _invalidate_dashboard_cache()
            try:
                with get_db() as snap_conn:
                    c0 = snap_conn.cursor()
                    c0.execute(
                        """
                        SELECT DISTINCT wallet_address FROM tokens
                        WHERE wallet_address IS NOT NULL AND TRIM(wallet_address) != ''
                        """
                    )
                    for row in c0.fetchall():
                        wa = str(row["wallet_address"] or "").strip()
                        if wa:
                            _record_wallet_pnl_snapshot(snap_conn, wa, sol_usd, force=True)
            except Exception as e:
                print(f"[!] Snapshots P/L globaux: {e}")

        return {
            "message":      f"{recalculated} tokens recalculés (HIFO chronologique)",
            "recalculated": recalculated,
            "details":      details,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur recalcul HIFO : {str(e)}")

# Historique des prix pour graphiques
@app.get("/api/history/{token_id}")
async def get_price_history(token_id: int, limit: int = 100):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT price, timestamp 
            FROM price_history 
            WHERE token_id=? 
            ORDER BY timestamp DESC 
            LIMIT ?
        """, (token_id, limit))
        history = cursor.fetchall()
        return [{"price": row['price'], "timestamp": row['timestamp']} for row in reversed(list(history))]

# Cache pour gains-history et portfolio-history (TTL 30s)
_charts_cache: dict = {}
_charts_cache_ttl = 30


@app.get("/api/wallet-pnl-history")
async def get_wallet_pnl_history(
    wallet: Optional[str] = Query(None),
    days: Optional[int] = Query(
        None,
        ge=0,
        le=3650,
        description="Fenêtre glissante (jours). Omis ou 0 = tout l’historique des snapshots pour ce wallet.",
    ),
    limit: Optional[int] = Query(
        None,
        ge=1,
        le=100_000,
        description="Plafond optionnel de points (ordre chronologique). Omis = pas de plafond (après filtre days).",
    ),
):
    """
    Points (date, gain/perte net USD) pour le graphique d’évolution du portefeuille.
    Aligné sur le dashboard : Σ gains − Σ pertes des tokens (hors wSOL).
    Les points s’accumulent à chaque actualisation des prix (ou recalcul HIFO).
    """
    if wallet is None or not str(wallet).strip():
        return []
    w = str(wallet).strip()
    use_days = days is not None and int(days) > 0
    try:
        sol_usd = await _get_sol_usd_price()
    except Exception:
        sol_usd = 150.0
    with get_db() as conn:
        _ensure_wallet_pnl_snapshots(conn, w, sol_usd)
        _maybe_realign_wallet_pnl_origin(conn, w)
        cursor = conn.cursor()
        where = "WHERE wallet_address = ?"
        params: list = [w]
        if use_days:
            where += " AND datetime(recorded_at) >= datetime('now', '-' || ? || ' days')"
            params.append(int(days))
        order_sql = " ORDER BY datetime(recorded_at) ASC, id ASC"
        sel = """
            SELECT recorded_at, net_pnl_usd, total_invested_usd, current_value_usd, withdrawn_usd
            FROM wallet_pnl_snapshots
        """
        sql = sel + where + order_sql
        if limit is not None:
            cursor.execute(sql + " LIMIT ?", (*params, limit))
        else:
            cursor.execute(sql, tuple(params))
        rows = cursor.fetchall()
    return [
        {
            "date": row["recorded_at"],
            "net_pnl_usd": round(float(row["net_pnl_usd"] or 0), 4),
            "total_invested_usd": round(float(row["total_invested_usd"] or 0), 4),
            "current_value_usd": round(float(row["current_value_usd"] or 0), 4),
            "withdrawn_usd": round(float(row["withdrawn_usd"] or 0), 4),
        }
        for row in rows
    ]


# Portfolio Evolution
@app.get("/api/portfolio-history")
async def get_portfolio_history(
    days: Optional[int] = Query(
        None,
        ge=0,
        le=3650,
        description="Fenêtre glissante (jours). Omis ou 0 = depuis la première entrée price_history (historique complet).",
    ),
    wallet: Optional[str] = Query(
        None,
        description="Limiter aux tokens de ce wallet ; la courbe commence à la date d’origine du portefeuille en base.",
    ),
):
    use_window = days is not None and int(days) > 0
    wkey = (wallet or "").strip() or None
    cache_key = f"portfolio_{wkey or 'all'}_{days if use_window else 'all'}"
    now = time.time()
    if cache_key in _charts_cache:
        cached = _charts_cache[cache_key]
        if now - cached["ts"] < _charts_cache_ttl:
            return cached["data"]
    with get_db() as conn:
        cursor = conn.cursor()

        origin_day: Optional[str] = None
        if wkey:
            origin_day = _wallet_portfolio_origin_date_only(cursor, wkey)
            cursor.execute(
                "SELECT id, current_tokens FROM tokens WHERE wallet_address = ?",
                (wkey,),
            )
        else:
            cursor.execute("SELECT id, current_tokens FROM tokens")
        tokens = {row["id"]: row["current_tokens"] for row in cursor.fetchall()}

        if not tokens:
            return []

        # Récupérer l'historique des prix groupé par date (tout le passé si pas de fenêtre)
        if use_window:
            cursor.execute("""
                SELECT 
                    DATE(ph.timestamp) as date,
                    ph.token_id,
                    AVG(ph.price) as avg_price
                FROM price_history ph
                WHERE ph.timestamp >= datetime('now', '-' || ? || ' days')
                GROUP BY DATE(ph.timestamp), ph.token_id
                ORDER BY date ASC
            """, (int(days),))
        else:
            cursor.execute("""
                SELECT 
                    DATE(ph.timestamp) as date,
                    ph.token_id,
                    AVG(ph.price) as avg_price
                FROM price_history ph
                GROUP BY DATE(ph.timestamp), ph.token_id
                ORDER BY date ASC
            """)
        
        price_history = cursor.fetchall()
        
        # Calculer la valeur totale par date
        portfolio_by_date = {}
        for row in price_history:
            date = row['date']
            token_id = row['token_id']
            avg_price = row['avg_price']
            
            if date not in portfolio_by_date:
                portfolio_by_date[date] = 0
            
            if token_id in tokens:
                portfolio_by_date[date] += tokens[token_id] * avg_price

        # Depuis l’origine du wallet : ne pas afficher de « valeur » avant la 1re activité en base
        if origin_day:
            portfolio_by_date = {
                d: v for d, v in portfolio_by_date.items() if str(d) >= origin_day
            }

        # Convertir en liste triée
        result = [{"date": date, "value": value} for date, value in sorted(portfolio_by_date.items())]
        _charts_cache[cache_key] = {"data": result, "ts": time.time()}
        return result

# Historique des gains (Gain Figé + Gain Actuel) par date
@app.get("/api/gains-history")
async def get_gains_history():
    cache_key = "gains"
    now = time.time()
    if cache_key in _charts_cache:
        cached = _charts_cache[cache_key]
        if now - cached["ts"] < _charts_cache_ttl:
            return cached["data"]
    with get_db() as conn:
        cursor = conn.cursor()

        # Total investi en USD
        # Helius : invested_amount en SOL × sol_usd_at_buy ; Manuel : invested_amount déjà en USD (sol_usd_at_buy NULL)
        cursor.execute("""
            SELECT COALESCE(SUM(
                CASE WHEN sol_usd_at_buy IS NOT NULL AND sol_usd_at_buy > 0
                     THEN invested_amount * sol_usd_at_buy
                     ELSE invested_amount
                END
            ), 0) as total FROM tokens
        """)
        total_invested = cursor.fetchone()['total']

        # Tokens actuels
        cursor.execute("SELECT id, current_tokens FROM tokens")
        tokens = {row['id']: row['current_tokens'] for row in cursor.fetchall()}

        if not tokens:
            return []

        # Prix moyen par date et token
        cursor.execute("""
            SELECT DATE(timestamp) as date, token_id, AVG(price) as avg_price
            FROM price_history
            GROUP BY DATE(timestamp), token_id
            ORDER BY date ASC
        """)
        price_rows = cursor.fetchall()

        # Ventes en USD — Helius : sol_received × sol_usd_at_sale ; Manuel : sale_amount déjà en USD
        cursor.execute("""
            SELECT s.sale_date,
                CASE WHEN s.sol_usd_at_sale IS NOT NULL AND s.sol_usd_at_sale > 0
                     THEN COALESCE(s.sol_received, s.sale_amount, 0) * s.sol_usd_at_sale
                     ELSE COALESCE(s.sale_amount, 0)
                END as sold_usd,
                CASE WHEN t.sol_usd_at_buy IS NOT NULL AND t.sol_usd_at_buy > 0
                     THEN (s.tokens_sold / MAX(CAST(t.purchased_tokens AS REAL), 0.000001)) * t.invested_amount * t.sol_usd_at_buy
                     ELSE (s.tokens_sold / MAX(CAST(t.purchased_tokens AS REAL), 0.000001)) * t.invested_amount
                END as cost_usd
            FROM sales s JOIN tokens t ON s.token_id = t.id
            ORDER BY s.sale_date ASC
        """)
        sales_rows = cursor.fetchall()

        # Regrouper prix par date
        prices_by_date = {}
        for row in price_rows:
            d = row['date']
            if d not in prices_by_date:
                prices_by_date[d] = {}
            prices_by_date[d][row['token_id']] = row['avg_price']

        # Construire le graphique date par date
        result = []
        cumulative_sales = 0
        cumulative_realized_gain = 0
        sales_index = 0
        sales_list = list(sales_rows)

        for date in sorted(prices_by_date.keys()):
            while sales_index < len(sales_list) and sales_list[sales_index]['sale_date'] <= date:
                row = sales_list[sales_index]
                sold_usd = row['sold_usd'] or 0
                cost_usd = row['cost_usd'] or 0
                cumulative_sales += sold_usd
                cumulative_realized_gain += sold_usd - cost_usd
                sales_index += 1

            portfolio_value = sum(
                tokens.get(tid, 0) * price
                for tid, price in prices_by_date[date].items()
            )

            total_value = portfolio_value + cumulative_sales
            total_gain = max(0, total_value - total_invested)

            result.append({
                "date": date,
                "gain_total": round(total_gain, 4),
                "gain_fige": round(cumulative_realized_gain, 4)
            })
        _charts_cache[cache_key] = {"data": result, "ts": time.time()}
        return result

# =============================================================================
# === HELIUS API — Données blockchain Solana en temps réel ===
# =============================================================================
# HELIUS_API_KEY / BIRDEYE_API_KEY : lus dans config.py via os.getenv uniquement (secrets Render / .env).

HELIUS_BASE = "https://api.helius.xyz/v0"

# Birdeye (optionnel) + RPC public fallback
BIRDEYE_BASE = "https://api.birdeye.so/v1"
RPC_MAINNET = "https://rpc.ankr.com/solana"  # RPC gratuit pour fallback


def _helius_key() -> str:
    """Vérifie que la clé Helius est configurée (variable d’environnement, jamais en dur dans le code)."""
    if not HELIUS_API_KEY:
        raise HTTPException(
            status_code=500,
            detail=(
                f"{ENV_NAME_HELIUS_API_KEY} manquante : définissez-la comme secret sur Render "
                "ou dans .env en local. Aucune clé ne doit figurer dans le dépôt Git."
            ),
        )
    return HELIUS_API_KEY


def _helius_key_raw() -> str:
    """Clé Helius si présente (sans lever) — enrichissement noms, etc."""
    return (HELIUS_API_KEY or "").strip()


def _token_name_is_placeholder(name: Optional[str], mint: str) -> bool:
    """True si le nom en BDD est vide ou le fallback mint[:8]… (Helius sans métadonnées)."""
    mint = (mint or "").strip()
    if not mint:
        return False
    n = (name or "").strip()
    if not n:
        return True
    p8 = mint[:8]
    if n in (p8 + "…", p8 + "...", p8) or n == mint:
        return True
    return False


# ── 1. Solde SOL + tokens d'un wallet ────────────────────────────────────────
@app.get("/api/helius/balances/{wallet_address}")
async def helius_balances(wallet_address: str):
    """Retourne les soldes SOL et tokens d'un wallet via Helius."""
    key = _helius_key()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{HELIUS_BASE}/addresses/{wallet_address}/balances",
            params={"api-key": key}
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code,
                            detail=f"Helius erreur: {resp.text}")
    data = resp.json()
    native = float(data.get("nativeBalance", 0) or 0) / LAMPORTS_PER_SOL
    wsol = _wsol_amount_from_helius_tokens(data.get("tokens", []))
    return {
        "wallet": wallet_address,
        "sol_balance": native + wsol,
        "sol_native": native,
        "sol_wrapped": wsol,
        "tokens": data.get("tokens", [])
    }


# ── 2. Historique complet des transactions parsées ─────────────────────────
@app.get("/api/helius/transactions/{wallet_address}")
async def helius_transactions(
    wallet_address: str,
    limit: int = Query(100, ge=1, le=100),
    before: Optional[str] = None,
    tx_type: Optional[str] = None,
):
    """
    Retourne les transactions parsées d'un wallet (swaps, transfers, etc.)
    Paramètres :
      - limit  : max 100 par appel
      - before : signature de la dernière tx pour paginer
      - tx_type: filtre optionnel (SWAP, TRANSFER, NFT_SALE…)
    """
    key = _helius_key()
    params: dict = {"api-key": key, "limit": limit}
    if before:
        params["before"] = before
    if tx_type:
        params["type"] = tx_type

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{HELIUS_BASE}/addresses/{wallet_address}/transactions",
            params=params
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code,
                            detail=f"Helius erreur: {resp.text}")
    return resp.json()


# ── 3. Infos / métadonnées d'un token (mint) ──────────────────────────────
@app.get("/api/helius/token-info/{mint_address}")
async def helius_token_info(mint_address: str):
    """Retourne les métadonnées d'un token via Helius."""
    key = _helius_key()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{HELIUS_BASE}/token-metadata",
            params={"api-key": key},
            json={"mintAccounts": [mint_address], "includeOffChain": True, "disableCache": False}
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code,
                            detail=f"Helius erreur: {resp.text}")
    return resp.json()


async def _resolve_token_names_dexscreener(mints: list[str]) -> dict[str, str]:
    """Complète les noms via DexScreener (souvent mieux que les métadonnées on-chain pour les memecoins)."""
    if not mints:
        return {}
    out: dict[str, str] = {}
    sem = asyncio.Semaphore(5)

    async def _one(client: httpx.AsyncClient, mint: str) -> None:
        if not mint:
            return
        async with sem:
            try:
                r = await client.get(
                    f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
                    timeout=12.0,
                )
                if r.status_code != 200:
                    return
                for p in r.json().get("pairs") or []:
                    bt = p.get("baseToken") or {}
                    if (bt.get("address") or "").strip() != mint:
                        continue
                    nm = (bt.get("name") or "").strip()
                    sym = (bt.get("symbol") or "").strip()
                    label = nm or sym
                    if label:
                        out[mint] = label[:200]
                        return
            except Exception:
                return

    uniq = list(dict.fromkeys(m for m in mints if m))
    async with httpx.AsyncClient() as client:
        await asyncio.gather(*[_one(client, m) for m in uniq])
    return out


# ── Helper : résoudre les noms de tokens en batch via Helius ───────────────
async def _resolve_token_names(mints: list[str], key: str) -> dict[str, str]:
    """
    Helius token-metadata puis DexScreener pour les mints encore sans nom.
    `key` peut être vide : dans ce cas seul DexScreener est utilisé.
    """
    if not mints:
        return {}
    names: dict[str, str] = {}
    chunk_size = 100
    if key:
        async with httpx.AsyncClient(timeout=20.0) as client:
            for i in range(0, len(mints), chunk_size):
                chunk = mints[i : i + chunk_size]
                try:
                    resp = await client.post(
                        f"{HELIUS_BASE}/token-metadata",
                        params={"api-key": key},
                        json={
                            "mintAccounts": chunk,
                            "includeOffChain": True,
                            "disableCache": False,
                        },
                    )
                    if resp.status_code == 200:
                        for item in resp.json():
                            mint = item.get("account", "")
                            name = (
                                (item.get("onChainMetadata") or {})
                                .get("metadata", {})
                                .get("data", {})
                                .get("name")
                                or (item.get("offChainMetadata") or {})
                                .get("metadata", {})
                                .get("name")
                                or (item.get("legacyMetadata") or {}).get("name")
                            )
                            if name:
                                names[mint] = name.strip().rstrip("\x00")
                except Exception:
                    pass
    missing = [m for m in mints if m and not names.get(m)]
    if missing:
        names.update(await _resolve_token_names_dexscreener(missing))
    return names


# ── 4a. Vérification DELTA — juste comparer dernière sig Helius vs BDD ────
@app.get("/api/helius/needs-import/{wallet_address}")
async def helius_check_sync(wallet_address: str) -> dict:
    """
    Compare les N tx les plus récentes Helius avec la BDD (imported_tx + purchases + sales).
    Une seule signature « connue » en tête ne suffit pas : une vente récente peut être absente
    d'imported_tx ou Helius peut réordonner / indexer avec retard.
    """
    key = _helius_key()

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                f"{HELIUS_BASE}/addresses/{wallet_address}/transactions",
                params={"api-key": key, "limit": HELIUS_RECENT_TX_WINDOW},
            )
        if resp.status_code != 200:
            return {"needs_import": True, "reason": "Helius error"}

        helius_txs = resp.json() or []
        if not helius_txs:
            return {"needs_import": False, "reason": "No transactions"}

        sigs = [tx.get("signature", "") for tx in helius_txs if tx.get("signature")]
        last_helius_sig = sigs[0] if sigs else ""
    except Exception as e:
        return {"needs_import": True, "reason": f"Helius fetch error: {str(e)}"}

    with get_db() as conn:
        missing = [s for s in sigs if not _tx_signature_recorded_in_db(conn, s)]

    return {
        "needs_import": len(missing) > 0,
        "last_helius_sig": last_helius_sig,
        "pending_recent": len(missing),
        "already_synced": len(missing) == 0,
    }


# ── 4. Import automatique des swaps → BDD ─────────────────────────────────
BATCH_SIZE = 100  # Helius accepte jusqu'à 100 tx par requête → moins d'appels réseau


def _native_sol_recu_envoye_from_tx(tx: dict, wallet_address: str) -> tuple[float, float]:
    """
    Variation SOL natif du wallet sur une tx (Helius accountData.nativeBalanceChange).
    Les SWAP sont inclus : un achat meme fait typiquement baisser le solde natif → compté comme « envoyé » ;
    une vente SOL peut l’augmenter → « reçu ».
    Limite : si le parcours ne touche qu’au WSOL (wrapped) sans delta natif, ça peut rester à 0.
    """
    account_data = tx.get("accountData") or []
    wallet_sol_change = 0
    for acc in account_data:
        if acc.get("account") == wallet_address:
            wallet_sol_change = int(acc.get("nativeBalanceChange", 0) or 0)
            break
    if abs(wallet_sol_change) <= 10_000:
        return 0.0, 0.0
    sol_amt = abs(wallet_sol_change) / LAMPORTS_PER_SOL
    if wallet_sol_change > 0:
        return sol_amt, 0.0
    return 0.0, sol_amt


async def _helius_fetch_sol_flow_totals(wallet: str, max_pages: int = 45) -> tuple[float, float, int]:
    """
    SOL reçu / envoyé agrégés (Helius), sur max_pages * BATCH_SIZE transactions.
    Toutes les tx (dont SWAP) : somme des deltas natifs positifs = reçu, négatifs = envoyé.
    """
    w = (wallet or "").strip()
    if not HELIUS_API_KEY or not w:
        return 0.0, 0.0, 0
    sol_recu = 0.0
    sol_envoye = 0.0
    pages_done = 0
    cursor_sig: Optional[str] = None
    async with httpx.AsyncClient(timeout=85.0) as client:
        while pages_done < max_pages:
            params: dict = {"api-key": HELIUS_API_KEY, "limit": BATCH_SIZE}
            if cursor_sig:
                params["before"] = cursor_sig
            r = await client.get(f"{HELIUS_BASE}/addresses/{w}/transactions", params=params)
            if r.status_code != 200:
                break
            txs = r.json() or []
            if not txs:
                break
            for tx in txs:
                dr, de = _native_sol_recu_envoye_from_tx(tx, w)
                sol_recu += dr
                sol_envoye += de
            cursor_sig = txs[-1].get("signature")
            pages_done += 1
            if len(txs) < BATCH_SIZE:
                break
    return sol_recu, sol_envoye, pages_done


@app.post("/api/helius/import-swaps/{wallet_address}")
async def helius_import_swaps(
    wallet_address: str,
    max_pages: int = Query(50, ge=1, le=150),
    resume_history: bool = Query(
        False,
        description="Si True, continue à paginer même si le bloc récent est déjà importé (compléter l'historique).",
    ),
    skip_post_import_prices: bool = Query(
        False,
        description="Si True, ne pas lancer update_all_prices à la fin (le client peut appeler /update-prices).",
    ),
    repair_imported_buys: bool = Query(
        False,
        description="Si True, supprime et réimporte les achats déjà marqués dans imported_tx (nécessaire après correction du parser).",
    ),
):
    """
    Récupère les swaps du wallet via Helius et les importe dans la BDD.
    S'arrête dès qu'une transaction déjà importée est trouvée (pas de fetch inutile).
    - Crée ou met à jour les tokens achetés
    - Enregistre les ventes (dédoublonnage par signature de transaction)
    - repair_imported_buys=True : recalcule les achats déjà importés (sinon un 2e import ne change rien).
    """
    key = _helius_key()
    swaps_fetched: list = []
    cursor_sig: Optional[str] = None
    caught_up = False
    last_batch_size = 0
    inner_pages = 0

    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS imported_tx (
                signature TEXT PRIMARY KEY, tx_type TEXT NOT NULL,
                imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.get(
            f"{HELIUS_BASE}/addresses/{wallet_address}/transactions",
            params={"api-key": key, "limit": HELIUS_RECENT_TX_WINDOW},
        )
        first_batch: list = []
        if resp.status_code == 200:
            first_batch = resp.json() or []

        if first_batch:
            sigs_fb = [tx.get("signature", "") for tx in first_batch if tx.get("signature")]
            with get_db() as conn:
                all_recent_known = bool(sigs_fb) and all(
                    _tx_signature_recorded_in_db(conn, s) for s in sigs_fb
                )
            if all_recent_known and not resume_history and not repair_imported_buys:
                return {
                    "message": "Déjà à jour.",
                    "imported_buys": 0,
                    "imported_sales": 0,
                    "repaired_buy_transactions": 0,
                    "skipped": True,
                    "may_have_more_history": False,
                }
            if all_recent_known and resume_history and not repair_imported_buys:
                # Reprendre plus loin dans le passé sans retraiter le bloc récent
                cursor_sig = first_batch[-1].get("signature", "") or None
                swaps_fetched = []
                inner_pages = max_pages
            else:
                swaps_fetched.extend(first_batch)
                cursor_sig = first_batch[-1].get("signature", "") or None
                inner_pages = max_pages - 1
        else:
            inner_pages = max_pages

        exhausted_inner_budget = False
        for _ in range(inner_pages):
            params: dict = {"api-key": key, "limit": BATCH_SIZE}
            if cursor_sig:
                params["before"] = cursor_sig

            resp = await client.get(
                f"{HELIUS_BASE}/addresses/{wallet_address}/transactions",
                params=params,
            )
            if resp.status_code != 200:
                break

            batch = resp.json()
            if not batch:
                break

            last_batch_size = len(batch)

            # Vérifier si on a déjà importé une de ces tx → on a rattrapé le retard
            # (sauf repair_imported_buys : il faut continuer à paginer pour réécrire l’historique)
            sigs = [tx.get("signature", "") for tx in batch if tx.get("signature")]
            if sigs and not repair_imported_buys:
                with get_db() as conn:
                    ph = ",".join("?" * len(sigs))
                    cur = conn.execute(
                        f"SELECT signature FROM imported_tx WHERE signature IN ({ph})",
                        sigs,
                    )
                    if cur.fetchone() is not None:
                        caught_up = True

            swaps_fetched.extend(batch)
            cursor_sig = batch[-1]["signature"]

            if repair_imported_buys:
                if len(batch) < BATCH_SIZE:
                    break
            elif caught_up or len(batch) < BATCH_SIZE:
                break
        else:
            exhausted_inner_budget = inner_pages > 0

    may_have_more_history = bool(
        exhausted_inner_budget and not caught_up and last_batch_size == BATCH_SIZE
    )
    if (
        not may_have_more_history
        and not caught_up
        and first_batch
        and len(first_batch) >= HELIUS_RECENT_TX_WINDOW
        and inner_pages == 0
    ):
        may_have_more_history = True

    if not swaps_fetched:
        return {
            "message": "Aucun swap trouvé pour ce wallet.",
            "imported_buys": 0,
            "imported_sales": 0,
            "repaired_buy_transactions": 0,
            "may_have_more_history": False,
        }

    # Trier par timestamp CROISSANT (Helius renvoie newest-first).
    # Sans ce tri, un SELL antérieur peut être traité avant le BUY,
    # ce qui laisse current_tokens à tort > 0 après l'import.
    swaps_fetched.sort(key=lambda tx: tx.get("timestamp", 0))

    try:
        sol_usd = await _get_sol_usd_price()
    except Exception:
        sol_usd = 0.0

    # Collecter tous les mints uniques via tokenTransfers pour résoudre leurs noms

    all_mints: set[str] = set()
    for tx in swaps_fetched:
        for tr in tx.get("tokenTransfers", []):
            mint = tr.get("mint", "")
            if mint and mint not in BLACKLISTED_MINTS:
                all_mints.add(mint)

    known_mints: set[str] = set()
    with get_db() as conn:
        cur = conn.execute(
            "SELECT address FROM tokens WHERE wallet_address = ? AND address != ''",
            (wallet_address,),
        )
        known_mints = {r["address"] for r in cur.fetchall()}
    mints_to_resolve = [m for m in all_mints if m not in known_mints]
    token_names = await _resolve_token_names(mints_to_resolve, key)

    imported_buys = 0
    imported_sales = 0
    repaired_buy_transactions = 0
    errors = []

    with get_db() as conn:
        db_cur = conn.cursor()

        if not USE_POSTGRES:
            # Migrations douces SQLite uniquement (schéma Postgres déjà complet au démarrage)
            db_cur.execute("PRAGMA table_info(sales)")
            cols = [r["name"] for r in db_cur.fetchall()]
            if "transaction_signature" not in cols:
                db_cur.execute("ALTER TABLE sales ADD COLUMN transaction_signature TEXT")
            if "sale_timestamp" not in cols:
                db_cur.execute("ALTER TABLE sales ADD COLUMN sale_timestamp INTEGER")
            if "sale_slot" not in cols:
                db_cur.execute("ALTER TABLE sales ADD COLUMN sale_slot INTEGER DEFAULT 0")
            if "sol_received" not in cols:
                db_cur.execute("ALTER TABLE sales ADD COLUMN sol_received REAL DEFAULT 0")
            conn.commit()

        # S'assurer que la table de suivi des signatures existe
        db_cur.execute("""
            CREATE TABLE IF NOT EXISTS imported_tx (
                signature   TEXT PRIMARY KEY,
                tx_type     TEXT NOT NULL,
                imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        if not USE_POSTGRES:
            db_cur.execute("PRAGMA table_info(sales)")
            sale_cols = [r["name"] for r in db_cur.fetchall()]
            if "sol_usd_at_sale" not in sale_cols:
                db_cur.execute("ALTER TABLE sales ADD COLUMN sol_usd_at_sale REAL DEFAULT NULL")
            if "sale_slot" not in sale_cols:
                db_cur.execute("ALTER TABLE sales ADD COLUMN sale_slot INTEGER DEFAULT 0")
        conn.commit()

        # Pré-charger tous les prix SOL historiques en parallèle (évite 50+ appels séquentiels)
        unique_dates = set()
        for tx in swaps_fetched:
            ts = tx.get("timestamp", 0)
            d = (
                datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
                if ts else datetime.now(timezone.utc).strftime("%Y-%m-%d")
            )
            unique_dates.add(d)
        _date_sol_cache: dict = {}
        if unique_dates:
            dates_list = sorted(unique_dates)

            async def _fetch_sol_day(d: str) -> tuple[str, float]:
                async with _sol_history_fetch_sem:
                    p = await _get_sol_usd_at_date(d)
                return (d, p)

            pairs = await asyncio.gather(*[_fetch_sol_day(d) for d in dates_list])
            today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            for d, p in pairs:
                if p and p > 0:
                    _date_sol_cache[d] = p
                elif d == today_utc and sol_usd > 0:
                    _date_sol_cache[d] = sol_usd
                else:
                    # Dernière ressource : spot (peut fausser fortement le $ d’achat sur les txs passées)
                    _date_sol_cache[d] = sol_usd if sol_usd > 0 else 150.0
                    if d != today_utc:
                        print(
                            f"[!] Import Helius: cours SOL/USD introuvable pour {d} — "
                            f"spot actuel utilisé : risque d’erreur sur le prix d’achat en $"
                        )

        for tx in swaps_fetched:
            try:
                sig = tx.get("signature", "")
                timestamp_unix = tx.get("timestamp", 0)
                tx_slot = tx.get("slot", 0) or 0
                tx_date = (
                    datetime.fromtimestamp(timestamp_unix, timezone.utc).strftime("%Y-%m-%d")
                    if timestamp_unix else datetime.now(timezone.utc).strftime("%Y-%m-%d")
                )

                token_transfers = tx.get("tokenTransfers", [])
                account_data    = tx.get("accountData", [])
                native_transfers = tx.get("nativeTransfers", [])
                fee_lamports    = tx.get("fee", 5000) or 5000

                # Variation native SOL du wallet dans cette tx
                wallet_sol_change = 0
                for acc in account_data:
                    if acc.get("account") == wallet_address:
                        wallet_sol_change = acc.get("nativeBalanceChange", 0)
                        break

                # SOL envoyé par le wallet vers des tiers (hors tips Jito, hors lui-même)
                # → utilisé pour calculer sol_spent sur les achats natifs
                native_sol_out_amounts = _native_sol_out_amounts_lamports(wallet_address, native_transfers)
                native_sol_out_lamports = sum(native_sol_out_amounts)
                # SOL reçu par le wallet depuis des tiers (hors lui-même)
                # → utilisé pour calculer sol_recv sur les ventes natives
                native_sol_in_lamports = sum(
                    int(nt.get("amount", 0) or 0)
                    for nt in native_transfers
                    if nt.get("toUserAccount") == wallet_address
                    and nt.get("fromUserAccount") != wallet_address
                )

                # Tokens SPL reçus/envoyés (hors blacklist + hors wSOL : pas d’« achat meme » sur le mint SOL)
                tokens_received = [t for t in token_transfers
                                   if t.get("toUserAccount") == wallet_address
                                   and t.get("mint", "") not in BLACKLISTED_MINTS
                                   and t.get("mint", "") not in _IMPORT_IGNORE_SPL_MINTS
                                   and t.get("mint", "") != ""]
                tokens_sent     = [t for t in token_transfers
                                   if t.get("fromUserAccount") == wallet_address
                                   and t.get("mint", "") not in BLACKLISTED_MINTS
                                   and t.get("mint", "") not in _IMPORT_IGNORE_SPL_MINTS
                                   and t.get("mint", "") != ""]
                tokens_received = _helius_merge_token_transfers_by_mint(tokens_received)
                tokens_sent = _helius_merge_token_transfers_by_mint(tokens_sent)

                # Wrapped-SOL reçu/envoyé par le wallet (tokenAmount déjà en SOL)
                wsol_received = sum(float(t.get("tokenAmount", 0) or 0) for t in token_transfers
                                    if t.get("toUserAccount") == wallet_address
                                    and t.get("mint") == SOL_MINT)
                wsol_sent_amounts = [
                    float(t.get("tokenAmount", 0) or 0)
                    for t in token_transfers
                    if t.get("fromUserAccount") == wallet_address
                    and t.get("mint") == SOL_MINT
                    and float(t.get("tokenAmount", 0) or 0) > 0
                ]
                wsol_sent = sum(wsol_sent_amounts)

                # ── Détection achat/vente ──────────────────────────────────
                # Priorité au sens du SOL (plus fiable que les token transfers
                # qui peuvent contenir des tokens intermédiaires dans les swaps multi-hop Jupiter).
                # wallet_sol_change < 0 → du SOL est sorti → ACHAT
                # wallet_sol_change > 0 → du SOL est entré → VENTE
                # Si le solde SOL est neutre (wSOL wrappé/dewrappé), on se rabat sur wSOL.
                sol_net = wallet_sol_change - fee_lamports  # variation hors frais réseau

                if sol_net < 0 or (sol_net == 0 and wsol_sent > 0):
                    # SOL sorti → achat potentiel
                    is_buy  = bool(tokens_received)
                    is_sell = False
                elif sol_net > 0 or (sol_net == 0 and wsol_received > 0):
                    # SOL entré → vente potentielle
                    is_sell = bool(tokens_sent)
                    is_buy  = False
                else:
                    # Impossible à trancher → on ignore
                    is_buy  = False
                    is_sell = False

                if not is_buy and not is_sell:
                    continue

                # Prix SOL/USD de la date (commun aux deux cas, pré-chargé en parallèle)
                sol_at_tx = _date_sol_cache.get(tx_date) or sol_usd

                if is_buy:
                    # Dédoublonnage (sans repair : un 2e import ne corrige pas les montants déjà en BDD)
                    already_imported = db_cur.execute("SELECT 1 FROM imported_tx WHERE signature = ?", (sig,)).fetchone()
                    if already_imported:
                        if repair_imported_buys:
                            db_cur.execute(
                                """
                                DELETE FROM purchases WHERE transaction_signature = ?
                                AND (wallet_address = ? OR token_id IN (
                                    SELECT id FROM tokens WHERE wallet_address = ?
                                ))
                                """,
                                (sig, wallet_address, wallet_address),
                            )
                            db_cur.execute("DELETE FROM imported_tx WHERE signature = ?", (sig,))
                            conn.commit()
                            repaired_buy_transactions += 1
                        else:
                            if tx_slot:
                                db_cur.execute(
                                    "UPDATE purchases SET purchase_slot = ? WHERE transaction_signature = ?",
                                    (tx_slot, sig)
                                )
                                conn.commit()
                            continue

                    # SOL dépensé (dans l'ordre de fiabilité) :
                    # 1. wSOL wrappé envoyé : le plus précis (déjà en SOL, hors frais)
                    # 2. nativeTransfers vers tiers hors Jito : exclut frais réseau + Jito tips
                    # 3. Fallback : nativeBalanceChange brut - frais réseau
                    if wsol_sent > 0:
                        sol_spent = (
                            _estimate_swap_wsol_spent_sol(wsol_sent_amounts)
                            if len(wsol_sent_amounts) > 1
                            else wsol_sent
                        )
                    elif native_sol_out_lamports > 0:
                        sol_spent = _estimate_swap_sol_spent_lamports(native_sol_out_amounts) / LAMPORTS_PER_SOL
                    else:
                        sol_spent = max(0.0, (abs(wallet_sol_change) - fee_lamports) / LAMPORTS_PER_SOL)

                    # Plafond : la somme des envois ne peut pas dépasser le SOL réellement quitté le wallet (hors frais tx)
                    sol_cap_wallet = max(0.0, (abs(wallet_sol_change) - fee_lamports) / LAMPORTS_PER_SOL)
                    if sol_spent > 0 and sol_cap_wallet > 1e-12 and sol_spent > sol_cap_wallet * 1.0001:
                        sol_spent = sol_cap_wallet

                    if sol_spent == 0:
                        continue

                    valid_received = [
                        t
                        for t in tokens_received
                        if t.get("mint") and float(t.get("tokenAmount", 0) or 0) != 0
                    ]
                    non_stable_recv = [
                        t
                        for t in valid_received
                        if t.get("mint", "") not in _SWAP_INTERMEDIATE_STABLE_MINTS
                    ]
                    cost_targets_recv = non_stable_recv if non_stable_recv else valid_received
                    n_toks = len(cost_targets_recv) or 1

                    # Stablecoins envoyés par le wallet (swap USDC/USDT → meme) : coût en USD quasi exact
                    stable_sent_usd = 0.0
                    for t in tokens_sent:
                        m = (t.get("mint") or "").strip()
                        if m in _SWAP_INTERMEDIATE_STABLE_MINTS:
                            stable_sent_usd += float(t.get("tokenAmount", 0) or 0)
                    sol_usd_leg = sol_spent * sol_at_tx if sol_at_tx > 0 else 0.0
                    use_stable_quote = (
                        stable_sent_usd >= 1.0
                        and sol_at_tx > 0
                        and stable_sent_usd >= 0.4 * max(sol_usd_leg, 1e-6)
                    )
                    base_sol_for_split = (
                        (stable_sent_usd / sol_at_tx) if use_stable_quote else sol_spent
                    )

                    for tok in tokens_received:
                        mint         = tok.get("mint", "")
                        token_amount = float(tok.get("tokenAmount", 0) or 0)
                        if not mint or token_amount == 0:
                            continue
                        if mint in _SWAP_INTERMEDIATE_STABLE_MINTS and non_stable_recv:
                            continue

                        tok_sol_spent   = base_sol_for_split / n_toks
                        price_per_token = tok_sol_spent / token_amount if token_amount else 0

                        db_cur.execute(
                            "SELECT id, purchased_tokens, current_tokens, invested_amount, sol_usd_at_buy FROM tokens WHERE address = ? AND wallet_address = ?",
                            (mint, wallet_address)
                        )
                        existing = db_cur.fetchone()

                        if existing:
                            new_purchased = (existing["purchased_tokens"] or 0) + token_amount
                            new_current   = (existing["current_tokens"]   or 0) + token_amount
                            new_invested  = (existing["invested_amount"]  or 0) + tok_sol_spent
                            avg_price     = new_invested / new_purchased if new_purchased else 0
                            old_sol_usd_buy  = existing["sol_usd_at_buy"] or sol_at_tx
                            old_invested_usd = old_sol_usd_buy * (existing["invested_amount"] or 0)
                            new_invested_usd = old_invested_usd + sol_at_tx * tok_sol_spent
                            new_sol_usd_at_buy = new_invested_usd / new_invested if new_invested else sol_at_tx
                            real_name = token_names.get(mint)
                            db_cur.execute("""
                                UPDATE tokens SET
                                    purchased_tokens = ?,
                                    current_tokens   = ?,
                                    invested_amount  = ?,
                                    purchase_price   = ?,
                                    sol_usd_at_buy   = ?,
                                    purchase_date    = COALESCE(purchase_date, ?),
                                    name             = CASE WHEN ? IS NOT NULL THEN ? ELSE name END,
                                    wallet_address   = ?,
                                    updated_at       = CURRENT_TIMESTAMP
                                WHERE id = ?
                            """, (new_purchased, new_current, new_invested, avg_price,
                                  new_sol_usd_at_buy, tx_date,
                                  real_name, real_name, wallet_address, existing["id"]))
                        else:
                            token_name = token_names.get(mint) or (mint[:8] + "…")
                            db_cur.execute("""
                                INSERT INTO tokens
                                    (name, address, purchase_date, purchased_tokens, current_tokens,
                                     purchase_price, invested_amount, sol_usd_at_buy, wallet_address)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (token_name, mint, tx_date, token_amount, token_amount,
                                  price_per_token, tok_sol_spent, sol_at_tx, wallet_address))

                        # Récupérer l'id du token (existant ou nouvellement créé)
                        if existing:
                            tok_id_for_purchase = existing["id"]
                        else:
                            tok_id_for_purchase = db_cur.lastrowid

                        # Enregistrer l'achat individuel dans la table purchases
                        db_cur.execute("""
                            INSERT OR IGNORE INTO purchases
                                (token_id, purchase_date, purchase_timestamp, purchase_slot, tokens_bought,
                                 purchase_price, sol_spent, transaction_signature, sol_usd_at_buy,
                                 wallet_address)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (tok_id_for_purchase, tx_date, timestamp_unix, tx_slot, token_amount,
                              price_per_token, tok_sol_spent, sig, sol_at_tx, wallet_address))
                        # Backfill slot si la ligne existait déjà (INSERT OR IGNORE)
                        if db_cur.rowcount == 0 and tx_slot:
                            db_cur.execute(
                                "UPDATE purchases SET purchase_slot = ? WHERE transaction_signature = ?",
                                (tx_slot, sig)
                            )

                        conn.commit()
                        imported_buys += 1

                    db_cur.execute(
                        "INSERT OR IGNORE INTO imported_tx (signature, tx_type) VALUES (?, 'buy')",
                        (sig,)
                    )
                    conn.commit()

                elif is_sell:
                    # SOL reçu (dans l'ordre de fiabilité) :
                    # 1. wSOL wrappé reçu : le plus précis
                    # 2. nativeTransfers entrants depuis tiers : exclut les auto-transferts
                    # 3. Fallback : nativeBalanceChange brut + frais (les frais sont sortis, on les réajoute)
                    if wsol_received > 0:
                        sol_recv = wsol_received
                    elif native_sol_in_lamports > 0:
                        sol_recv = native_sol_in_lamports / LAMPORTS_PER_SOL
                    else:
                        sol_recv = max(0.0, (wallet_sol_change + fee_lamports) / LAMPORTS_PER_SOL)

                    if sol_recv == 0:
                        continue

                    sell_signature_touched = False
                    valid_sent = [
                        t
                        for t in tokens_sent
                        if t.get("mint") and float(t.get("tokenAmount", 0) or 0) != 0
                    ]
                    non_stable_sent = [
                        t
                        for t in valid_sent
                        if t.get("mint", "") not in _SWAP_INTERMEDIATE_STABLE_MINTS
                    ]
                    cost_targets_sent = non_stable_sent if non_stable_sent else valid_sent
                    n_toks = len(cost_targets_sent) or 1

                    for tok in tokens_sent:
                        mint         = tok.get("mint", "")
                        token_amount = float(tok.get("tokenAmount", 0) or 0)
                        if not mint or token_amount == 0:
                            continue
                        if mint in _SWAP_INTERMEDIATE_STABLE_MINTS and non_stable_sent:
                            continue

                        db_cur.execute("SELECT id, current_tokens FROM tokens WHERE address = ? AND wallet_address = ?", (mint, wallet_address))
                        tok_row = db_cur.fetchone()
                        if not tok_row:
                            continue

                        token_id = tok_row["id"]

                        existing_sale = db_cur.execute(
                            "SELECT id FROM sales WHERE transaction_signature = ? AND token_id = ?",
                            (sig, token_id),
                        ).fetchone()
                        if existing_sale and tx_slot:
                            db_cur.execute(
                                "UPDATE sales SET sale_slot = ? WHERE transaction_signature = ? AND token_id = ?",
                                (tx_slot, sig, token_id),
                            )
                            conn.commit()
                        if existing_sale:
                            sell_signature_touched = True
                            continue

                        tok_sol_recv = sol_recv / n_toks
                        new_current  = max(0.0, (tok_row["current_tokens"] or 0) - token_amount)
                        sale_price   = tok_sol_recv / token_amount if token_amount else 0

                        db_cur.execute("""
                            INSERT INTO sales
                                (token_id, sale_date, sale_timestamp, sale_slot, tokens_sold,
                                 sale_price, sale_amount, sol_received, transaction_signature,
                                 sol_usd_at_sale)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (token_id, tx_date, timestamp_unix, tx_slot, token_amount,
                              sale_price, tok_sol_recv, tok_sol_recv, sig, sol_at_tx))

                        db_cur.execute("""
                            UPDATE tokens SET
                                current_tokens = ?,
                                sold_tokens = COALESCE(sold_tokens, 0) + ?,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                        """, (new_current, token_amount, token_id))

                        conn.commit()
                        imported_sales += 1
                        sell_signature_touched = True

                    if sell_signature_touched:
                        db_cur.execute(
                            "INSERT OR IGNORE INTO imported_tx (signature, tx_type) VALUES (?, 'sell')",
                            (sig,),
                        )
                        conn.commit()

            except Exception as exc:
                errors.append({"signature": tx.get("signature", "?"), "error": str(exc)})
                continue

    # ── Recalcul final depuis purchases et sales (sources de vérité) ────────
    # Corrige toute dérive dans purchased_tokens, sold_tokens, invested_amount, etc.
    with get_db() as conn:
        conn.execute("""
            UPDATE tokens SET
                purchased_tokens = COALESCE(
                    (SELECT SUM(tokens_bought) FROM purchases WHERE token_id = tokens.id),
                    purchased_tokens
                ),
                sold_tokens = COALESCE(
                    (SELECT SUM(tokens_sold) FROM sales WHERE token_id = tokens.id),
                    0
                ),
                invested_amount = COALESCE(
                    (SELECT SUM(sol_spent) FROM purchases WHERE token_id = tokens.id),
                    invested_amount
                ),
                purchase_price = CASE
                    WHEN COALESCE((SELECT SUM(tokens_bought) FROM purchases WHERE token_id = tokens.id), 0) > 0
                    THEN (SELECT SUM(sol_spent) FROM purchases WHERE token_id = tokens.id)
                         / (SELECT SUM(tokens_bought) FROM purchases WHERE token_id = tokens.id)
                    ELSE purchase_price
                END,
                sol_usd_at_buy = CASE
                    WHEN COALESCE((SELECT SUM(sol_spent) FROM purchases WHERE token_id = tokens.id), 0) > 0
                    THEN (SELECT SUM(p.sol_spent * COALESCE(p.sol_usd_at_buy, 150)) FROM purchases p WHERE p.token_id = tokens.id)
                         / NULLIF((SELECT SUM(sol_spent) FROM purchases WHERE token_id = tokens.id), 0)
                    ELSE sol_usd_at_buy
                END
            WHERE EXISTS (SELECT 1 FROM purchases WHERE token_id = tokens.id)
        """)
        # Recalcul de current_tokens : purchased - sold (depuis les tables)
        conn.execute("""
            UPDATE tokens
            SET current_tokens = MAX(0.0,
                COALESCE((SELECT SUM(tokens_bought) FROM purchases WHERE token_id = tokens.id), 0)
                - COALESCE((SELECT SUM(tokens_sold) FROM sales WHERE token_id = tokens.id), 0)
            )
        """)
        conn.commit()

    # ── Mise à jour automatique des prix + gains/pertes après l'import ────
    prices_updated = 0
    if not skip_post_import_prices:
        try:
            result = await update_all_prices(wallet=wallet_address)
            prices_updated = result.get("updated", 0)
        except Exception:
            pass  # Non bloquant

    _invalidate_dashboard_cache(wallet_address)
    with get_db() as _ic:
        _ensure_wallet_sol_flow_schema(_ic)
        _ic.execute("DELETE FROM wallet_sol_flow WHERE wallet_address = ?", (wallet_address,))
        _invalidate_wallet_hifo_cache(_ic, wallet_address)
    _invalidate_charts_cache()

    return {
        "message": "Import terminé",
        "wallet": wallet_address,
        "swaps_analysed": len(swaps_fetched),
        "imported_buys": imported_buys,
        "imported_sales": imported_sales,
        "repaired_buy_transactions": repaired_buy_transactions,
        "prices_updated": prices_updated,
        "may_have_more_history": may_have_more_history,
        "errors": errors[:10],
    }


# ── Backfill : remplir sol_usd_at_sale pour les ventes sans prix historique ──
@app.post("/api/backfill-sol-prices")
async def backfill_sol_prices():
    """
    Pour chaque vente en BDD qui n'a pas de sol_usd_at_sale,
    récupère le prix SOL/USD historique via CoinGecko et le stocke.
    À appeler une seule fois après migration.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        if not USE_POSTGRES:
            cursor.execute("PRAGMA table_info(sales)")
            cols = [r["name"] for r in cursor.fetchall()]
            if "sol_usd_at_sale" not in cols:
                cursor.execute("ALTER TABLE sales ADD COLUMN sol_usd_at_sale REAL DEFAULT NULL")
                conn.commit()

        try:
            cursor.execute("DELETE FROM wallet_hifo_cache")
            cursor.execute("UPDATE sales SET hifo_buy_cost_usd = NULL, hifo_pnl_usd = NULL")
            conn.commit()
        except sqlite3.OperationalError:
            pass

        cursor.execute("""
            SELECT id, sale_date FROM sales
            WHERE (sol_usd_at_sale IS NULL OR sol_usd_at_sale = 0) AND sale_date IS NOT NULL
        """)
        rows = cursor.fetchall()

    updated = 0
    errors = []
    date_cache: dict = {}

    for row in rows:
        sale_date = row["sale_date"]
        if not sale_date:
            continue
        if sale_date not in date_cache:
            import asyncio
            await asyncio.sleep(1.5)  # 1,5s entre chaque appel pour respecter la limite CoinGecko (50/min)
            price = await _get_sol_usd_at_date(sale_date)
            date_cache[sale_date] = price
        price = date_cache[sale_date]
        if price and price > 0:
            with get_db() as conn:
                conn.execute(
                    "UPDATE sales SET sol_usd_at_sale = ? WHERE id = ?",
                    (price, row["id"])
                )
                conn.commit()
            updated += 1
        else:
            errors.append({"sale_id": row["id"], "date": sale_date, "error": "Prix introuvable"})

    return {
        "message": f"{updated} ventes mises à jour avec prix historique SOL",
        "updated": updated,
        "errors": errors
    }


# ── 5. Activité récente du wallet (toutes tx confondues) ──────────────────
@app.get("/api/helius/transfers/{wallet_address}")
async def helius_wallet_transfers(wallet_address: str, limit: int = Query(50, ge=1, le=100)):
    """
    Retourne la liste des envois et des réceptions de SOL/tokens pour un wallet.
    Analyse tokenTransfers + accountData de chaque transaction.
    """
    key = _helius_key()
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{HELIUS_BASE}/addresses/{wallet_address}/transactions",
            params={"api-key": key, "limit": limit}
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=f"Helius erreur: {resp.text}")

    txs = resp.json()
    events: list[dict] = []

    for tx in txs:
        ts = tx.get("timestamp", 0)
        date_str = datetime.fromtimestamp(ts, timezone.utc).strftime("%d/%m/%Y %H:%M") if ts else "-"
        sig = tx.get("signature", "")
        tx_type = tx.get("type", "")

        token_transfers = tx.get("tokenTransfers", [])
        account_data    = tx.get("accountData", [])
        fee_lamports    = tx.get("fee", 0)

        # ── SOL natif : variation pour le wallet ──────────────────────────
        wallet_sol_change = 0
        for acc in account_data:
            if acc.get("account") == wallet_address:
                wallet_sol_change = acc.get("nativeBalanceChange", 0)
                break

        # Ignorer les variations purement dues aux frais (< 10_000 lamports)
        net_sol_change = wallet_sol_change
        if abs(net_sol_change) > 10_000:
            sol_amount = abs(net_sol_change) / LAMPORTS_PER_SOL
            direction  = "recu" if net_sol_change > 0 else "envoye"
            # Trouver l'autre compte
            other_acc = None
            for acc in account_data:
                if acc.get("account") != wallet_address:
                    chg = acc.get("nativeBalanceChange", 0)
                    # L'autre côté doit être de signe opposé
                    if (net_sol_change > 0 and chg < 0) or (net_sol_change < 0 and chg > 0):
                        other_acc = acc.get("account")
                        break
            events.append({
                "signature": sig,
                "date": date_str,
                "timestamp": ts,
                "tx_type": tx_type,
                "asset": "SOL",
                "mint": None,
                "amount": sol_amount,
                "direction": direction,
                "counterpart": other_acc,
            })

        # ── Tokens SPL ────────────────────────────────────────────────────
        for tr in token_transfers:
            mint   = tr.get("mint", "")
            amount = float(tr.get("tokenAmount", 0) or 0)
            if amount == 0:
                continue
            if tr.get("toUserAccount") == wallet_address and mint != SOL_MINT:
                events.append({
                    "signature": sig,
                    "date": date_str,
                    "timestamp": ts,
                    "tx_type": tx_type,
                    "asset": mint[:8] + "…",
                    "mint": mint,
                    "amount": amount,
                    "direction": "recu",
                    "counterpart": tr.get("fromUserAccount"),
                })
            elif tr.get("fromUserAccount") == wallet_address and mint != SOL_MINT:
                events.append({
                    "signature": sig,
                    "date": date_str,
                    "timestamp": ts,
                    "tx_type": tx_type,
                    "asset": mint[:8] + "…",
                    "mint": mint,
                    "amount": amount,
                    "direction": "envoye",
                    "counterpart": tr.get("toUserAccount"),
                })

    # ── Totaux résumés ────────────────────────────────────────────────────
    sol_recu    = sum(e["amount"] for e in events if e["direction"] == "recu"    and e["mint"] is None)
    sol_envoye  = sum(e["amount"] for e in events if e["direction"] == "envoye"  and e["mint"] is None)

    # Agrégation tokens (envoyés/reçus par mint)
    from collections import defaultdict
    tok_recu:   dict = defaultdict(float)
    tok_envoye: dict = defaultdict(float)
    for e in events:
        if e["mint"] is None:
            continue
        if e["direction"] == "recu":
            tok_recu[e["mint"]] += e["amount"]
        else:
            tok_envoye[e["mint"]] += e["amount"]

    return {
        "wallet":      wallet_address,
        "tx_analysed": len(txs),
        "summary": {
            "sol_recu":    round(sol_recu, 6),
            "sol_envoye":  round(sol_envoye, 6),
            "tokens_recus":   {k: round(v, 4) for k, v in tok_recu.items()},
            "tokens_envoyes": {k: round(v, 4) for k, v in tok_envoye.items()},
        },
        "events": sorted(events, key=lambda e: e["timestamp"], reverse=True),
    }


@app.get("/api/helius/activity/{wallet_address}")
async def helius_wallet_activity(wallet_address: str, limit: int = Query(20, ge=1, le=100)):
    """
    Retourne un résumé lisible des dernières transactions du wallet
    (swaps, transfers, NFT sales, staking…).
    """
    key = _helius_key()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{HELIUS_BASE}/addresses/{wallet_address}/transactions",
            params={"api-key": key, "limit": limit}
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=f"Helius erreur: {resp.text}")

    txs = resp.json()
    summary = []
    for tx in txs:
        ts = tx.get("timestamp", 0)
        summary.append({
            "signature": tx.get("signature"),
            "type": tx.get("type"),
            "description": tx.get("description", ""),
            "fee_sol": tx.get("fee", 0) / LAMPORTS_PER_SOL,
            "timestamp": ts,
            "date": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if ts else None,
            "source": tx.get("source", ""),
        })
    return {"wallet": wallet_address, "count": len(summary), "transactions": summary}


# =============================================================================

# === SYNC BALANCES DEPUIS LA BLOCKCHAIN ===
@app.post("/api/sync-balances/{wallet_address}")
async def sync_balances_from_chain(wallet_address: str):
    """
    Récupère les soldes réels on-chain via Helius et corrige current_tokens
    pour tous les tokens du wallet. Règle les cas où des ventes n'ont pas
    été capturées par l'import (limite de pages, transactions manquées, etc.)
    """
    key = _helius_key()
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(
            f"{HELIUS_BASE}/addresses/{wallet_address}/balances",
            params={"api-key": key}
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code,
                            detail=f"Helius erreur: {resp.text}")

    data = resp.json()
    # Soldes réels on-chain : mint → balance (déjà ajusté par decimals)
    on_chain: dict = {}
    for t in data.get("tokens", []):
        mint = t.get("mint", "")
        if not mint or mint in BLACKLISTED_MINTS:
            continue
        balance = _helius_token_ui_balance(t)
        if balance > 0:
            on_chain[mint] = balance

    updated = 0
    added = 0
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, address, name, current_tokens, purchased_tokens, sold_tokens FROM tokens WHERE wallet_address = ?",
            (wallet_address,)
        )
        tokens = cursor.fetchall()
        db_addresses = {t["address"] for t in tokens}

        # Ajouter les tokens on-chain absents de la BDD (airdrops, transferts, etc.)
        missing_mints = [m for m, bal in on_chain.items() if m not in db_addresses and bal > 0]
        if missing_mints:
            key = _helius_key()
            token_names = await _resolve_token_names(missing_mints, key)
            for mint in missing_mints:
                real_balance = on_chain[mint]
                name = token_names.get(mint) or (mint[:8] + "…")
                try:
                    cursor.execute("""
                        INSERT INTO tokens
                            (name, address, current_tokens, purchased_tokens, sold_tokens,
                             invested_amount, wallet_address)
                        VALUES (?, ?, ?, ?, 0, 0, ?)
                    """, (name, mint, real_balance, real_balance, wallet_address))
                    conn.commit()
                    added += 1
                    db_addresses.add(mint)
                except Exception as e:
                    if not is_unique_constraint_error(e):
                        raise
                    # Doublon (même mint + même wallet) ou schéma pas encore migré
                    conn.rollback()

        # Mettre à jour les soldes des tokens existants
        for tok in tokens:
            real_balance = on_chain.get(tok["address"], 0.0)
            old_current = tok["current_tokens"] or 0
            diff = abs(old_current - real_balance)
            threshold = max(1e-6, min(old_current, real_balance) * 0.0001) if (old_current and real_balance) else 1e-6
            if diff > threshold:
                extra_sold = max(0.0, old_current - real_balance)
                cursor.execute("""
                    UPDATE tokens SET
                        current_tokens = ?,
                        sold_tokens    = COALESCE(sold_tokens, 0) + ?,
                        updated_at     = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (real_balance, extra_sold, tok["id"]))
                updated += 1
        conn.commit()

    # Toujours invalider le cache pour avoir SOL + soldes à jour
    _invalidate_dashboard_cache(wallet_address)

    return {
        "synced": updated,
        "added": added,
        "wallet": wallet_address,
        "on_chain_tokens_found": len(on_chain)
    }


# === TOUTES LES TRANSACTIONS (ACHATS + VENTES) ===
@app.get("/api/all-transactions")
async def get_all_transactions(
    wallet: Optional[str] = Query(None),
    limit: Optional[int] = Query(None, ge=10, le=500),
    skip_hifo: bool = Query(False, description="Ne pas recalculer HIFO en live (lit hifo_* en BDD si cache valide)"),
):
    """
    Retourne tous les achats et toutes les ventes triés par date décroissante.
    skip_hifo=0 : simulation HIFO complète. skip_hifo=1 : PnL par vente depuis la BDD si le cache
    wallet est à jour (même empreinte que les achats/ventes), sinon champs PnL vides.
    """
    try:
        sol_usd = await _get_sol_usd_price()
    except Exception:
        sol_usd = 0

    if wallet is None or not str(wallet).strip():
        return []

    with get_db() as conn:
        cursor = conn.cursor()

        wallet_filter  = "AND t.wallet_address = ?"
        wallet_filter2 = "AND wallet_address = ?"
        params  = (wallet,)
        params2 = (wallet,)

        # ── Ventes ────────────────────────────────────────────────────────
        cursor.execute(f"""
            SELECT s.id as sale_id, 'sell' as tx_type, s.sale_date as tx_date,
                   t.id as token_id, t.name as token_name, t.address as token_address,
                   s.tokens_sold as token_amount, s.sale_price as price_sol,
                   s.sol_received as sol_amount, s.sol_usd_at_sale,
                   COALESCE(s.sale_timestamp, 0) as sale_ts,
                   COALESCE(s.sale_slot, 0) as sale_slot,
                   COALESCE(s.sale_timestamp, 0) as tx_timestamp,
                   s.transaction_signature,
                   s.hifo_buy_cost_usd, s.hifo_pnl_usd
            FROM sales s
            JOIN tokens t ON s.token_id = t.id
            WHERE 1=1 {wallet_filter}
            ORDER BY s.sale_date DESC
        """, params)
        sells_raw = [dict(r) for r in cursor.fetchall()]

        # ── Achats individuels (table purchases) ─────────────────────────
        cursor.execute(f"""
            SELECT p.id, 'buy' as tx_type, p.purchase_date as tx_date,
                   t.name as token_name, t.address as token_address,
                   p.tokens_bought as token_amount, p.purchase_price as price_sol,
                   p.sol_spent as sol_amount, p.sol_usd_at_buy as sol_usd_at_sale,
                   COALESCE(p.purchase_timestamp, 0) as tx_timestamp,
                   p.transaction_signature
            FROM purchases p
            JOIN tokens t ON p.token_id = t.id
            WHERE 1=1 {wallet_filter}
            ORDER BY p.purchase_date DESC
        """, params)
        buys = [dict(r) for r in cursor.fetchall()]

        gain_per_sale = {}
        fp = _wallet_hifo_fingerprint(cursor, wallet)
        cw = cursor.execute(
            "SELECT fingerprint FROM wallet_hifo_cache WHERE wallet_address = ?", (wallet,)
        ).fetchone()
        cache_hit = cw and cw["fingerprint"] == fp

        if skip_hifo and cache_hit:
            for s in sells_raw:
                sid = s["sale_id"]
                buy = s.get("hifo_buy_cost_usd")
                pnl = s.get("hifo_pnl_usd")
                rate = s.get("sol_usd_at_sale") or sol_usd
                sell_usd = (s.get("sol_amount") or 0) * rate
                gain_per_sale[sid] = {
                    "sell_usd": round(sell_usd, 4),
                    "buy_usd": round(buy, 4) if buy is not None else None,
                    "pnl_usd": round(pnl, 4) if pnl is not None else None,
                }
        elif not skip_hifo:
            gain_per_sale = _compute_hifo_gain_per_sale(cursor, wallet, sol_usd)

        # ── Construire le résultat final ──────────────────────────────────
        sells = []
        for s in sells_raw:
            rate = s.get("sol_usd_at_sale") or sol_usd
            s["amount_usd"] = (s.get("sol_amount") or 0) * rate
            s["price_usd"]  = (s.get("price_sol") or 0) * rate
            pnl             = gain_per_sale.get(s["sale_id"], {})
            s["pnl_usd"]    = pnl.get("pnl_usd")
            s["cost_usd"]   = pnl.get("buy_usd")
            sells.append(s)

        result = []
        for tx in buys:
            rate = tx.get("sol_usd_at_sale") or sol_usd
            tx["amount_usd"] = (tx.get("sol_amount") or 0) * rate
            tx["price_usd"]  = (tx.get("price_sol") or 0) * rate
            tx["pnl_usd"]    = None
            tx["cost_usd"]   = None
            result.append(tx)

        result += sells
        result.sort(key=lambda x: (x.get("tx_timestamp") or 0, x.get("tx_type") or ""), reverse=True)
        if limit:
            result = result[:limit]
        return result



# === FRONTEND STATIC FILES ===
# Priorité : React (app_proposer_par_kimi/dist) si buildé, sinon HTML classique
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_BACKEND_DIR))
REACT_DIST = os.path.join(_PROJECT_ROOT, "app_proposer_par_kimi", "dist")
HTML_FRONTEND = os.path.join(_BACKEND_DIR, "..", "frontend")
FRONTEND_DIR = REACT_DIST if os.path.isdir(REACT_DIST) else HTML_FRONTEND

if FRONTEND_DIR == REACT_DIST:
    print("[OK] Interface React (dist) — toutes les modifications récentes")
else:
    print("[OK] Interface HTML classique — lancez 'npm run build' dans app_proposer_par_kimi pour la version React")

# Désactiver le cache pour que les modifications soient prises en compte au refresh (F5)
@app.middleware("http")
async def no_cache_middleware(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path in ("/", "/index.html") or path.endswith(".js") or path.endswith(".css"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response

# Route racine : index.html
@app.get("/", include_in_schema=False)
async def root():
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    return FileResponse(index_path, media_type="text/html")

# Fichiers statiques (JS, CSS, assets)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, check_dir=False), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
