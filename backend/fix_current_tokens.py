import sqlite3, os

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "meme_coins.db")
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

rows_before = conn.execute(
    "SELECT name, current_tokens, purchased_tokens, sold_tokens FROM tokens ORDER BY name"
).fetchall()

print("=== AVANT ===")
problems = 0
for r in rows_before:
    purchased = r["purchased_tokens"] or 0
    sold      = r["sold_tokens"] or 0
    current   = r["current_tokens"] or 0
    expected  = max(0.0, purchased - sold)
    flag = " <-- PROBLEME" if abs(current - expected) > 0.01 else ""
    if flag:
        problems += 1
    print(f"  {r['name'][:22]:22s} | achetes={purchased:.4f} | vendus={sold:.4f} | current={current:.4f} | attendu={expected:.4f}{flag}")

print(f"\n{problems} tokens avec current_tokens incorrect\n")

# Correction
conn.execute("""
    UPDATE tokens
    SET current_tokens = MAX(0.0, purchased_tokens - COALESCE(sold_tokens, 0))
""")
conn.commit()

rows_after = conn.execute(
    "SELECT name, current_tokens, purchased_tokens, sold_tokens FROM tokens ORDER BY name"
).fetchall()

print("=== APRES CORRECTION ===")
for r in rows_after:
    purchased = r["purchased_tokens"] or 0
    sold      = r["sold_tokens"] or 0
    current   = r["current_tokens"] or 0
    print(f"  {r['name'][:22]:22s} | achetes={purchased:.4f} | vendus={sold:.4f} | current={current:.4f}")

conn.close()
print("\nCorrection appliquee avec succes.")
