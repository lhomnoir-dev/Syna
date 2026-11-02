## Architecture et Guide de Développement

### 1. La Stack Technique Recommandée

- **Langage :** **Python** (via `python-telegram-bot` ou `aiogram` pour l'asynchronisme).
    
- **Stockage / Vault :** Un dossier local de fichiers Markdown (votre coffre Obsidian).
    
- **Moteur d'IA / Recherche :**
    
    - _Interne :_ Recherche textuelle (regex/grep) ou recherche sémantique locale (embeddings avec `ChromaDB` ou `FAISS` indexant vos notes).
        
    - _Externe :_ API de recherche (Exa, Tavily, ou Google Custom Search) pour le web.
        
- **Synchronisation :** Git (via un script de commit/push automatique) ou Syncthing si le bot tourne sur un serveur distant (VPS).
    

### 2. Flux d'Exécution des Commandes Clés

- **`/mknot` & `mkfol` :** Le bot reçoit le message Telegram $\rightarrow$ Nettoie le titre pour en faire un nom de fichier valide $\rightarrow$ Crée le fichier `.md` dans le bon sous-dossier du vault Obsidian.
    
- **`/search` :**
    
    - _Interne :_ Parcourt les fichiers `.md` à la recherche de mots-clés ou interroge la base vectorielle.
        
    - _Externe :_ Interroge une API web, résume les résultats via un LLM (ex: OpenAI / Anthropic / modèle local) et renvoie la réponse sur Telegram avec une option pour l'enregistrer dans Obsidian (`/mknot`).
        
- **`/synote` (La connexion automatique) :** Analyse le contenu de la nouvelle note ou d'une note existante, extrait les concepts clés, compare avec les titres et contenus des autres notes du vault, et injecte automatiquement des liens wikilinks (`[[Nom de note]]`) pertinents.
    

## Points Critiques à Maîtriser

### 1. Sécurité et Authentification du Bot Telegram

- **Le problème :** Par défaut, un bot Telegram est public. N'importe qui sur Telegram pourrait envoyer des commandes pour supprimer des notes (`rmnote`, `rmdir`) ou polluer votre vault.
    
- **La solution :** Implémenter une **whitelist stricte d'ID Telegram**. Le bot doit vérifier l'ID de l'expéditeur (`update.effective_user.id`) à chaque commande et rejeter tout utilisateur non autorisé.
    

### 2.Concurrence et Conflits de Synchronisation (Git / Fichiers)

- **Le problème :** Si vous modifiez une note directement dans l'application Obsidian sur votre ordinateur en même temps que le bot Telegram en crée ou modifie une autre sur le serveur/stockage distant, vous risquez des **conflits de fusion (Merge Conflicts)** si vous utilisez Git, ou des corruptions de fichiers.
    
- **La solution :** Mettre en place un mécanisme de verrouillage simple (locks) ou automatiser un cycle propre : `git pull` avant chaque écriture par le bot, suivi d'un `git commit -m "Syna bot update"` et `git push`.
    

### 3.Gestion des Chemins et Sécurité du Système de Fichiers (Path Traversal)

- **Le problème :** Si un utilisateur malveillant (ou une injection de texte) arrive à manipuler la commande `mkdir` ou `rmdir` pour envoyer un chemin du type `../../etc/` ou des caractères spéciaux, le script pourrait supprimer des fichiers sensibles du serveur.
    
- **La solution :** Valider et sanctuariser tous les chemins d'accès. Le bot doit obligatoirement cantonner toutes les opérations de fichiers à l'intérieur de la racine absolue du dossier Obsidian (`os.path.commonpath`), en rejetant tout chemin contenant `..`.
    

### 4.Limitation des Tokens et Longueur des Messages Telegram

- **Le problème :** Telegram limite la taille des messages à **4096 caractères**. Si votre fonction `/search` ou `getnot` renvoie une note très longue ou un résumé web massif, l'API Telegram plantera.
    
- **La solution :** Prévoir un système de découpage automatique des messages (_chunking_) ou formater la réponse sous forme de fichier `.md` envoyé en pièce jointe si le texte dépasse une certaine taille.
    

### 5.Pertinence et "Hallucination" de la Synchronisation (`synote`)

- **Le problème :** Un LLM peut lier des notes qui n'ont aucun rapport réel juste parce qu'elles partagent un mot commun, polluant votre graphe Obsidian avec des liens non pertinents (bruit sémantique).
    
- **La solution :** Définir un seuil de similarité strict (score de similarité cosinus élevé) avant d'insérer automatiquement un lien `[[...]]`, ou demander une validation (bouton interactif Telegram : _"Voulez-vous lier cette note à X ?"_) avant d'appliquer les modifications.