# 🚀 Guide de Démarrage Rapide

## Installation et Lancement

### Méthode 1 : Automatique (Recommandé)

1. **Ouvrir un terminal PowerShell** dans le dossier `backend/`
2. **Exécuter :**
   ```powershell
   .\start.bat
   ```
3. **Ouvrir l'interface :** Double-cliquez sur `frontend/ouvrir.bat`

### Méthode 2 : Manuel

**Terminal 1 - Backend :**
```powershell
cd backend
py -m pip install fastapi uvicorn pydantic httpx python-multipart
py main.py
```

**Terminal 2 - Frontend :**
```powershell
cd frontend
start index.html
```

## ✅ Vérification

- Backend : http://localhost:8000
- API Docs : http://localhost:8000/docs
- Frontend : Ouvrir `frontend/index.html` dans votre navigateur

## 🔧 En cas de problème

### Problème : Module non trouvé
```powershell
py -m pip install --upgrade pip
py -m pip install fastapi uvicorn pydantic httpx python-multipart
```

### Problème : Port déjà utilisé
Modifier le port dans `backend/main.py` ligne finale :
```python
uvicorn.run(app, host="0.0.0.0", port=8001)  # Changer 8000 en 8001
```

### Problème : CORS Error
Vérifier que le backend tourne sur le port 8000, sinon modifier `API_URL` dans `frontend/app.js` :
```javascript
const API_URL = 'http://localhost:8001/api';  // Adapter le port
```

## 🌐 Connexion Wallet

1. Installer l'extension [Phantom Wallet](https://phantom.app/)
2. Cliquer sur "Connecter Wallet" dans l'application
3. Approuver la connexion dans Phantom

## 🔄 Actualisation des prix

- **Manuel :** Bouton "Actualiser" dans l'interface
- **Automatique :** Toutes les 5 minutes

## 📊 Utilisation

1. **Ajouter un token :** Bouton vert "Ajouter un Token"
2. **Modifier :** Icône crayon sur chaque ligne
3. **Supprimer :** Icône corbeille sur chaque ligne
4. **Voir graphique :** Icône graphique sur chaque ligne

## 🎨 Captures d'écran

L'interface comprend :
- Dashboard avec 6 statistiques principales
- 2 graphiques (évolution + répartition)
- Tableau détaillé de tous vos tokens
- Formulaire d'ajout/modification

## 💡 Astuces

- Les prix sont mis à jour en temps réel via Jupiter API (Solana)
- L'historique des prix est conservé pour les graphiques
- Vous pouvez gérer plusieurs ventes par token
- Les calculs de gains/pertes sont automatiques

## 📞 Support

Consultez le [README.md](../README.md) complet pour plus de détails.
