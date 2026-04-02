# Déploiement Render + Neon

## Ce que fait l’app aujourd’hui

- L’API dans `main.py` utilise **SQLite** (fichier), pas PostgreSQL.
- **Neon** sert surtout aux scripts utilitaires (`migrate_to_postgres.py`, `database.py` en mode `DATABASE_MODE=postgres`) tant que l’API n’est pas migrée vers Postgres.

## Données 24h/24 sans disque persistant payant sur Render

**Contexte :** sur l’offre **Web Service gratuite** de Render, le disque du conteneur est en général **éphémère** : si tu éteins ton PC, ce n’est pas lui qui héberge la BDD — c’est Render — mais au **redéploiement** ou parfois au redémarrage, un **fichier SQLite** stocké *dans* le conteneur (sans volume persistant) peut **être effacé**. Le **disque persistant** Render est l’option simple pour garder ce fichier, mais il est **payant**.

**Ce que tu veux (H24, 0 € en plus sur Render) :** faire vivre les données **ailleurs**, sur une base **gratuite** qui tourne en continu :

| Fournisseur (exemples) | Coût typique | Rôle |
|------------------------|--------------|------|
| **[Neon](https://neon.tech)** | Gratuit (quota hobby) | PostgreSQL managé, URL `DATABASE_URL` |
| **[Supabase](https://supabase.com)** | Gratuit (quota) | PostgreSQL managé |
| **PlanetScale** / autres | Selon offre | Souvent MySQL, pas adapté tel quel ici |

Tu mets **`DATABASE_URL`** (secret) sur Render : la base est **chez Neon/Supabase**, pas sur le disque Render — donc **pas besoin du disque payant Render** pour que les données survivent quand ton PC est éteint.

**Limite importante aujourd’hui :** cette app **ne pilote pas encore** Postgres dans `main.py` (tout le code API est écrit pour **SQLite**). Le fichier `database.py` est un **ancien brouillon** (peu de tables vs le vrai schéma dans `main.py`). **Brancher Neon “pour de vrai”** = **migrer** l’API vers PostgreSQL (refonte des connexions + requêtes + migrations) — c’est le bon chemin produit, mais ce n’est **pas** un simple réglage d’environnement.

**En résumé :**

- **Oui**, une BDD en ligne **gratuite** H24 sans payer le disque Render, c’est **possible** (Neon / Supabase…).
- **Non**, ce n’est **pas encore branché** dans cette version du code : il faudra une **migration Postgres** (chantier de dev).
- En attendant : soit **disque persistant** Render (payant), soit **accepter** de repartir d’une BDD vide après certains redéploiements (non recommandé en prod).

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
3. Tant que **`main.py`** n’est pas migré vers Postgres, mettre seulement `DATABASE_URL` sur Render **ne change rien** pour l’API : elle continue d’utiliser **SQLite**. Quand la migration sera faite, tu ajouteras par ex. `DATABASE_MODE=postgres` + `DATABASE_URL` (secret) sur Render.

## Front séparé (Vercel, etc.)

1. Déploie le dossier `frontend/` en statique.
2. Dans `frontend/api-config.js`, décommente et renseigne `window.__MEME_API_BASE__` avec l’URL Render **sans** slash final.
3. Ajoute l’URL du front dans `ALLOWED_ORIGINS` côté Render.

## Docker sur Render

Alternative au runtime Python : type de service **Docker**, `Dockerfile` à la racine du projet, même variables d’env.
