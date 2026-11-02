
Pour un assistant de recherche comme **Syna**, la gestion de la recherche est le cœur du système. Elle doit être **hybride** : elle doit à la fois fouiller dans votre mémoire locale (**Obsidian**) et aller chercher de l'information fraîche sur le web (**externe**).

Voici comment concevoir, structurer et implémenter ce module de recherche de manière efficace.

##  Architecture du Moteur de Recherche de Syna

Plaintext

```
                        ┌────────────────────────┐
                        │ Commande Telegram      │
                        │ /search [requête]      │
                        └───────────┬────────────┘
                                    │
                        ┌───────────▼────────────┐
                        │ Routeur de Requête     │
                        └─────┬────────────┬─────┘
             (Interne)        │            │        (Externe)
             ┌────────────────┘            └────────────────┐
             ▼                                              ▼
┌─────────────────────────┐                    ┌─────────────────────────┐
│ Recherche Interne       │                    │ Recherche Externe       │
│ • Sémantique (Embeddings│                    │ • API Web (Tavily/Exa)  │
│ • Lexicale (Grep/Regex) │                    │ • Scraping & Synthèse   │
└────────────┬────────────┘                    └────────────┬────────────┘
             │                                              │
             └──────────────────────┬───────────────────────┘
                                    │
                        ┌───────────▼────────────┐
                        │ Agrégation & LLM       │
                        │ (Formattage de la 답)  │
                        └───────────┬────────────┘
                                    │
                        ┌───────────▼────────────┐
                        │ Réponse sur Telegram   │
                        │ + Bouton [mknot]    │
                        └────────────────────────┘
```

### 1. La Recherche Interne (Dans Obsidian)

Obsidian stocke vos notes sous forme de fichiers Markdown plats `.md`. Pour y chercher de l'information de façon intelligente, vous avez deux approches à combiner :

- **Approche Lexicale (Rapide & Légère) :**
    
    - Parcourir les fichiers du dossier via un script (en Python avec `os.walk` ou `pathlib`).
        
    - Utiliser une recherche par mots-clés (regex) pour trouver les notes contenant les termes exacts ou les titres correspondants. Idéal pour retrouver un nom de projet ou un tag (`#python`, `#ai`).
        
- **Approche Sémantique (Intelligente / RAG local) :**
    
    - Idéal pour des questions complexes du type : _"Qu'est-ce que j'ai noté l'mois dernier sur l'architecture microservices ?"_
        
    - **Mise en œuvre :**
        
        1. Découper vos notes en morceaux (chunks).
            
        2. Générer des _embeddings_ (vecteurs de mots) à l'aide d'un modèle léger et gratuit (ex: `sentence-transformers` en local).
            
        3. Les stocker dans une base vectorielle légère comme **ChromaDB** ou **FAISS**.
            
        4. Lorsqu'une recherche est lancée via Telegram, le bot convertit votre question en vecteur et interroge la base locale pour retrouver les extraits de notes les plus pertinents.
            

### 2. La Recherche Externe (En Ligne)

Quand la réponse n'est pas dans votre vault, Syna doit chercher sur le web.

- **Les APIs de Recherche pour IA :**
    
    - Évitez le scraping Google brut (souvent bloqué par des CAPTCHA). Privilégiez des moteurs conçus pour les agents LLM :
        
        - **Tavily AI API :** Très populaire pour les agents, renvoie directement du contenu propre, prêt pour un LLM.
            
        - **Exa API (anciennement Metaphor) :** Excellente pour la recherche sémantique sur le web de haute qualité.
            
        - **Google Custom Search JSON API :** Alternative classique.
            
- **Le Pipeline :**
    
    1. Le bot extrait les mots-clés de la commande `/search`.
        
    2. Il interroge l'API web.
        
    3. Il récupère les 3 ou 4 meilleurs articles/liens.
        
    4. Un LLM (ex: un modèle rapide via une API) synthétise ces sources en une réponse claire et structurée.
        

### 3. Expérience Utilisateur (UX) sur Telegram

Pour que l'utilisation via Telegram soit fluide, la commande `/search` doit être interactive :

- **Le format de sortie :**
    
    - Diviser la réponse en deux blocs :
        
        1. **Sources Internes (Obsidian)** : Titres des notes trouvées avec des liens/extraits.
            
        2. **Synthèse Externe (Web)** : Résumé de la recherche en ligne.
            
- **Les boutons interactifs (Inline Keyboards) :**
    
    - Sous la réponse du bot, ajouter des boutons Telegram :
        
        - `[Enregistrer la réponse comme note]` (déclenche un `mknot` automatique).
            
        - `[Voir les notes liées (/synote)]`.
            

##  Points Critiques de la Gestion de la Recherche

1. **La Fraîcheur et la Synchronisation de l'Index Local :**
    
    - _Le problème :_ Si vous créez une note directement dans Obsidian sur votre PC, le moteur de recherche sémantique du bot (si basé sur une base vectorielle) ne la connaîtra pas tant qu'il n'aura pas ré-indexé le dossier.
        
    - _La solution :_ Mettre en place un écouteur de fichiers (`watchdog` en Python) sur le dossier Obsidian. Dès qu'un fichier `.md` est modifié, créé ou supprimé, le script met à jour l'index en arrière-plan de manière incrémentale.
        
2. **La Maîtrise des Coûts et de la Latence des LLM :**
    
    - Interroger une API de recherche + envoyer le tout à un gros modèle (comme GPT-4 ou Claude) à chaque requête peut coûter cher et prendre 10 secondes.
        
    - _La solution :_ Utiliser un modèle rapide et économique (ex: `gpt-4o-mini`, `Claude 3 Haiku`, ou un petit modèle open source en local comme `Mistral-7B` ou `Llama-3`) pour la synthèse des recherches.
        
3. **La Pertinence du Contexte (Prompt Stuffing) :**
    
    - Si la recherche interne renvoie 10 notes entières, vous risquez de saturer la fenêtre de contexte du modèle ou d'inclure du bruit.
        
    - _La solution :_ N'envoyer au modèle que les extraits (chunks) ayant le score de similarité le plus élevé avec la requête, accompagnés du titre de la note source.