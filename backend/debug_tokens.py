import sqlite3, os

DB = os.path.join('..', 'data', 'meme_coins.db')
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

print("--- TOKENS ---")
rows = conn.execute(
    "SELECT name, current_tokens, purchased_tokens, sold_tokens, current_value, current_price "
    "FROM tokens ORDER BY current_value DESC"
).fetchall()

for r in rows:
    name = (r["name"] or "?")[:25]
    cur  = r["current_tokens"] or 0
    buy  = r["purchased_tokens"] or 0
    sold = r["sold_tokens"] or 0
    val  = r["current_value"] or 0
    prc  = r["current_price"] or 0
    print(f"  {name:25s} | current={cur:.4f} | achetes={buy:.4f} | vendus={sold:.4f} | value=${val:.2f} | price=${prc:.8f}")

total = conn.execute("SELECT SUM(current_value) as t FROM tokens").fetchone()["t"] or 0
print(f"\nTotal current_value en BDD: ${total:.2f}")

conn.close()
