@echo off
chcp 65001 >nul
color 0A

echo.
echo ╔════════════════════════════════════════════════════════════╗
echo ║                                                            ║
echo ║        🪙  MEME COIN TRACKER - Installation  🪙           ║
echo ║                                                            ║
echo ║              Portfolio Manager Professionnel               ║
echo ║                                                            ║
echo ╚════════════════════════════════════════════════════════════╝
echo.
echo.

:: Vérifier Python
echo [Étape 1/5] Vérification de Python...
py --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Python n'est pas installé ou pas dans le PATH
    echo.
    echo Téléchargez Python depuis : https://www.python.org/downloads/
    echo Assurez-vous de cocher "Add Python to PATH" lors de l'installation
    pause
    exit /b 1
)
py --version
echo ✅ Python détecté
echo.

:: Installer les dépendances
echo [Étape 2/5] Installation des dépendances Python...
echo Cela peut prendre quelques minutes...
echo.
cd backend
py -m pip install --quiet --upgrade pip
py -m pip install fastapi uvicorn pydantic httpx python-multipart
if %errorlevel% neq 0 (
    echo ❌ Erreur lors de l'installation des dépendances
    echo Vérifiez votre connexion internet
    pause
    exit /b 1
)
echo ✅ Dépendances installées
echo.

:: Créer la base de données
echo [Étape 3/5] Initialisation de la base de données...
if not exist "..\data" mkdir "..\data"
echo ✅ Dossier data créé
echo.



:: Vérifier Phantom Wallet
echo [Étape 5/5] Vérification de Phantom Wallet...
echo.
echo ℹ️  Pour connecter votre wallet Solana :
echo    1. Installez l'extension Phantom Wallet
echo    2. https://phantom.app/
echo    3. Configurez votre wallet
echo.

cd ..

echo.
echo ╔════════════════════════════════════════════════════════════╗
echo ║                                                            ║
echo ║             ✅  Installation terminée !  ✅               ║
echo ║                                                            ║
echo ╚════════════════════════════════════════════════════════════╝
echo.
echo.
echo 🚀 Pour démarrer l'application :
echo.
echo    1. Ouvrez un terminal dans le dossier 'backend'
echo    2. Exécutez : start.bat
echo    3. Ouvrez : frontend\ouvrir.bat
echo.
echo 📚 Documentation complète : README.md
echo 🎯 Guide rapide : QUICKSTART.md
echo.
echo Appuyez sur une touche pour ouvrir le guide de démarrage...
pause >nul

start "" "QUICKSTART.md"

echo.
echo Voulez-vous démarrer l'application maintenant ? (O/N)
set /p choix="> "

if /i "%choix%"=="O" (
    echo.
    echo Démarrage du serveur...
    start "Meme Coin Tracker - Backend" cmd /k "cd backend && start.bat"
    timeout /t 3 /nobreak >nul
    echo Ouverture de l'interface...
    start "" "frontend\start.html"
    echo.
    echo ✅ Application lancée !
    echo.
) else (
    echo.
    echo Pour démarrer plus tard, exécutez : backend\start.bat
    echo.
)

pause
