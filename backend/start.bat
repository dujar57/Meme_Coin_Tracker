@echo off
echo ========================================
echo   Meme Coin Tracker - Installation
echo ========================================
echo.

echo [1/3] Installation des dependances Python...
py -m pip install --quiet fastapi uvicorn pydantic httpx python-multipart 2>nul
if %errorlevel% neq 0 (
    echo ERREUR: Impossible d'installer les dependances.
    echo Verifiez votre connexion internet et Python.
    pause
    exit /b 1
)
echo ✓ Dependances installees

echo.
echo [2/2] Lancement du serveur...
echo.
echo ========================================
echo   Serveur demarre sur http://localhost:8000
echo   API Documentation: http://localhost:8000/docs
echo ========================================
echo.
echo Appuyez sur Ctrl+C pour arreter le serveur
echo.

py main.py
