# 📊 Fonctionnement du Système de Détection des Transactions

## 🔍 Comment ça marche actuellement

### 1. **Import des Tokens** (Bouton "Importer mes Tokens")
```
Scan du wallet → Détecte tous les tokens SPL avec balance > 0
↓
Pour chaque token trouvé:
  - Récupère le prix actuel (DexScreener → Jupiter → Birdeye)
  - Enregistre: nom, adresse, quantité, prix actuel
  - ⚠️ NE connaît PAS le prix d'achat réel
```

**Problème**: Le système utilise le **prix actuel** comme prix d'achat par défaut, ce qui fausse les calculs.

### 2. **Détection des Ventes** (Bouton "Import + Historique")
```
Scan des 100-5000 dernières transactions du wallet
↓
Pour chaque transaction:
  - Compare balance token AVANT vs APRÈS
  - Si balance diminue + SOL augmente = VENTE détectée
  - Enregistre: tokens vendus, date, SOL reçu
```

**Problème**: 
- Le prix de vente enregistré = **prix actuel du token** (pas le prix réel au moment de la vente)
- Ne détecte que les échanges directs token → SOL
- Rate les swaps token → autre token → SOL

---

## ❌ Les Limites Actuelles

### 1. **Prix d'achat inconnu**
```
❌ Ce qui se passe:
  - Vous achetez PEPE à 0.00001$ 
  - Import du wallet quand PEPE = 0.00005$
  - Le système pense que vous avez acheté à 0.00005$
  
✅ Résultat attendu vs Réalité:
  - Réel: +400% de gains
  - Affiché: ±0% (prix actuel = prix d'achat)
```

### 2. **Prix de vente estimé**
```
❌ Ce qui se passe:
  - Vous vendez 1000 PEPE le 1er janvier à 0.00003$ (= 0.03$)
  - Le système analyse la transaction le 15 janvier
  - PEPE vaut maintenant 0.00007$
  - Le système enregistre: 1000 tokens vendus à 0.00007$ (= 0.07$)
  
✅ Résultat attendu vs Réalité:
  - Réel: Vendu pour 0.03$ (0.01 SOL reçu)
  - Affiché: Vendu pour 0.07$ (mais le SOL reçu est correct: 0.01 SOL)
```

### 3. **Transactions complexes non détectées**
```
❌ Scénarios ratés:
  - PEPE → BONK → SOL (détecte seulement la baisse de PEPE)
  - Vente sur un DEX non standard
  - Transferts entre vos wallets
  - LP tokens (liquidité)
  - Staking
```

---

## 📈 Données Précises vs Approximatives

| Donnée | Précision | Source |
|--------|-----------|--------|
| **Quantité tokens actuelle** | ✅ 100% précis | Blockchain Solana |
| **Prix actuel** | ✅ Quasi-précis | DexScreener/Jupiter en temps réel |
| **Valeur actuelle** | ✅ Précis | Quantité × Prix actuel |
| **Prix d'achat** | ⚠️ **ESTIMÉ** | Prix actuel au moment de l'import |
| **Prix de vente** | ⚠️ **ESTIMÉ** | Prix actuel au moment du scan |
| **SOL reçu lors vente** | ✅ Précis | Blockchain Solana |
| **Tokens vendus (quantité)** | ✅ Précis | Blockchain Solana |
| **Gains/Pertes** | ⚠️ **APPROXIMATIF** | Basé sur prix estimés |

---

## 💡 Solutions pour Améliorer la Précision

### ✅ **Solution 1: Saisie manuelle** (Actuellement fonctionnel)
Quand vous ajoutez un token manuellement:
```
✅ Vous indiquez:
  - Prix d'achat réel
  - Quantité achetée
  - Date d'achat
  
✅ Résultat: Calculs 100% précis
```

### ⚠️ **Solution 2: Scan historique profond** (Complexe à implémenter)
```
Pour chaque token détecté:
  1. Remonter dans TOUTES les transactions du wallet
  2. Trouver la PREMIÈRE fois où le token apparaît (= achat initial)
  3. Calculer le prix d'achat réel: SOL dépensé ÷ tokens reçus
  
Problème:
  - Peut nécessiter de scanner des milliers de transactions
  - API Solana limite les requêtes
  - Très long (plusieurs minutes)
```

### 🔥 **Solution 3: Utiliser les archives de prix** (Meilleure option)
```
Pour les ventes passées:
  1. Détecter une vente le 1er janvier
  2. Interroger une API d'historique de prix (Birdeye Historical)
  3. Récupérer le prix RÉEL du token au 1er janvier
  
Avantage: Prix historiques précis
Problème: APIs payantes ou limitées
```

---

## 🎯 Recommandations d'Usage

### Pour des données précises:
1. **Méthode manuelle** ✅
   - Ajoutez vos tokens manuellement
   - Notez vos prix d'achat réels
   - Enregistrez vos ventes au moment où vous les faites

2. **Import immédiat** ⚠️
   - Importez votre wallet JUSTE APRÈS un achat
   - Le prix actuel sera proche du prix d'achat réel

3. **Suivi régulier** 📊
   - Actualisez les prix tous les jours
   - Export Excel pour archive externe

### Pour un aperçu rapide:
- L'import automatique reste utile pour:
  - Voir vos holdings actuels
  - Valeur de portefeuille en temps réel
  - Tracking général de performance

---

## 🛠️ Améliorations Possibles (À développer)

### Court terme:
1. **Calculer prix d'achat rétroactif** lors de l'import
   - Scanner les transactions d'achat
   - Formule: SOL dépensé ÷ tokens reçus

2. **Prix de vente historique**
   - Intégrer Birdeye Historical API
   - Récupérer le prix exact au moment de la vente

3. **Détection des swaps complexes**
   - Analyser les instructions de transaction Jupiter
   - Détecter PEPE → BONK → SOL

### Long terme:
4. **Sync bi-directionnel**
   - Webhook pour mises à jour en temps réel
   - Auto-import des nouveaux tokens
   - Notification de vente

5. **Machine Learning**
   - Classifier automatiquement: achat vs vente vs transfert
   - Prédire les patterns de trading

---

## 📝 Conclusion

**Le système actuel est précis pour:**
- ✅ Holdings actuels (quantités)
- ✅ Valeurs actuelles en temps réel
- ✅ Quantités vendues

**Il est approximatif pour:**
- ⚠️ Prix d'achat (utilise prix actuel)
- ⚠️ Prix de vente (utilise prix actuel)
- ⚠️ Calculs de profits/pertes

**Pour une précision maximale:**
- Privilégiez la saisie manuelle
- Ou attendez les améliorations futures du scan rétroactif

---

**Questions fréquentes:**

**Q: Pourquoi mes gains sont faux?**
A: Le prix d'achat est estimé au prix actuel, pas au prix réel d'achat.

**Q: La vente affiche le mauvais montant?**
A: Le montant en $ est estimé, mais le SOL reçu est exact.

**Q: Puis-je corriger manuellement?**
A: Oui! Éditez le token et saisissez le vrai prix d'achat.
