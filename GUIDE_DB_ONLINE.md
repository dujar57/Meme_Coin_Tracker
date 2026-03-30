# 🚀 Guide : Passer à une Base de Données en Ligne

## 📋 Table des matières
1. [Pourquoi passer en ligne ?](#pourquoi)
2. [Services recommandés](#services)
3. [Configuration étape par étape](#configuration)
4. [Migration des données](#migration)
5. [Déploiement complet](#deploiement)

---

## 🤔 Pourquoi passer à une base de données en ligne ?

### Avantages
✅ **Accès de n'importe où** : Vos données accessibles depuis tous vos appareils  
✅ **Sauvegardes automatiques** : Plus de risque de perte  
✅ **Performance** : Meilleure pour de gros volumes  
✅ **Scalabilité** : Supporte plus d'utilisateurs simultanés  
✅ **Déploiement** : Nécessaire pour mettre l'app en ligne  

### Inconvénients
⚠️ **Coût** : Les services gratuits ont des limites  
⚠️ **Connexion internet** : Nécessaire pour accéder aux données  
⚠️ **Configuration** : Un peu plus complexe au début  

---

## 🏆 Services Recommandés (Gratuits pour commencer)

### 1. **Supabase** ⭐ RECOMMANDÉ
- ✅ **PostgreSQL gratuit** : 500 MB
- ✅ **Très simple** : Interface visuelle
- ✅ **500 MB gratuit** : Amplement suffisant
- ✅ **Authentification incluse** : Pour multi-utilisateurs
- 🌐 [supabase.com](https://supabase.com)

**Limites gratuites** : 500 MB, 2 Go de bande passante/mois

---

### 2. **Railway.app** 💪 PUISSANT
- ✅ **PostgreSQL gratuit** : $5 de crédit/mois
- ✅ **Déploiement 1-click** : Backend + DB ensemble
- ✅ **Très rapide** : Excellente performance
- 🌐 [railway.app](https://railway.app)

**Limites gratuites** : $5/mois de crédit (≈ 500h d'utilisation)

---

### 3. **Render** 🎨 SIMPLE
- ✅ **PostgreSQL gratuit** : 1 GB
- ✅ **Auto-déploiement** : Depuis GitHub
- ✅ **SSL inclus** : Sécurisé par défaut
- 🌐 [render.com](https://render.com)

**Limites gratuites** : 1 GB, s'endort après inactivité

---

### 4. **Neon** ⚡ MODERNE
- ✅ **PostgreSQL serverless** : 3 GB
- ✅ **Ultra-rapide** : Scaling automatique
- ✅ **3 GB gratuit** : Généreux
- 🌐 [neon.tech](https://neon.tech)

**Limites gratuites** : 3 GB, 100 heures de compute/mois

---

### 5. **ElephantSQL** 🐘 CLASSIQUE
- ✅ **PostgreSQL dédié** : 20 MB gratuit
- ✅ **Très stable** : Existant depuis longtemps
- ⚠️ **20 MB seulement** : Peut être juste
- 🌐 [elephantsql.com](https://elephantsql.com)

---

## 🛠️ Configuration Étape par Étape

### Option 1: Supabase (Recommandé) ⭐

#### 1. Créer un compte Supabase
```
1. Allez sur https://supabase.com
2. Cliquez sur "Start your project"
3. Connectez-vous avec GitHub (ou email)
```

#### 2. Créer un projet
```
1. Cliquez sur "New project"
2. Nom: meme-coin-tracker
3. Database Password: [Choisissez un mot de passe fort]
4. Region: Europe West (ou proche de vous)
5. Plan: Free
6. Cliquez "Create new project"
```

#### 3. Récupérer les informations de connexion
```
1. Dans votre projet Supabase, allez dans "Settings" (⚙️)
2. Cliquez sur "Database"
3. Scrollez jusqu'à "Connection string"
4. Copiez l'URL sous "URI" 

Exemple :
postgresql://postgres.[votre-ref]:[votre-password]@db.[votre-ref].supabase.co:5432/postgres
```

#### 4. Configurer votre application

**Créez un fichier `.env`** dans le dossier `backend/` :

```bash
# Copiez .env.example et renommez en .env
cp .env.example .env
```

**Éditez le fichier `.env`** :

```env
# Mode PostgreSQL
DATABASE_MODE=postgres

# URL Supabase (remplacez par la vôtre)
DATABASE_URL=postgresql://postgres.[ref]:[password]@db.[ref].supabase.co:5432/postgres

# API Configuration
API_PORT=8000
API_HOST=0.0.0.0
```

#### 5. Installer les dépendances PostgreSQL

```bash
cd backend
pip install psycopg2-binary python-dotenv sqlalchemy
```

Ou installez tout depuis requirements.txt :

```bash
pip install -r requirements.txt
```

#### 6. Initialiser la base de données

```bash
python database.py
```

Vous devriez voir :
```
🔧 Mode base de données: postgres
🔍 Test de connexion à la base de données...
✅ PostgreSQL connecté: PostgreSQL 15.x
✅ PostgreSQL initialisé
```

#### 7. Lancer le backend

```bash
python main.py
```

---

### Option 2: Railway.app 🚂

#### 1. Créer un compte Railway
```
1. Allez sur https://railway.app
2. Cliquez "Start a New Project"
3. Connectez-vous avec GitHub
```

#### 2. Créer une base PostgreSQL
```
1. Cliquez "+ New" → "Database" → "PostgreSQL"
2. Railway crée automatiquement la base
```

#### 3. Récupérer l'URL de connexion
```
1. Cliquez sur votre base PostgreSQL
2. Allez dans "Connect"
3. Copiez "DATABASE_URL"

Exemple:
postgresql://postgres:password@containers-us-west-xxx.railway.app:6543/railway
```

#### 4. Configurer votre `.env`
```env
DATABASE_MODE=postgres
DATABASE_URL=postgresql://postgres:password@containers-us-west-xxx.railway.app:6543/railway
```

#### 5. Suivre les étapes 5-7 de Supabase

---

### Option 3: Render 🎨

#### 1. Créer un compte Render
```
1. https://render.com
2. Sign up with GitHub
```

#### 2. Créer une base PostgreSQL
```
1. Dashboard → New → PostgreSQL
2. Name: meme-coin-db
3. Database: meme_coins
4. User: postgres
5. Region: Frankfurt (ou proche)
6. Plan: Free
7. Create Database
```

#### 3. Récupérer les informations
```
Dans PostgreSQL Info:
- Internal Database URL (pour déployer sur Render)
- External Database URL (pour développement local)

Copiez "External Database URL"
```

#### 4. Configuration `.env`
```env
DATABASE_MODE=postgres
DATABASE_URL=postgresql://postgres:[password]@[host]/[db]?ssl=true
```

---

## 📦 Migration des Données SQLite → PostgreSQL

### Script automatique

Créez `migrate_to_postgres.py` :

```python
import sqlite3
import psycopg2
from psycopg2.extras import execute_values
import os
from dotenv import load_dotenv

load_dotenv()

SQLITE_PATH = "../data/meme_coins.db"
POSTGRES_URL = os.getenv('DATABASE_URL')

def migrate():
    # Connexion SQLite
    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cursor = sqlite_conn.cursor()
    
    # Connexion PostgreSQL
    pg_conn = psycopg2.connect(POSTGRES_URL)
    pg_cursor = pg_conn.cursor()
    
    print("🔄 Migration SQLite → PostgreSQL")
    
    # Migrer tokens
    sqlite_cursor.execute("SELECT * FROM tokens")
    tokens = sqlite_cursor.fetchall()
    
    if tokens:
        columns = tokens[0].keys()
        values = [tuple(dict(row).values()) for row in tokens]
        
        query = f"INSERT INTO tokens ({','.join(columns)}) VALUES %s ON CONFLICT (address) DO NOTHING"
        execute_values(pg_cursor, query, values)
        print(f"✅ {len(tokens)} tokens migrés")
    
    # Migrer sales
    sqlite_cursor.execute("SELECT * FROM sales")
    sales = sqlite_cursor.fetchall()
    
    if sales:
        columns = sales[0].keys()
        values = [tuple(dict(row).values()) for row in sales]
        
        query = f"INSERT INTO sales ({','.join(columns)}) VALUES %s"
        execute_values(pg_cursor, query, values)
        print(f"✅ {len(sales)} ventes migrées")
    
    pg_conn.commit()
    
    sqlite_conn.close()
    pg_conn.close()
    
    print("✅ Migration terminée!")

if __name__ == "__main__":
    migrate()
```

**Lancer la migration** :
```bash
python migrate_to_postgres.py
```

---

## 🚀 Déploiement Complet (Backend + Frontend + DB)

### Solution tout-en-un : Render

#### 1. Backend sur Render

**Fichier `render.yaml`** (à la racine) :

```yaml
services:
  - type: web
    name: meme-coin-api
    env: python
    buildCommand: "cd backend && pip install -r requirements.txt"
    startCommand: "cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT"
    envVars:
      - key: DATABASE_MODE
        value: postgres
      - key: DATABASE_URL
        fromDatabase:
          name: meme-coin-db
          property: connectionString

databases:
  - name: meme-coin-db
    databaseName: meme_coins
    user: postgres
```

**Déployer** :
```
1. Push sur GitHub
2. Render → New → Blueprint
3. Connecter votre repo
4. Render déploie automatiquement !
```

#### 2. Frontend

**Sur Netlify/Vercel** :
```
1. Mettez le dossier frontend/ sur GitHub
2. Netlify → New site from Git
3. Build command: (aucune)
4. Publish directory: frontend
5. Deploy!
```

**Mettez à jour `API_URL` dans `app.js`** :
```javascript
const API_URL = 'https://votre-api.onrender.com/api';
```

---

## ✅ Checklist Finale

- [ ] Compte créé sur service choisi (Supabase/Railway/Render)
- [ ] Base PostgreSQL créée
- [ ] `.env` configuré avec DATABASE_URL
- [ ] Dépendances installées (`pip install -r requirements.txt`)
- [ ] Test connexion OK (`python database.py`)
- [ ] Tables créées (`python main.py` démarrage)
- [ ] Migration données effectuée (si nécessaire)
- [ ] Backend démarre sans erreur
- [ ] Frontend connecté à la nouvelle API

---

## 🆘 Dépannage

### Erreur: "psycopg2 not found"
```bash
pip install psycopg2-binary
```

### Erreur: "connection refused"
- Vérifiez que `DATABASE_URL` est correct
- Vérifiez les règles firewall (certains réseaux bloquent PostgreSQL)
- Utilisez `sslmode=require` dans l'URL

### Erreur: "SSL required"
Ajoutez à la fin de l'URL :
```
?sslmode=require
```

### Performance lente
- Utilisez un serveur proche géographiquement
- Passez à un plan payant pour plus de CPU
- Ajoutez des index sur les colonnes fréquemment recherchées

---

## 💰 Coûts Estimés

| Service | Plan Gratuit | Suffisant pour |
|---------|-------------|----------------|
| Supabase | 500 MB | ~10,000 tokens |
| Railway | $5/mois | ~500h utilisation |
| Render | 1 GB | ~50,000 tokens |
| Neon | 3 GB | ~150,000 tokens |

**Pour démarrer** : Le plan gratuit suffit amplement !

**Pour scaling** : $5-10/mois pour usage intensif

---

## 🎯 Prochaines Étapes

1. ✅ Choisir un service (recommandé: Supabase)
2. ✅ Créer la base de données
3. ✅ Configurer `.env`
4. ✅ Tester en local
5. ✅ Migrer les données (optionnel)
6. ✅ Déployer le backend
7. ✅ Mettre à jour le frontend
8. 🚀 Profit !

---

## 📞 Support

Besoin d'aide ? Consultez la documentation :
- [Supabase Docs](https://supabase.com/docs)
- [Railway Docs](https://docs.railway.app)
- [Render Docs](https://render.com/docs)

---

**Bonne chance ! 🚀**
