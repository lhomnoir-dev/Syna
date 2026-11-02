# Syna

**Syna** est un assistant de recherche personnel sur Telegram, connecté à votre
coffre **Obsidian**. Il crée et gère vos notes, cherche à la fois dans votre
vault et sur le web (avec une synthèse par Claude), et relie automatiquement
vos notes entre elles par des wikilinks `[[...]]`.

Les documents de conception d'origine se trouvent dans [`docs/`](docs/).

## Fonctionnalités

| Commande | Rôle |
|---|---|
| `/mknot Dossier/Titre` + contenu | Crée une note (contenu sur les lignes suivantes, `Titre \| contenu`, ou en réponse à un message) |
| `/getnot note` | Affiche une note (envoyée en fichier `.md` si elle est longue) |
| `/update note` + contenu | Remplace le contenu (frontmatter conservé, ancienne version dans `.trash/versions`) |
| `/append note` + texte | Ajoute du texte à la fin d'une note |
| `/rmnote note` | Supprime une note (confirmation par bouton, déplacée dans `.trash/`) |
| `/mkfol chemin` (alias `/mkdir`) | Crée un dossier |
| `/rmdir chemin` | Supprime un dossier (confirmation, déplacé dans `.trash/`) |
| `/ls [dossier]` | Liste un dossier |
| `/search requête` | Recherche hybride : notes + web + synthèse LLM |
| `/search -i …` / `/search -w …` | Notes uniquement / web uniquement |
| `/synote note` | Propose des liens vers les notes proches, à valider par boutons |
| `/sync`, `/reindex`, `/status`, `/help` | Système |

Dans une recherche : `#tag` filtre par tag (tags imbriqués compris),
`/regex/` ou `/regex/i` lance une recherche par expression régulière.

Sous chaque résultat de `/search`, deux boutons :
**💾 Enregistrer comme note** (crée une note dans `Syna/Recherches/` avec la
synthèse, les sources web et des liens vers les notes du vault) et
**🔗 Notes liées**.

## Architecture

```
syna/
├── config.py              # Chargement et validation du .env
├── main.py                # Point d'entrée : services, handlers, polling
├── runtime.py             # Conteneur des services partagés
├── core/
│   ├── security.py        # Whitelist (garde global + décorateur @restricted)
│   ├── vault.py           # CRUD Obsidian, isolation des chemins
│   ├── git_sync.py        # Verrou + pull/commit/push
│   ├── messaging.py       # Découpage des messages (limite de 4096 caractères)
│   └── pending.py         # Actions en attente de confirmation (boutons)
├── services/
│   ├── markdown.py        # Frontmatter, tags, wikilinks, chunks, tokenisation
│   ├── search_internal.py # BM25 + embeddings optionnels, index incrémental
│   ├── search_external.py # Tavily / Exa + synthèse Claude
│   ├── synote_ai.py       # Liaison automatique des notes
│   └── watcher.py         # watchdog : réindexation à chaque modification
└── handlers/
    ├── commands.py        # /start /help /status /ls /sync /reindex
    ├── notes.py           # /mknot /getnot /update /append /rmnote /mkfol /rmdir /synote
    └── search.py          # /search et ses boutons
```

### Recherche

* **Interne, lexicale** : les notes sont découpées en extraits (par titre puis
  par paragraphe) et classées par BM25, sans accents ni mots vides, avec un
  bonus quand les mots de la requête sont dans le titre.
* **Interne, sémantique (optionnelle)** : définissez `EMBEDDINGS_MODEL` (par
  exemple `paraphrase-multilingual-MiniLM-L12-v2`) et installez
  `pip install -e .[semantic]`. Les embeddings sont calculés en local et gardés
  dans un index vectoriel en mémoire. Les classements lexical et sémantique
  sont combinés par *Reciprocal Rank Fusion*.
* **Fraîcheur de l'index** : seuls les fichiers modifiés sont réindexés.
  `watchdog` déclenche la mise à jour dès qu'un `.md` change (bot, Obsidian ou
  `git pull`).
* **Externe** : Tavily (par défaut) ou Exa renvoie les 4 meilleurs résultats.
  Un modèle Claude rapide et économique (`claude-haiku-5-5` par défaut, avec
  `effort: low`) écrit la synthèse. Pour éviter de surcharger le prompt, il ne
  reçoit que les extraits de notes les mieux classés (`CONTEXT_CHUNKS`), et
  cite ses sources (`[N1]` pour les notes, `[W1]` pour le web).

