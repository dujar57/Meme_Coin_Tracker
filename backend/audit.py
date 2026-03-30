import sqlite3, os

DB = os.path.join('..', 'data', 'meme_coins.db')
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

print("=== VENTES ===")
rows = conn.execute("""
    SELECT t.name, s.sale_date, s.tokens_sold, s.sale_price, s.sol_received, s.sol_usd_at_sale
    FROM sales s JOIN tokens t ON s.token_id = t.id
    ORDER BY s.sale_date
""").fetchall()
total_sol = 0
for r in rows:
    sol = r["sol_received"] or 0
    total_sol += sol
    sol_rate = r["sol_usd_at_sale"] or "?"
    print(f"  {(r['name'] or '')[:20]:20s} | {r['sale_date']} | {r['tokens_sold']:.0f} tok | {sol:.4f} SOL recus | taux_sol={sol_rate}")

print(f"\nTotal SOL recu sur toutes les ventes: {total_sol:.6f} SOL")

print()
print("=== TOKENS (investissement en SOL) ===")
rows2 = conn.execute(
    "SELECT name, invested_amount, sol_usd_at_buy, purchase_date, purchased_tokens FROM tokens ORDER BY invested_amount DESC"
).fetchall()
total_inv = 0
for r in rows2:
    inv = r["invested_amount"] or 0
    total_inv += inv
    print(f"  {(r['name'] or '')[:20]:20s} | {inv:.6f} SOL investis | taux_achat={r['sol_usd_at_buy'] or '?'} | date={r['purchase_date']}")
print(f"\nTotal investi: {total_inv:.6f} SOL")

conn.close()
