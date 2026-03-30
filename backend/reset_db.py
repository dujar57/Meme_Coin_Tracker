#!/usr/bin/env python3
"""
Vide complètement la base de données.
Usage: python reset_db.py
"""
import sqlite3
import os

DB = os.path.join(os.path.dirname(__file__), "..", "data", "meme_coins.db")

def main():
    if not os.path.exists(DB):
        print(f"Base non trouvée: {DB}")
        return

    conn = sqlite3.connect(DB)
    c = conn.cursor()

    # Désactiver les foreign keys pour SQLite
    c.execute("PRAGMA foreign_keys = OFF")

    # Ordre de suppression (tables enfants avant parents)
    tables = [
        "token_notes",
        "token_targets",
        "price_history",
        "sales",
        "purchases",
        "imported_tx",
        "tokens",
        "wallets",
    ]

    for table in tables:
        try:
            c.execute(f"DELETE FROM {table}")
            n = c.rowcount
            print(f"  {table}: {n} lignes supprimées")
        except sqlite3.OperationalError as e:
            if "no such table" in str(e):
                print(f"  {table}: (table absente)")
            else:
                raise

    # Réinitialiser settings (garder la structure)
    try:
        c.execute("DELETE FROM settings")
        print(f"  settings: vidé")
    except sqlite3.OperationalError:
        pass

    c.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    conn.close()
    print("\nOK — Base de données vidée")

if __name__ == "__main__":
    main()
