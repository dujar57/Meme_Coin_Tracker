"""
Configuration centralisée : CORS, auth, env.
Source de vérité : .env (jamais commité).
"""
import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# Environnement
ENV = os.getenv("ENV", "development")
IS_PROD = ENV.lower() == "production"


def _parse_csv(env_key: str) -> list[str]:
    raw = os.getenv(env_key, "").strip()
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def _merge_unique(items: list[str], *extra: str) -> list[str]:
    out: list[str] = []
    seen = set()
    for it in items + [e for e in extra if e]:
        k = it.strip().rstrip("/")
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out


# --- CORS : jamais * en prod ; Render injecte RENDER_EXTERNAL_URL ---
_ALLOW_USER = _parse_csv("ALLOWED_ORIGINS")
_RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")

ALLOWED_ORIGINS = _merge_unique(_ALLOW_USER, _RENDER_URL)

_explicit_cors = bool(_ALLOW_USER or _RENDER_URL)
if not ALLOWED_ORIGINS:
    # Développement local uniquement
    ALLOWED_ORIGINS = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]

if IS_PROD and not _explicit_cors:
    import sys

    print(
        "[SECURITE] ENV=production sans ALLOWED_ORIGINS ni RENDER_EXTERNAL_URL : "
        "CORS limité aux origines localhost — le site ne sera pas joignable depuis Internet.",
        file=sys.stderr,
    )

# CORS : liste explicite d’en-têtes et méthodes (pas de "*" en prod)
CORS_ALLOW_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
CORS_ALLOW_HEADERS = [
    "Authorization",
    "Content-Type",
    "X-API-Key",
    "Accept",
    "Accept-Language",
    "Origin",
    "Cache-Control",
    "Pragma",
]

# Auth optionnelle — si API_KEY défini, les requêtes doivent inclure X-API-Key
# (réservé aux clients non-navigateur : mettre la clé dans le front l’exposerait à tout le monde)
API_KEY = os.getenv("API_KEY", "").strip()
REQUIRE_API_KEY = bool(API_KEY) and IS_PROD

# Trusted Host : TRUSTED_HOSTS explicite, ou RENDER_EXTERNAL_HOSTNAME + localhost
_TRUST_USER = _parse_csv("TRUSTED_HOSTS")
_RENDER_HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip()

TRUSTED_HOSTS = _merge_unique(_TRUST_USER, _RENDER_HOST, "localhost", "127.0.0.1")
# Retirer doublons vides
TRUSTED_HOSTS = [h for h in TRUSTED_HOSTS if h]

# En prod on impose le contrôle Host dès qu’on connaît au moins un hôte (Render ou .env)
USE_TRUSTED_HOST = IS_PROD and bool(TRUSTED_HOSTS)
