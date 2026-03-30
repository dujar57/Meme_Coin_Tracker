#!/usr/bin/env python3
"""
Vide toutes les données des tables SQLite en conservant le schéma (CREATE TABLE, index).

Usage (depuis le dossier backend) :
  python wipe_database_data.py              # demande confirmation
  python wipe_database_data.py --yes        # sans confirmation (irréversible)

Après exécution : redémarrer le serveur (main.py) pour vider les caches mémoire.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

_BACK = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.normpath(os.path.join(_BACK, "..", "data", "meme_coins.db"))


def wipe_data(db_path: str) -> tuple[int, list[str]]:
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"Base introuvable : {db_path}")

    conn = sqlite3.connect(db_path, timeout=60)
    try:
        c = conn.cursor()
        c.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )
        tables = [r[0] for r in c.fetchall()]
        if not tables:
            return 0, []

        c.execute("PRAGMA foreign_keys=OFF")
        total_deleted = 0
        for t in tables:
            try:
                c.execute(f'DELETE FROM "{t}"')
                total_deleted += c.rowcount if c.rowcount is not None else 0
            except sqlite3.Error as e:
                conn.rollback()
                c.execute("PRAGMA foreign_keys=ON")
                raise RuntimeError(f"Échec sur la table {t!r}: {e}") from e

        try:
            c.execute("DELETE FROM sqlite_sequence")
        except sqlite3.Error:
            pass

        conn.commit()
        c.execute("PRAGMA foreign_keys=ON")

        # Réduit le fichier ; à faire hors transaction longue
        conn.execute("VACUUM")
        conn.commit()

        return total_deleted, tables
    finally:
        conn.close()


def main() -> int:
    p = argparse.ArgumentParser(description="Supprime toutes les lignes de la BDD (schéma inchangé).")
    p.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Ne pas demander confirmation (destructif).",
    )
    args = p.parse_args()

    print(f"Fichier cible : {DB_PATH}")
    if not args.yes:
        s = input("Effacer TOUTES les données ? Tapez OUI en majuscules : ").strip()
        if s != "OUI":
            print("Annulé.")
            return 1

    try:
        _, tables = wipe_data(DB_PATH)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 2
    except Exception as e:
        print(f"Erreur : {e}", file=sys.stderr)
        return 3

    print(f"OK — {len(tables)} table(s) vidée(s) : {', '.join(tables)}")
    print("Redémarrez python main.py pour repartir à zéro côté caches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