### /synote

Les candidats sont les notes dont le **titre est cité** dans le texte, et
celles dont la **similarité cosinus** (TF-IDF, ou embeddings si activés)
dépasse `SYNOTE_THRESHOLD`. Les notes déjà liées sont ignorées. **Rien n'est
écrit sans validation** : chaque candidat est un bouton à cocher. À
l'application, la première mention du titre devient un `[[wikilink]]` (hors
code, liens et titres). À défaut, le lien est ajouté dans une section
`## Notes liées`.

## Sécurité

* **Whitelist stricte** : `ALLOWED_USER_IDS` est obligatoire. Un garde global
  (groupe de handlers `-1`) bloque toute mise à jour venant d'un inconnu avant
  les autres handlers. Chaque commande porte aussi le décorateur `@restricted`
  (défense en profondeur). Par défaut, les inconnus sont ignorés sans réponse.
* **Path traversal** : chaque chemin passe par `Vault.resolve()`. Sont rejetés
  les chemins absolus, les composants `..`, l'octet nul, `.git/`,
  `.obsidian/`, la corbeille, et les liens symboliques qui sortent du vault
  (vérification par `is_relative_to` et `os.path.commonpath` après
  résolution). Les noms de fichiers sont nettoyés.
* **Suppressions récupérables** : `/rmnote` et `/rmdir` demandent une
  confirmation, que seul l'auteur de la commande peut donner. Les fichiers sont
  déplacés dans `.trash/`, pas effacés.
* **Secrets** : tout passe par le fichier `.env`, ignoré par Git. Les logs de
  `httpx` sont coupés pour que le token du bot n'y apparaisse pas.
* **Contenu web non fiable** : le prompt de synthèse demande au modèle de
  traiter les sources comme des données. Le texte produit n'est jamais
  exécuté : il n'est écrit dans le vault que si vous appuyez sur
  « Enregistrer ».

## Synchronisation Git

Avec `GIT_SYNC=true` (le vault doit être un dépôt Git), chaque écriture suit
ce cycle :

```
verrou → git pull --rebase --autostash → écriture → git commit → git push
```

Le verrou fait passer les écritures une par une. Si le `pull` échoue (conflit
ou réseau), le rebase est annulé et **aucune écriture n'est faite**. Si le
`push` est refusé parce que le remote a avancé, le bot refait un `pull` puis
retente le push une fois. Un `pull` périodique (`GIT_PULL_INTERVAL`) récupère
les modifications faites depuis Obsidian sur vos autres appareils.

## Installation

```bash
git clone <ce dépôt> syna && cd syna
python -m venv .venv && source .venv/bin/activate
pip install -e .            # ou : pip install -e .[semantic,dev]
cp .env.example .env        # puis remplissez les valeurs
python -m syna              # ou : syna
```

Configuration minimale dans `.env` :

```ini
TELEGRAM_BOT_TOKEN=123456:ABC...   # @BotFather
ALLOWED_USER_IDS=123456789         # votre ID (@userinfobot)
OBSIDIAN_VAULT_PATH=/chemin/absolu/vers/le/vault
```

Ajoutez `TAVILY_API_KEY` (ou `SEARCH_PROVIDER=exa` et `EXA_API_KEY`) pour la
recherche web, et `ANTHROPIC_API_KEY` pour la synthèse. Toutes les options sont
décrites dans [`.env.example`](.env.example).

### Service systemd (VPS)

```ini
# /etc/systemd/system/syna.service
[Unit]
Description=Syna Telegram bot
After=network-online.target

[Service]
WorkingDirectory=/opt/syna
ExecStart=/opt/syna/.venv/bin/python -m syna
Restart=on-failure
User=syna

[Install]
WantedBy=multi-user.target
```

## Tests

```bash
pip install -e .[dev]
pytest
ruff check .
```

Les tests couvrent l'isolation des chemins, la whitelist, la recherche
(BM25, tags, regex, index incrémental), `/synote`, le découpage des messages,
la synchronisation Git (avec un vrai dépôt distant local) et les handlers
Telegram (avec des objets simulés, sans réseau).
