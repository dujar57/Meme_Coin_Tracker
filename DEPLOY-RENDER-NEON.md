# Déploiement Render + Neon

## Ce que fait l’app aujourd’hui

- L’API dans `main.py` utilise **SQLite** (fichier), pas PostgreSQL.
- **Neon** sert surtout aux scripts utilitaires (`migrate_to_postgres.py`, `database.py` en mode `DATABASE_MODE=postgres`) tant que l’API n’est pas migrée vers Postgres.

## Render (API + front servis par FastAPI)

1. Crée un **Web Service** sur [Render](https://render.com), branche ton dépôt.
2. Si le repo contient plusieurs dossiers, mets **Root Directory** sur `fichier/meme_coin_tracker` (ou le chemin réel vers ce projet).
3. Tu peux importer le blueprint : fichier `render.yaml` à la racine du service.
4. Variables d’environnement :
   - `HELIUS_API_KEY` (obligatoire)
   - Optionnel : `ALLOWED_ORIGINS` si le front est sur un autre domaine (ex. Vercel), liste séparée par des virgules
   - Optionnel : `SQLITE_DB_PATH` = chemin absolu du fichier SQLite si tu montes un **disque persistant** Render (sinon la base est perdue au redémarrage sur l’offre gratuite)

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
