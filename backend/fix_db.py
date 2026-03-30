"""Script de correction : diviser par 2 les tokens doublés par un double-import."""
import sqlite3, os, sys

DB = os.path.join(os.path.dirname(__file__), "..", "data", "meme_coins.db")
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
c = conn.cursor()

print("=== AVANT ===")
c.execute("SELECT id, name, purchased_tokens, current_tokens, invested_amount FROM tokens ORDER BY id")
for r in c.fetchall():
    print(f"  [{r['id']}] {r['name'][:20]:20} | bought={r['purchased_tokens']:.4e} | curr={r['current_tokens']:.4e} | inv={r['invested_amount']:.4f} SOL")

# Correction : achats doublés → diviser par 2
# purchased_tokens /= 2
# current_tokens   = current_tokens - purchased_tokens/2  (= 2B-S - B = B-S)
# invested_amount  /= 2
# purchase_price inchangé (inv/purchased reste le même)
c.execute("""
    UPDATE tokens SET
        current_tokens   = current_tokens   - purchased_tokens / 2.0,
        purchased_tokens = purchased_tokens / 2.0,
        invested_amount  = invested_amount  / 2.0
""")
n = c.rowcount

# Créer table de suivi pour déduplication future des imports
c.execute("""
    CREATE TABLE IF NOT EXISTS imported_tx (
        signature   TEXT PRIMARY KEY,
        tx_type     TEXT NOT NULL,
        imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
""")

# Enregistrer les signatures de ventes déjà en BDD
c.execute("SELECT transaction_signature FROM sales WHERE transaction_signature IS NOT NULL")
sigs = [(r[0], "sell") for r in c.fetchall()]
c.executemany("INSERT OR IGNORE INTO imported_tx (signature, tx_type) VALUES (?, ?)", sigs)

conn.commit()

print(f"\n=== CORRECTION APPLIQUÉE ({n} tokens) ===")
c.execute("SELECT id, name, purchased_tokens, current_tokens, invested_amount FROM tokens ORDER BY id")
for r in c.fetchall():
    print(f"  [{r['id']}] {r['name'][:20]:20} | bought={r['purchased_tokens']:.4e} | curr={r['current_tokens']:.4e} | inv={r['invested_amount']:.4f} SOL")

c.execute("SELECT SUM(invested_amount) FROM tokens")
print(f"\nTotal investi: {c.fetchone()[0]:.4f} SOL")
conn.close()
print("OK")
