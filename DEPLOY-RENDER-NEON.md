# Déploiement Render + Neon

## Ce que fait l’app aujourd’hui

- L’API dans `main.py` utilise **SQLite** (fichier), pas PostgreSQL.
- **Neon** sert surtout aux scripts utilitaires (`migrate_to_postgres.py`, `database.py` en mode `DATABASE_MODE=postgres`) tant que l’API n’est pas migrée vers Postgres.

## Render (API + front servis par FastAPI)

1. Crée un **Web Service** sur [Render](https://render.com), branche ton dépôt.
2. Si le repo contient plusieurs dossiers, mets **Root Directory** sur `fichier/meme_coin_tracker` (ou le chemin réel vers ce projet).
3. Tu peux importer le blueprint : fichier `render.yaml` à la racine du service.
4. **Secrets et config perso : uniquement dans Render** (pas dans Git, pas dans `render.yaml` en clair). Dans le service → **Environment** → **Add Environment Variable** → coche **Secret** pour chaque clé / mot de passe / URL sensible. Le `render.yaml` ne contient que `sync: false` pour ces noms : les valeurs vivent sur le tableau de bord et ne sont pas resynchronisées depuis le dépôt.

### Variables à renseigner sur Render (toutes optionnelles sauf mention)

| Variable | Secret ? | Rôle |
|----------|----------|------|
| `HELIUS_API_KEY` | Oui | Obligatoire pour swaps, soldes, RPC Helius. |
| `BIRDEYE_API_KEY` | Oui | Optionnel (prix / fallback). |
| `API_KEY` | Oui | Optionnel : si défini **et** `ENV=production`, toutes les routes `/api/*` exigent l’en-tête `X-API-Key` **sauf** `/api/health` (health check Render). **Ne pas** mettre cette clé dans le JS du navigateur : l’app SPA ne l’envoie pas ; réserve-la à des scripts ou à l’admin. |
| `SQLITE_DB_PATH` | Selon cas | Chemin absolu SQLite si disque persistant (ex. `/mnt/data/meme_coins.db`). |
| `DATABASE_URL` / `DATABASE_MODE` / `POSTGRES_*` | Oui si mots de passe | Uniquement si tu utilises Postgres (hors `main.py` SQLite par défaut). |
| `ALLOWED_ORIGINS` | Peut contenir des URLs perso | Optionnel : front sur un autre domaine (CSV), en plus de l’URL Render. |
| `TRUSTED_HOSTS` | Idem | Optionnel sur Render (hôte Render injecté automatiquement). |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` / `DISCORD_WEBHOOK_URL` | Oui | Optionnel (alertes). |

Déjà fixées par le blueprint (non secrètes) : `ENV=production`, `PYTHON_VERSION`. **Injectées par Render sans action** : `RENDER_EXTERNAL_URL`, `RENDER_EXTERNAL_HOSTNAME`, `PORT`.

Avec **`ENV=production`** : en-têtes (CSP, HSTS, etc.), `/docs`, `/redoc` et `/openapi.json` sont **désactivés** ; CORS et Trusted Host sont stricts.

### Sécurité (rappel)

- Le **JavaScript / HTML** du navigateur reste lisible dans les outils développeur : seules les **clés serveur** sur Render (secrets) et la **base** côté disque sont protégées. Ne mets **jamais** Helius, Birdeye ou `API_KEY` dans `api-config.js`, `app.js` ou tout fichier statique.
- En local, copie `backend/.env.example` vers `backend/.env` (fichier ignoré par Git).

## Neon (PostgreSQL)

1. Crée un projet sur [Neon](https://neon.tech), récupère la **connection string** (`DATABASE_URL`).
2. Pour des essais de migration de données : en local, mets `DATABASE_URL` dans `.env` et lance `migrate_to_postgres.py` (schéma côté Postgres doit exister — voir `database.py` / scripts).
3. Ne mets **pas** `DATABASE_URL` sur le service Render tant que l’API n’utilise pas Postgres : l’appli continue de lire SQLite uniquement.

## Front séparé (Vercel, etc.)

1. Déploie le dossier `frontend/` en statique.
2. Dans `frontend/api-config.js`, décommente et renseigne `window.__MEME_API_BASE__` avec l’URL Render **sans** slash final.
3. Ajoute l’URL du front dans `ALLOWED_ORIGINS` côté Render.

## Docker sur Render

Alternative au runtime Python : type de service **Docker**, `Dockerfile` à la racine du projet, même variables d’env.
