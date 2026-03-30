"""
Gestionnaire de base de données unifié pour SQLite et PostgreSQL
Supporte le mode local (SQLite) et en ligne (PostgreSQL)
"""
import os
import sqlite3
from contextlib import contextmanager
from typing import Optional
from dotenv import load_dotenv

# Charger les variables d'environnement
load_dotenv()

# Configuration
DATABASE_MODE = os.getenv('DATABASE_MODE', 'sqlite')

# Chemin SQLite - utiliser chemin absolu basé sur la position de ce fichier
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
DEFAULT_SQLITE_PATH = os.path.join(PROJECT_ROOT, 'data', 'meme_coins.db')
SQLITE_DB_PATH = os.getenv('SQLITE_DB_PATH', DEFAULT_SQLITE_PATH)

# Créer le dossier data s'il n'existe pas
os.makedirs(os.path.dirname(SQLITE_DB_PATH), exist_ok=True)

# PostgreSQL
POSTGRES_HOST = os.getenv('POSTGRES_HOST', 'localhost')
POSTGRES_PORT = os.getenv('POSTGRES_PORT', '5432')
POSTGRES_DB = os.getenv('POSTGRES_DB', 'meme_coins')
POSTGRES_USER = os.getenv('POSTGRES_USER', 'postgres')
POSTGRES_PASSWORD = os.getenv('POSTGRES_PASSWORD', '')
POSTGRES_SSLMODE = os.getenv('POSTGRES_SSLMODE', 'prefer')
DATABASE_URL = os.getenv('DATABASE_URL', None)

print(f"🔧 Mode base de données: {DATABASE_MODE}")

# === SQLite (mode actuel) ===
@contextmanager
def get_db_sqlite():
    """Connexion SQLite (mode local)"""
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

# === PostgreSQL (mode en ligne) ===
def get_postgres_connection():
    """Crée une connexion PostgreSQL"""
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        
        if DATABASE_URL:
            # Utiliser DATABASE_URL directement (pratique pour déploiement)
            conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        else:
            # Construire la connexion depuis les variables
            conn = psycopg2.connect(
                host=POSTGRES_HOST,
                port=POSTGRES_PORT,
                dbname=POSTGRES_DB,
                user=POSTGRES_USER,
                password=POSTGRES_PASSWORD,
                sslmode=POSTGRES_SSLMODE,
                cursor_factory=RealDictCursor
            )
        return conn
    except ImportError:
        raise Exception("psycopg2 non installé. Installez-le avec: pip install psycopg2-binary")
    except Exception as e:
        raise Exception(f"Erreur de connexion PostgreSQL: {str(e)}")

@contextmanager
def get_db_postgres():
    """Connexion PostgreSQL (mode en ligne)"""
    conn = get_postgres_connection()
    try:
        yield conn
    finally:
        conn.close()

# === Interface unifiée ===
@contextmanager
def get_db():
    """
    Connexion unifiée - utilise SQLite ou PostgreSQL selon la configuration
    """
    if DATABASE_MODE == 'postgres':
        with get_db_postgres() as conn:
            yield conn
    else:
        with get_db_sqlite() as conn:
            yield conn

# === Initialisation des tables ===
def init_db():
    """Initialise les tables selon le mode de base de données"""
    
    if DATABASE_MODE == 'postgres':
        init_db_postgres()
    else:
        init_db_sqlite()

def init_db_sqlite():
    """Initialise SQLite (mode actuel)"""
    with get_db_sqlite() as conn:
        cursor = conn.cursor()
        
        # Table des tokens
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                address TEXT UNIQUE NOT NULL,
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
                sale_timestamp INTEGER,
                tokens_sold REAL NOT NULL,
                sale_price REAL NOT NULL,
                sale_amount REAL NOT NULL,
                sol_received REAL DEFAULT 0,
                real_price_usd REAL DEFAULT 0,
                transaction_signature TEXT,
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
        
        conn.commit()
        print("✅ SQLite initialisé")

def init_db_postgres():
    """Initialise PostgreSQL (mode en ligne)"""
    with get_db_postgres() as conn:
        cursor = conn.cursor()
        
        # Table des tokens
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tokens (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                address VARCHAR(255) UNIQUE NOT NULL,
                detection_date VARCHAR(50),
                comments TEXT,
                event VARCHAR(255),
                mcap_target VARCHAR(100),
                purchase_date VARCHAR(50),
                current_tokens DECIMAL(20, 8) DEFAULT 0,
                purchased_tokens DECIMAL(20, 8) DEFAULT 0,
                purchase_price DECIMAL(20, 8) DEFAULT 0,
                current_price DECIMAL(20, 8) DEFAULT 0,
                loss DECIMAL(20, 8) DEFAULT 0,
                gain DECIMAL(20, 8) DEFAULT 0,
                current_value DECIMAL(20, 8) DEFAULT 0,
                invested_amount DECIMAL(20, 8) DEFAULT 0,
                sold_tokens DECIMAL(20, 8) DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Table des ventes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sales (
                id SERIAL PRIMARY KEY,
                token_id INTEGER NOT NULL,
                sale_date VARCHAR(50) NOT NULL,
                sale_timestamp BIGINT,
                tokens_sold DECIMAL(20, 8) NOT NULL,
                sale_price DECIMAL(20, 8) NOT NULL,
                sale_amount DECIMAL(20, 8) NOT NULL,
                sol_received DECIMAL(20, 8) DEFAULT 0,
                real_price_usd DECIMAL(20, 8) DEFAULT 0,
                transaction_signature VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (token_id) REFERENCES tokens (id) ON DELETE CASCADE
            )
        """)
        
        # Table des prix historiques
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id SERIAL PRIMARY KEY,
                token_id INTEGER NOT NULL,
                price DECIMAL(20, 8) NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (token_id) REFERENCES tokens (id) ON DELETE CASCADE
            )
        """)
        
        # Index pour performance
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tokens_address ON tokens(address)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_sales_token_id ON sales(token_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_sales_signature ON sales(transaction_signature)
        """)
        
        conn.commit()
        print("✅ PostgreSQL initialisé")

# === Utilitaires ===
def test_connection():
    """Teste la connexion à la base de données"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            if DATABASE_MODE == 'postgres':
                cursor.execute("SELECT version()")
                version = cursor.fetchone()
                print(f"✅ PostgreSQL connecté: {version}")
            else:
                cursor.execute("SELECT sqlite_version()")
                version = cursor.fetchone()
                print(f"✅ SQLite connecté: {version[0]}")
            return True
    except Exception as e:
        print(f"❌ Erreur de connexion: {e}")
        return False

if __name__ == "__main__":
    print("🔍 Test de connexion à la base de données...")
    test_connection()
