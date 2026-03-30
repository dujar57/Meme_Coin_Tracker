"""
Script de migration SQLite → PostgreSQL
Transfère toutes les données de la base locale vers PostgreSQL en ligne
"""
import sqlite3
import psycopg2
from psycopg2.extras import execute_values, RealDictCursor
import os
from dotenv import load_dotenv

# Charger la configuration
load_dotenv()

SQLITE_PATH = os.getenv('SQLITE_DB_PATH', '../data/meme_coins.db')
DATABASE_URL = os.getenv('DATABASE_URL')
POSTGRES_HOST = os.getenv('POSTGRES_HOST')
POSTGRES_PORT = os.getenv('POSTGRES_PORT', '5432')
POSTGRES_DB = os.getenv('POSTGRES_DB')
POSTGRES_USER = os.getenv('POSTGRES_USER')
POSTGRES_PASSWORD = os.getenv('POSTGRES_PASSWORD')

def get_postgres_connection():
    """Connexion PostgreSQL"""
    if DATABASE_URL:
        return psycopg2.connect(DATABASE_URL)
    else:
        return psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            sslmode='require'
        )

def migrate_table(sqlite_cursor, pg_cursor, table_name, id_column='id'):
    """Migre une table complète"""
    print(f"\n📊 Migration de la table '{table_name}'...")
    
    # Récupérer toutes les données de SQLite
    sqlite_cursor.execute(f"SELECT * FROM {table_name}")
    rows = sqlite_cursor.fetchall()
    
    if not rows:
        print(f"⚠️  Aucune donnée dans '{table_name}'")
        return 0
    
    # Obtenir les noms de colonnes
    columns = [description[0] for description in sqlite_cursor.description]
    print(f"📋 Colonnes: {', '.join(columns)}")
    
    # Convertir en liste de tuples
    values = [tuple(dict(row).values() for k in columns) for row in rows]
    
    # Construire la requête INSERT
    placeholders = ','.join(['%s'] * len(columns))
    column_list = ','.join(columns)
    
    # Gérer les conflits (éviter les doublons)
    if table_name == 'tokens':
        conflict_clause = "ON CONFLICT (address) DO UPDATE SET name = EXCLUDED.name"
    elif id_column:
        conflict_clause = f"ON CONFLICT ({id_column}) DO NOTHING"
    else:
        conflict_clause = ""
    
    query = f"""
        INSERT INTO {table_name} ({column_list})
        VALUES %s
        {conflict_clause}
    """
    
    try:
        # Insérer par batch de 100
        batch_size = 100
        total_inserted = 0
        
        for i in range(0, len(rows), batch_size):
            batch = [tuple(dict(row).values()) for row in rows[i:i+batch_size]]
            execute_values(pg_cursor, query, batch, template=f"({placeholders})")
            total_inserted += len(batch)
            print(f"✅ {total_inserted}/{len(rows)} lignes insérées...")
        
        print(f"✅ Table '{table_name}': {len(rows)} lignes migrées")
        return len(rows)
        
    except Exception as e:
        print(f"❌ Erreur lors de la migration de '{table_name}': {e}")
        return 0

def verify_migration(sqlite_cursor, pg_cursor, table_name):
    """Vérifie que la migration est correcte"""
    print(f"\n🔍 Vérification de '{table_name}'...")
    
    # Compter dans SQLite
    sqlite_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    sqlite_count = sqlite_cursor.fetchone()[0]
    
    # Compter dans PostgreSQL
    pg_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    pg_count = pg_cursor.fetchone()[0]
    
    if sqlite_count == pg_count:
        print(f"✅ {table_name}: {pg_count} lignes (OK)")
        return True
    else:
        print(f"⚠️ {table_name}: SQLite={sqlite_count}, PostgreSQL={pg_count} (DIFFÉRENT)")
        return False

