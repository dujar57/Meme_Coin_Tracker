#!/usr/bin/env python3
"""
Script pour corriger les gains/pertes d'un wallet spécifique.
Usage: python fix_wallet_gains.py <adresse_wallet_solana>
"""
import sqlite3
import os
import sys

DB = os.path.join(os.path.dirname(__file__), "..", "data", "meme_coins.db")

if len(sys.argv) < 2 or not sys.argv[1] or len(sys.argv[1]) < 20:
    print("Usage: python fix_wallet_gains.py <adresse_wallet_solana>")
    sys.exit(1)

WALLET = sys.argv[1].strip()

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def main():
    conn = get_db()
    c = conn.cursor()

    # Récupérer le dernier sol_usd pour les conversions
    r = c.execute("SELECT sol_usd_at_buy FROM purchases WHERE sol_usd_at_buy > 0 ORDER BY rowid DESC LIMIT 1").fetchone()
    if not r:
        r = c.execute("SELECT sol_usd_at_sale FROM sales WHERE sol_usd_at_sale > 0 ORDER BY rowid DESC LIMIT 1").fetchone()
    sol_usd = float(r["sol_usd_at_buy"] if r and "sol_usd_at_buy" in r.keys() else (r["sol_usd_at_sale"] if r else 150.0))

    c.execute("SELECT id, current_tokens, current_price, current_value, invested_amount, sol_usd_at_buy FROM tokens WHERE wallet_address = ?", (WALLET,))
    tokens = c.fetchall()

    for token in tokens:
        token_id = token["id"]
        current_value_usd = token["current_value"] or (token["current_tokens"] * token["current_price"]) if token["current_price"] else 0

        # Lots HIFO
        c.execute("""
            SELECT purchase_timestamp, tokens_bought, sol_spent,
                   COALESCE(NULLIF(sol_usd_at_buy, 0), ?) as sol_rate_buy
            FROM purchases
            WHERE token_id = ? AND tokens_bought > 0 AND sol_spent > 0
            ORDER BY (sol_spent / tokens_bought) * COALESCE(NULLIF(sol_usd_at_buy, 0), ?) DESC,
                     purchase_timestamp ASC
        """, (sol_usd, token_id, sol_usd))
        purchase_lots = [dict(r) for r in c.fetchall()]
        for lot in purchase_lots:
            lot["remaining"] = lot["tokens_bought"]
            lot["price_usd"] = (lot["sol_spent"] / lot["tokens_bought"]) * lot["sol_rate_buy"] if lot["tokens_bought"] else 0

        # Ventes
        c.execute("""
            SELECT tokens_sold, sol_received,
                   COALESCE(sale_timestamp, 0) as sale_ts,
                   COALESCE(NULLIF(sol_usd_at_sale, 0), ?) as sol_rate_sell
            FROM sales WHERE token_id = ?
            ORDER BY COALESCE(sale_timestamp, 0) ASC
        """, (sol_usd, token_id))
        token_sales = c.fetchall()

        realized_cost_usd = 0.0
        sales_usd = 0.0
        for s in token_sales:
            tokens_left = s["tokens_sold"]
            sale_ts = s["sale_ts"]
            sales_usd += (s["sol_received"] or 0) * s["sol_rate_sell"]

            eligible = sorted(
                [l for l in purchase_lots if sale_ts == 0 or (l["purchase_timestamp"] or 0) <= sale_ts],
                key=lambda l: l["price_usd"], reverse=True
            )
            for lot in eligible:
                if tokens_left <= 0:
                    break
                consume = min(lot["remaining"], tokens_left)
                ratio = consume / lot["tokens_bought"] if lot["tokens_bought"] else 0
                realized_cost_usd += lot["sol_spent"] * ratio * lot["sol_rate_buy"]
                lot["remaining"] -= consume
                tokens_left -= consume

        # Coût total investi
        if purchase_lots:
            total_invested_usd = sum(l["sol_spent"] * l["sol_rate_buy"] for l in purchase_lots)
        else:
            inv = token["invested_amount"] or 0
            sol_at = token["sol_usd_at_buy"]
            total_invested_usd = inv * sol_at if sol_at else inv

        total_value_usd = current_value_usd + sales_usd
        profit_loss = total_value_usd - total_invested_usd
        gain = max(0.0, profit_loss)
        loss = abs(min(0.0, profit_loss))

        c.execute("UPDATE tokens SET gain=?, loss=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (gain, loss, token_id))
        print(f"  Token {token_id}: gain={gain:.2f}, loss={loss:.2f}")

    conn.commit()
    conn.close()
    print(f"\nOK — Gains/pertes corrigés pour {WALLET}")

if __name__ == "__main__":
    main()
