# 🪙 Meme Coin Tracker - Portfolio Manager

Application web professionnelle pour gérer vos investissements en meme coins sur Solana.

## ✨ Fonctionnalités

- 📊 **Dashboard interactif** avec statistiques en temps réel
- 📈 **Graphiques d'évolution** du portfolio et de répartition
- 🔄 **Mise à jour automatique** des prix via Jupiter API
- 🔗 **Connexion Phantom Wallet** pour intégration blockchain
- ✏️ **CRUD complet** : Ajouter, modifier, supprimer des tokens
- 💾 **Base de données SQLite** pour persistance des données
- 🎨 **Interface moderne** avec TailwindCSS
- 📱 **Responsive design** pour mobile et desktop

## 🚀 Installation

### Prérequis
- Python 3.8+
- Node.js (optionnel)
- Navigateur avec Phantom Wallet installé

### Backend (Python FastAPI)

```powershell
# Aller dans le dossier backend
cd backend

# Installer les dépendances
pip install -r requirements.txt

# Lancer le serveur
python main.py
```

Le serveur API sera accessible sur : `http://localhost:8000`

### Frontend

```powershell
# Aller dans le dossier frontend
cd frontend

# Option 1 : Serveur HTTP simple Python
python -m http.server 3000

# Option 2 : Serveur HTTP Node.js
npx http-server -p 3000
```

L'interface sera accessible sur : `http://localhost:3000`

## 📖 Utilisation

1. **Démarrer le backend** : `python backend/main.py`
2. **Démarrer le frontend** : Ouvrir `frontend/index.html` dans un navigateur
3. **Connecter votre wallet** : Cliquer sur "Connecter Wallet"
4. **Ajouter des tokens** : Bouton "Ajouter un Token"
5. **Actualiser les prix** : Bouton "Actualiser" (auto-refresh toutes les 5 min)

## 🏗️ Architecture

```
meme_coin_tracker/
├── backend/
│   ├── main.py              # API FastAPI
│   └── requirements.txt     # Dépendances Python
├── frontend/
│   ├── index.html           # Interface principale
│   └── app.js              # Logique JavaScript
└── data/
    └── meme_coins.db       # Base de données SQLite
```

### Fichiers Python hors API (à lancer à la main si besoin)

`audit.py`, `debug_tokens.py`, `fix_db.py`, `fix_current_tokens.py`, `fix_wallet_gains.py`, `migrate_sales_table.py`, `migrate_to_postgres.py`, `reset_db.py`, `database.py` (SQLite/Postgres), `alerting.py` : **utilitaires ponctuels**, non importés par `main.py`. Les migrations courantes de schéma sont appliquées au **démarrage** du serveur dans `main.py`.

## 🔌 API Endpoints

- `GET /api/dashboard` - Statistiques du portfolio
- `GET /api/tokens` - Liste des tokens
- `POST /api/tokens` - Ajouter un token
- `PUT /api/tokens/{id}` - Modifier un token
- `DELETE /api/tokens/{id}` - Supprimer un token
- `POST /api/update-prices` - Actualiser tous les prix
- `GET /api/price/{address}` - Prix d'un token
- `GET /api/history/{id}` - Historique des prix

## 🛠️ Technologies

**Backend:**
- Python 3.8+
- FastAPI - API REST rapide
- SQLite - Base de données
- httpx - Client HTTP async

**Frontend:**
- HTML5 / JavaScript ES6
- TailwindCSS - Design moderne
- Chart.js - Graphiques interactifs
- Solana Web3.js - Intégration blockchain
- Font Awesome - Icônes

## 🔐 Sécurité

- Pas de clés privées stockées
- Connexion wallet via Phantom (sécurisée)
- API CORS configurée
- Validation des données côté serveur

## ☁️ Déploiement (Render + Neon)

Voir **`DEPLOY-RENDER-NEON.md`** : blueprint `render.yaml`, variables d’environnement, disque SQLite, front Vercel avec `api-config.js`.

## 📝 Notes

- Les prix sont récupérés via l'API Jupiter (Solana DEX)
- Auto-refresh des prix toutes les 5 minutes
- Historique des prix sauvegardé pour les graphiques
- Compatible avec tous les tokens SPL Solana

## 🆘 Support

En cas de problème :
1. Vérifier que le backend est lancé sur le port 8000
2. Vérifier que Phantom Wallet est installé
3. Consulter les logs dans la console du navigateur
4. Vérifier la connexion internet pour les prix

## 📄 Licence

Projet personnel - Usage libre
