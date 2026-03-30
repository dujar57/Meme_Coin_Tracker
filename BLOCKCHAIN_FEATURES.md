# 🔗 Fonctionnalités Blockchain Solana

## 🎯 Vue d'ensemble

Votre application Meme Coin Tracker est maintenant **connectée à la blockchain Solana** ! Elle peut :

✅ **Récupérer les prix en temps réel** via Jupiter API  
✅ **Scanner automatiquement vos transactions** de vente  
✅ **Calculer vos profits/pertes réels** en comparant prix d'achat vs prix de vente  
✅ **Synchroniser votre wallet Phantom** avec votre portfolio  

---

## 🚀 Comment ça fonctionne

### 1️⃣ Connexion du Wallet

1. Installez [Phantom Wallet](https://phantom.app/) si ce n'est pas déjà fait
2. Dans l'interface, cliquez sur **"Connecter Wallet"**
3. Approuvez la connexion dans Phantom
4. Votre adresse apparaîtra en haut de la page

### 2️⃣ Ajout de vos Tokens

1. Cliquez sur **"Ajouter un Token"**
2. Renseignez :
   - **Nom du token** (ex: BONK, WIF, etc.)
   - **Adresse Solana du token** (Contract Address)
   - **Date d'achat** et **nombre de tokens achetés**
   - **Prix d'achat en USD**

### 3️⃣ Synchronisation Automatique

**C'EST LA MAGIE ! 🪄**

Cliquez sur **"Synchroniser avec Blockchain"** :

L'application va :
- 📡 Scanner vos dernières 100 transactions sur Solana
- 🔍 Détecter automatiquement toutes vos ventes de tokens
- 💰 Calculer le montant exact que vous avez retiré
- 📊 Mettre à jour vos profits/pertes en temps réel
- ✅ Enregistrer automatiquement les ventes dans votre historique

**Vous n'avez plus besoin d'enregistrer manuellement vos ventes !**

### 4️⃣ Actualisation des Prix

Le bouton **"Actualiser"** :
- Récupère les prix actuels depuis Jupiter API (le plus gros DEX agrégateur Solana)
- Met à jour la valeur de votre portfolio
- Calcule vos gains/pertes en temps réel

💡 **L'actualisation se fait automatiquement toutes les 5 minutes**

---

## 📊 Calcul des Profits/Pertes

### Formule Utilisée :

```
Bilan = (Valeur Actuelle + Argent Retiré) - Investissement Initial
```

**Exemple :**
- Vous achetez 1M de tokens à 0.00001$ = **10$ investis**
- Vous vendez 500K tokens à 0.00003$ = **15$ retirés**
- Il vous reste 500K tokens qui valent actuellement 0.00002$ = **10$ de valeur actuelle**

**Bilan : (10$ + 15$) - 10$ = +15$ de profit ! 🎉**

---

## 🔐 Sécurité

- ✅ Votre wallet **ne signe aucune transaction**
- ✅ L'application **lit seulement** vos transactions publiques
- ✅ Vos clés privées **ne sont jamais demandées**
- ✅ Connexion 100% sécurisée via Phantom

---

## 🛠️ APIs Utilisées

1. **Jupiter Price API** - Prix en temps réel des tokens Solana
2. **Solana RPC API** - Lecture des transactions blockchain
3. **Phantom Wallet** - Connexion sécurisée à Solana

---

## 💡 Conseils d'utilisation

### Pour un suivi optimal :

1. **Connectez votre wallet** dès le début
2. **Ajoutez vos tokens** avec les bonnes adresses de contrat
3. **Synchronisez régulièrement** (ex: une fois par jour) pour détecter les nouvelles ventes
4. **Consultez l'historique** de chaque token pour voir le détail de vos transactions

### Où trouver l'adresse d'un token ?

- Sur [Birdeye](https://birdeye.so/)
- Sur [DexScreener](https://dexscreener.com/)
- Dans votre wallet Phantom (cliquez sur le token)

---

## 🐛 Résolution de problèmes

### La synchronisation ne détecte pas mes ventes

**Causes possibles :**
- Le token n'est pas encore ajouté dans votre portfolio
- L'adresse du token dans votre base ne correspond pas à celle sur Solana
- La vente date de plus de 100 transactions (limite actuelle)

**Solution :** Enregistrez manuellement la vente dans l'historique du token

### Les prix ne s'actualisent pas

**Causes possibles :**
- Le token est trop nouveau ou n'est pas listé sur Jupiter
- Problème de connexion internet
- API temporairement indisponible

**Solution :** Attendez quelques minutes et réessayez

### Erreur "Wallet non détecté"

**Solution :**
1. Installez [Phantom Wallet](https://phantom.app/)
2. Rechargez la page
3. Essayez avec un autre navigateur (Chrome recommandé)

---

## 📈 Prochaines fonctionnalités

- [ ] Notifications en temps réel lors de ventes détectées
- [ ] Export des transactions pour déclaration fiscale
- [ ] Support de multiple wallets
- [ ] Alertes de prix personnalisées
- [ ] Graphiques d'évolution de chaque token

---

## 🎓 Pour aller plus loin

Votre application utilise maintenant la puissance de la blockchain Solana pour vous offrir un suivi **automatique et précis** de vos investissements en meme coins !

**Plus besoin de noter manuellement vos trades** - tout est synchronisé automatiquement ! 🚀
