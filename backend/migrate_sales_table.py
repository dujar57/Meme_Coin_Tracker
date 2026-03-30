"""
Script de migration pour ajouter les nouvelles colonnes à la table sales.

Obsolète pour une install à jour : `main.py` applique les mêmes colonnes au démarrage.
À n’utiliser que sur une très vieille base créée avant ces migrations.
"""
import sqlite3
import os

DB_PATH = "../data/meme_coins.db"

def migrate_sales_table():
    """Ajoute les nouvelles colonnes à la table sales si elles n'existent pas"""
    
    if not os.path.exists(DB_PATH):
        print("❌ Base de données non trouvée!")
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Vérifier les colonnes existantes
        cursor.execute("PRAGMA table_info(sales)")
        columns = [col[1] for col in cursor.fetchall()]
        print(f"📊 Colonnes actuelles: {columns}")
        
        # Ajouter les nouvelles colonnes si elles n'existent pas
        new_columns = {
            'sale_timestamp': 'INTEGER',
            'sol_received': 'REAL DEFAULT 0',
            'real_price_usd': 'REAL DEFAULT 0',
            'transaction_signature': 'TEXT'
        }
        
        for col_name, col_type in new_columns.items():
            if col_name not in columns:
                try:
                    cursor.execute(f"ALTER TABLE sales ADD COLUMN {col_name} {col_type}")
                    print(f"✅ Colonne '{col_name}' ajoutée")
                except sqlite3.OperationalError as e:
                    print(f"⚠️  Erreur ou colonne déjà existante: {col_name}")
        
        conn.commit()
        print("✅ Migration terminée avec succès!")
        
        # Afficher la nouvelle structure
        cursor.execute("PRAGMA table_info(sales)")
        columns = cursor.fetchall()
        print("\n📋 Structure finale de la table 'sales':")
        for col in columns:
            print(f"  - {col[1]} ({col[2]})")
        
    except Exception as e:
        print(f"❌ Erreur de migration: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    print("🔄 Migration de la table 'sales'...")
    migrate_sales_table()
