"""Commandes de base : /start, /help, /status, /ls, /sync, /reindex."""

from __future__ import annotations

import asyncio

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..core.messaging import esc, send_long
from .common import command, get_services, parse_args

HELP_TEXT = """<b>Syna</b> — assistant de recherche connecté à votre vault Obsidian.

<b>📝 Notes</b>
/mknot <i>Dossier/Titre</i> — créer une note
   contenu : lignes suivantes, <code>Titre | contenu</code> ou en réponse à un message
/getnot <i>note</i> — afficher une note
/update <i>note</i> + contenu — remplacer le contenu (ancienne version dans la corbeille)
/append <i>note</i> + texte — ajouter à la fin d'une note
/rmnote <i>note</i> — supprimer une note (avec confirmation, vers la corbeille)

<b>📁 Dossiers</b>
/mkfol <i>chemin</i> — créer un dossier (alias /mkdir)
/rmdir <i>chemin</i> — supprimer un dossier (avec confirmation, vers la corbeille)
/ls [<i>dossier</i>] — lister un dossier

<b>🔎 Recherche</b>
/search <i>requête</i> — recherche hybride (notes + web + synthèse)
/search -i <i>requête</i> — notes uniquement · /search -w <i>requête</i> — web uniquement
Astuces : <code>#tag</code> filtre par tag, <code>/regex/</code> (ou <code>/regex/i</code>) recherche par expression régulière.

<b>🔗 Liens</b>
/synote <i>note</i> — proposer des wikilinks vers les notes liées (validation par boutons)

<b>⚙️ Système</b>
/sync — synchroniser le vault avec Git · /reindex — reconstruire l'index · /status — état du bot"""

BOT_COMMANDS = [
    ("mknot", "Créer une note"),
    ("getnot", "Afficher une note"),
    ("update", "Remplacer le contenu d'une note"),
    ("append", "Ajouter du texte à une note"),
    ("rmnote", "Supprimer une note"),
    ("mkfol", "Créer un dossier"),
    ("rmdir", "Supprimer un dossier"),
    ("ls", "Lister un dossier"),
    ("search", "Rechercher (notes + web)"),
    ("synote", "Lier automatiquement une note"),
    ("sync", "Synchroniser avec Git"),
    ("reindex", "Reconstruire l'index"),
    ("status", "État du bot"),
    ("help", "Aide"),
]


@command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.effective_message.reply_text(
        f"👋 Bonjour {esc(user.first_name or '')} ! Je suis <b>Syna</b>.\n\n" + HELP_TEXT,
        parse_mode=ParseMode.HTML,
    )


@command
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


@command
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = get_services(context)
    await asyncio.to_thread(s.index.refresh)
    stats = s.index.stats()
    cfg = s.settings
    if not s.git.enabled:
        git_state = "désactivé"
    elif s.git.has_remote:
        git_state = f"activé (remote {esc(cfg.git_remote)})"
    else:
        git_state = "activé (commits locaux)"
    lines = [
        "<b>État de Syna</b>",
        f"📚 Vault : <code>{esc(str(cfg.vault_path))}</code>",
        f"📝 Notes indexées : {stats['notes']} ({stats['chunks']} extraits)",
        f"🧠 Recherche sémantique : {'oui (' + esc(cfg.embeddings_model) + ')' if s.index.embedder else 'non (lexicale BM25)'}",
        f"🌐 Recherche web : {s.provider.name if s.provider else 'désactivée'}",
        f"🤖 Synthèse LLM : {esc(s.synthesizer.model) if s.synthesizer else 'désactivée'}",
        f"🔄 Git : {git_state}",
        f"👀 Surveillance des fichiers : {'oui' if s.watcher and s.watcher.running else 'non'}",
        f"🔗 Seuil /synote : {cfg.synote_threshold:.2f}",
    ]
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@command
async def ls(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = get_services(context)
    args = parse_args(update)
    listing = s.vault.list_dir(args.target)
    header = f"📂 <b>{esc(listing.path or 'Racine du vault')}</b>"
    lines = [header]
    if not listing.folders and not listing.notes:
        lines.append("(dossier vide)")
    lines += [f"📁 {esc(name)}/" for name in listing.folders]
    lines += [f"📝 {esc(name)}" for name in listing.notes]
    await send_long(
        update.effective_message,
        "\n".join(lines),
        html_mode=True,
        max_inline_chars=s.settings.max_inline_chars,
        filename="liste.md",
    )


@command
async def sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = get_services(context)
    msg = await s.git.sync()
    s.index.mark_dirty()
    await update.effective_message.reply_text(msg)


@command
async def reindex(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = get_services(context)
    await update.effective_message.reply_text("⏳ Réindexation du vault…")
    await asyncio.to_thread(s.index.refresh, True)
    stats = s.index.stats()
    await update.effective_message.reply_text(f"✅ Index reconstruit : {stats['notes']} notes, {stats['chunks']} extraits.")
