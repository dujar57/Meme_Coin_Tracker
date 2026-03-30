# 🚀 Démarrage Rapide - Base de Données

## ✅ État Actuel
- ✅ Migration SQLite réussie (nouvelles colonnes ajoutées)
- ✅ Infrastructure PostgreSQL préparée
- ✅ Scripts de migration créés
- ✅ Mode hybride configuré (SQLite OU PostgreSQL)

---

## 🎯 Mode Actuel : SQLite (Local)

Votre application fonctionne actuellement en **mode local** avec SQLite.

### Démarrer en mode SQLite (actuel)
```bash
cd backend
py main.py
```

C'est tout ! Aucune configuration supplémentaire nécessaire.

---

## 🌐 Passer en Mode PostgreSQL (En Ligne)

Si vous voulez une base de données en ligne accessible de partout :

### Étape 1 : Choisir un service
Recommandé : **Supabase** (gratuit jusqu'à 500 MB)
- Inscription : https://supabase.com
- Cliquez "New project"
- Notez votre **DATABASE_URL**

### Étape 2 : Installer les dépendances PostgreSQL
```bash
cd backend
pip install psycopg2-binary python-dotenv
```

### Étape 3 : Configurer .env
Éditez `backend/.env` :
```env
DATABASE_MODE=postgres
DATABASE_URL=postgresql://postgres:[password]@[host]:5432/postgres?sslmode=require
```

### Étape 4 : Migrer vos données (optionnel)
Si vous avez déjà des données dans SQLite :
```bash
cd backend
python migrate_to_postgres.py
```

### Étape 5 : Démarrer en mode PostgreSQL
```bash
cd backend
python main.py
```

Vous verrez :
```
✅ Base de données initialisée (Mode: postgres)
```

---

## 📊 Vérifier que tout fonctionne

### Test de connexion
```bash
cd backend
python database.py
```

Résultat attendu :
```
🔧 Mode base de données: sqlite
🔍 Test de connexion à la base de données...
✅ SQLite connecté: 3.x.x
```

Ou si en mode PostgreSQL :
```
🔧 Mode base de données: postgres
🔍 Test de connexion à la base de données...
✅ PostgreSQL connecté: PostgreSQL 15.x
```

---

## 🔄 Changer de Mode

### Passer de SQLite → PostgreSQL
1. Éditez `.env` : `DATABASE_MODE=postgres`
2. Ajoutez `DATABASE_URL`
3. Relancez : `python main.py`

### Revenir à SQLite
1. Éditez `.env` : `DATABASE_MODE=sqlite`
2. Relancez : `python main.py`

---

## 📁 Fichiers Créés

```
backend/
├── .env                      ← Configuration active
├── .env.example             ← Template de configuration
├── database.py              ← Gestionnaire DB unifié
├── migrate_sales_table.py   ← Migration SQLite (fait ✅)
├── migrate_to_postgres.py   ← Migration SQLite → PostgreSQL
└── requirements.txt         ← Dépendances (mises à jour)

documentation/
└── GUIDE_DB_ONLINE.md       ← Guide complet PostgreSQL
```

---

## 💡 Conseil

**Pour débuter** : Restez en mode SQLite  
**Pour partager/déployer** : Passez à PostgreSQL

---

## 🆘 Besoin d'aide ?

Consultez le guide complet : [GUIDE_DB_ONLINE.md](../GUIDE_DB_ONLINE.md)

---

**Bonne utilisation ! 🎉**
