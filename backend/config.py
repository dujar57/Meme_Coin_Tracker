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

# CORS — en prod : origines explicites ; en dev : localhost autorisé
ALLOWED_ORIGINS_RAW = os.getenv("ALLOWED_ORIGINS", "")
if ALLOWED_ORIGINS_RAW:
    ALLOWED_ORIGINS = [o.strip() for o in ALLOWED_ORIGINS_RAW.split(",") if o.strip()]
else:
    # Dev par défaut : localhost + React dev server
    ALLOWED_ORIGINS = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
    ]

# Auth optionnelle — si API_KEY défini, les requêtes doivent inclure X-API-Key
API_KEY = os.getenv("API_KEY", "").strip()
REQUIRE_API_KEY = bool(API_KEY) and IS_PROD