def main():
    """Fonction principale de migration"""
    print("=" * 60)
    print("🔄 MIGRATION SQLite → PostgreSQL")
    print("=" * 60)
    
    # Vérifications préliminaires
    if not os.path.exists(SQLITE_PATH):
        print(f"❌ Base SQLite non trouvée: {SQLITE_PATH}")
        return
    
    if not DATABASE_URL and not all([POSTGRES_HOST, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD]):
        print("❌ Configuration PostgreSQL manquante dans .env")
        print("   Ajoutez DATABASE_URL ou POSTGRES_HOST/DB/USER/PASSWORD")
        return
    
    print(f"📂 Source: {SQLITE_PATH}")
    print(f"🎯 Cible: PostgreSQL ({POSTGRES_HOST or 'DATABASE_URL'})")
    
    # Demander confirmation
    confirm = input("\n⚠️  Cette opération va copier toutes les données. Continuer ? (oui/non): ")
    if confirm.lower() not in ['oui', 'o', 'yes', 'y']:
        print("❌ Migration annulée")
        return
    
    try:
        # Connexion SQLite
        print("\n📡 Connexion à SQLite...")
        sqlite_conn = sqlite3.connect(SQLITE_PATH)
        sqlite_conn.row_factory = sqlite3.Row
        sqlite_cursor = sqlite_conn.cursor()
        print("✅ SQLite connecté")
        
        # Connexion PostgreSQL
        print("📡 Connexion à PostgreSQL...")
        pg_conn = get_postgres_connection()
        pg_cursor = pg_conn.cursor(cursor_factory=RealDictCursor)
        print("✅ PostgreSQL connecté")
        
        # Initialiser les tables PostgreSQL
        print("\n🏗️  Création des tables PostgreSQL...")
        from database import init_db_postgres
        init_db_postgres()
        
        # Migration des tables
        total_migrated = 0
        
        # 1. Tokens (d'abord car référencé par les autres)
        total_migrated += migrate_table(sqlite_cursor, pg_cursor, 'tokens', 'id')
        pg_conn.commit()
        
        # 2. Sales
        total_migrated += migrate_table(sqlite_cursor, pg_cursor, 'sales', 'id')
        pg_conn.commit()
        
        # 3. Price History
        try:
            total_migrated += migrate_table(sqlite_cursor, pg_cursor, 'price_history', 'id')
            pg_conn.commit()
        except Exception as e:
            print(f"⚠️  Table price_history ignorée: {e}")
        
        # Vérification
        print("\n" + "=" * 60)
        print("🔍 VÉRIFICATION DE LA MIGRATION")
        print("=" * 60)
        
        all_ok = True
        all_ok &= verify_migration(sqlite_cursor, pg_cursor, 'tokens')
        all_ok &= verify_migration(sqlite_cursor, pg_cursor, 'sales')
        
        try:
            all_ok &= verify_migration(sqlite_cursor, pg_cursor, 'price_history')
        except:
            pass
        
        # Résumé
        print("\n" + "=" * 60)
        if all_ok:
            print("✅ MIGRATION RÉUSSIE !")
            print(f"📊 Total: {total_migrated} lignes migrées")
            print("\n💡 Prochaines étapes:")
            print("   1. Vérifiez les données dans PostgreSQL")
            print("   2. Modifiez DATABASE_MODE=postgres dans .env")
            print("   3. Relancez le backend: python main.py")
        else:
            print("⚠️  MIGRATION TERMINÉE AVEC AVERTISSEMENTS")
            print("   Vérifiez manuellement les données")
        print("=" * 60)
        
        # Fermeture
        sqlite_conn.close()
        pg_conn.close()
        
    except Exception as e:
        print(f"\n❌ ERREUR DE MIGRATION: {e}")
        print("\n💡 Solutions:")
        print("   - Vérifiez DATABASE_URL dans .env")
        print("   - Vérifiez la connexion internet")
        print("   - Vérifiez les credentials PostgreSQL")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
