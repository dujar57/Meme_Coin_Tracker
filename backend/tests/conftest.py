"""
Forcer ENV=development pendant pytest (ce fichier n’est chargé que par pytest),
pour éviter Trusted Host / CORS prod si le .env contient ENV=production.
"""
import os

os.environ["ENV"] = "development"
