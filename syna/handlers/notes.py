"""Commandes de gestion des notes et dossiers, et /synote."""

from __future__ import annotations

import asyncio
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..core.messaging import esc, send_long
from ..core.vault import NoteNotFoundError, VaultError
from ..services.markdown import split_frontmatter
from ..services.synote_ai import apply_links, find_link_candidates, link_target
from .common import command, get_services, parse_args

CB_DELETE = "del"
CB_SYNOTE = "syn"
CB_SYNOTE_START = "synstart"


def _created_frontmatter() -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"---\ncreated: {now}\ntags: [syna]\n---\n\n"


def synote_button(rel: str, store, user_id: int) -> InlineKeyboardMarkup:
    token = store.add(CB_SYNOTE_START, user_id, {"rel": rel})
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔗 Lier aux notes existantes (/synote)", callback_data=f"{CB_SYNOTE_START}:{token}")]]
    )


# ------------------------------------------------------------------- notes
@command
async def mknot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = get_services(context)
    args = parse_args(update)
    if not args.target:
        await update.effective_message.reply_text(
            "Usage : /mknot Dossier/Titre\nContenu de la note…\n\nou : /mknot Titre | contenu"
        )
        return
    target = args.target
    # Sans dossier explicite, la note va dans le dossier d'arrivée (inbox).
    if "/" not in target and s.settings.inbox_folder:
        target = f"{s.settings.inbox_folder}/{target}"
    content = _created_frontmatter() + (args.body + "\n" if args.body else "")
    async with s.git.transaction(f"création de {target}") as warnings:
        path = s.vault.create_note(target, content)
    s.index.mark_dirty()
    rel = s.vault.relpath(path)
    text = f"✅ Note créée : <code>{esc(rel)}</code>"
    if warnings:
        text += "\n" + esc("\n".join(warnings))
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=synote_button(rel, s.pending, update.effective_user.id)
    )


@command
async def getnot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = get_services(context)
    args = parse_args(update)
    if not args.target:
        await update.effective_message.reply_text("Usage : /getnot Titre ou Dossier/Titre")
        return
    path = s.vault.get_note(args.target)
    content = s.vault.read(path)
    rel = s.vault.relpath(path)
    header = f"📝 {rel}\n\n"
    await send_long(
        update.effective_message,
        header + content,
        max_inline_chars=s.settings.max_inline_chars,
        filename=path.name,
        file_content=content,
    )


@command
async def update_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = get_services(context)
    args = parse_args(update)
    if not args.target or not args.body:
        await update.effective_message.reply_text("Usage : /update Titre\nNouveau contenu complet…")
        return
    async with s.git.transaction(f"mise à jour de {args.target}") as warnings:
        path = s.vault.get_note(args.target)
        old = s.vault.read(path)
        # Sauvegarde de l'ancienne version dans la corbeille avant remplacement.
        s.vault.backup(path)
        # Le frontmatter existant est conservé, sauf si le nouveau contenu en fournit un.
        fm, _ = split_frontmatter(old)
        keep_fm = fm and not args.body.startswith("---")
        new_content = (f"---\n{fm}\n---\n\n" if keep_fm else "") + args.body + "\n"
        s.vault.write(path, new_content)
    s.index.mark_dirty()
    msg = f"✅ Note mise à jour : {s.vault.relpath(path)} (ancienne version dans {s.settings.trash_folder}/versions)"
    await update.effective_message.reply_text("\n".join([msg, *warnings]))


@command
async def append(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = get_services(context)
    args = parse_args(update)
    if not args.target or not args.body:
        await update.effective_message.reply_text("Usage : /append Titre\nTexte à ajouter…")
        return
    async with s.git.transaction(f"ajout à {args.target}") as warnings:
        path = s.vault.get_note(args.target)
        s.vault.append(path, args.body)
    s.index.mark_dirty()
    await update.effective_message.reply_text("\n".join([f"✅ Texte ajouté à {s.vault.relpath(path)}", *warnings]))


@command
async def rmnote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = get_services(context)
    args = parse_args(update)
    if not args.target:
        await update.effective_message.reply_text("Usage : /rmnote Titre ou Dossier/Titre")
        return
    path = s.vault.get_note(args.target)
    rel = s.vault.relpath(path)
    await _ask_delete(update, s, "note", rel)


# ----------------------------------------------------------------- dossiers
@command
async def mkfol(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = get_services(context)
    args = parse_args(update)
    if not args.target:
        await update.effective_message.reply_text("Usage : /mkfol Dossier/Sous-dossier")
        return
    async with s.git.transaction(f"création du dossier {args.target}") as warnings:
        path = s.vault.create_folder(args.target)
        # Git ne versionne pas les dossiers vides.
        if s.git.enabled:
            (path / ".gitkeep").touch()
    await update.effective_message.reply_text("\n".join([f"✅ Dossier créé : {s.vault.relpath(path)}/", *warnings]))


@command
async def rmdir(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = get_services(context)
    args = parse_args(update)
    if not args.target:
        await update.effective_message.reply_text("Usage : /rmdir Dossier")
        return
    path = s.vault.get_folder(args.target)
    if path == s.vault.root:
        raise VaultError("Impossible de supprimer la racine du vault.")
    await _ask_delete(update, s, "folder", s.vault.relpath(path))


async def _ask_delete(update: Update, s, kind: str, rel: str) -> None:
    token = s.pending.add(CB_DELETE, update.effective_user.id, {"kind": kind, "rel": rel})
    if kind == "folder":
        n_notes, n_dirs = s.vault.count_items(s.vault.resolve(rel))
        question = f"🗑 Supprimer le dossier <code>{esc(rel)}/</code> ({n_notes} note(s), {n_dirs} sous-dossier(s)) ?"
    else:
        question = f"🗑 Supprimer la note <code>{esc(rel)}</code> ?"
    question += f"\nElle sera déplacée dans <code>{esc(s.settings.trash_folder)}/</code>."
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Oui, supprimer", callback_data=f"{CB_DELETE}:{token}:yes"),
                InlineKeyboardButton("❌ Annuler", callback_data=f"{CB_DELETE}:{token}:no"),
            ]
        ]
    )
    await update.effective_message.reply_text(question, parse_mode=ParseMode.HTML, reply_markup=keyboard)


@command
async def on_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = get_services(context)
    query = update.callback_query
    _, token, choice = ([*query.data.split(":"), "", ""])[:3]
    action = s.pending.pop(token, update.effective_user.id, CB_DELETE)
    if action is None:
        await query.answer("Action expirée.", show_alert=True)
        await query.edit_message_reply_markup(None)
        return
    await query.answer()
    if choice != "yes":
        await query.edit_message_text("Suppression annulée.")
        return
    kind, rel = action.payload["kind"], action.payload["rel"]
    async with s.git.transaction(f"suppression de {rel}") as warnings:
        path = s.vault.resolve(rel)
        if kind == "folder":
            s.vault.delete_folder(path)
        else:
            if not path.is_file():
                raise NoteNotFoundError("La note n'existe plus.")
            s.vault.delete_note(path)
    s.index.mark_dirty()
    await query.edit_message_text("\n".join([f"🗑 Supprimé : {rel} (récupérable dans {s.settings.trash_folder}/)", *warnings]))


# ------------------------------------------------------------------- synote
@command
async def synote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = get_services(context)
    args = parse_args(update)
    if not args.target:
        await update.effective_message.reply_text("Usage : /synote Titre de la note")
        return
    path = s.vault.get_note(args.target)
    await _propose_links(update.effective_message, update.effective_user.id, s, s.vault.relpath(path))


async def _propose_links(message, user_id: int, s, rel: str) -> None:
    path = s.vault.resolve(rel)
    if not path.is_file():
        raise NoteNotFoundError(f"Note introuvable : {rel}")
    candidates = await asyncio.to_thread(
        find_link_candidates,
        s.vault,
        s.index,
        path,
        threshold=s.settings.synote_threshold,
        max_links=s.settings.synote_max_links,
    )
    if not candidates:
        await message.reply_text(
            f"🔗 Aucune note suffisamment proche de « {path.stem} » (seuil {s.settings.synote_threshold:.2f})."
        )
        return
    payload = {
        "rel": rel,
        "candidates": [{"rel": c.rel, "title": c.title, "reason": c.reason} for c in candidates],
        "selected": list(range(len(candidates))),
    }
    token = s.pending.add(CB_SYNOTE, user_id, payload)
    await message.reply_text(
        _synote_text(path.stem, payload), parse_mode=ParseMode.HTML, reply_markup=_synote_keyboard(token, payload)
    )


def _synote_text(title: str, payload: dict) -> str:
    lines = [f"🔗 <b>Liens proposés pour « {esc(title)} »</b>", "Touchez une note pour (dé)sélectionner, puis validez :", ""]
    for i, c in enumerate(payload["candidates"]):
        mark = "✅" if i in payload["selected"] else "⬜"
        lines.append(f"{mark} {esc(c['title'])} — <i>{esc(c['reason'])}</i>")
    return "\n".join(lines)


def _synote_keyboard(token: str, payload: dict) -> InlineKeyboardMarkup:
    rows = []
    for i, c in enumerate(payload["candidates"]):
        mark = "✅" if i in payload["selected"] else "⬜"
        label = f"{mark} {c['title']}"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"{CB_SYNOTE}:{token}:t{i}")])
    rows.append(
        [
            InlineKeyboardButton("🔗 Lier la sélection", callback_data=f"{CB_SYNOTE}:{token}:apply"),
            InlineKeyboardButton("❌ Annuler", callback_data=f"{CB_SYNOTE}:{token}:cancel"),
        ]
    )
    return InlineKeyboardMarkup(rows)


@command
async def on_synote_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = get_services(context)
    query = update.callback_query
    token = query.data.split(":", 1)[1] if ":" in query.data else ""
    action = s.pending.pop(token, update.effective_user.id, CB_SYNOTE_START)
    if action is None:
        await query.answer("Action expirée.", show_alert=True)
        return
    await query.answer("Analyse des liens…")
    await query.edit_message_reply_markup(None)
    await _propose_links(query.message, update.effective_user.id, s, action.payload["rel"])


@command
async def on_synote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = get_services(context)
    query = update.callback_query
    _, token, choice = ([*query.data.split(":"), "", ""])[:3]
    action = s.pending.get(token, update.effective_user.id, CB_SYNOTE)
    if action is None:
        await query.answer("Action expirée.", show_alert=True)
        await query.edit_message_reply_markup(None)
        return
    payload = action.payload
    title = s.vault.resolve(payload["rel"]).stem

    if choice.startswith("t") and choice[1:].isdigit():
        i = int(choice[1:])
        if 0 <= i < len(payload["candidates"]):
            selected = set(payload["selected"])
            selected ^= {i}
            payload["selected"] = sorted(selected)
        await query.answer()
        await query.edit_message_text(
            _synote_text(title, payload), parse_mode=ParseMode.HTML, reply_markup=_synote_keyboard(token, payload)
        )
        return

    s.pending.pop(token, update.effective_user.id, CB_SYNOTE)
    await query.answer()
    if choice != "apply" or not payload["selected"]:
        await query.edit_message_text("Aucun lien ajouté.")
        return

    chosen = [payload["candidates"][i] for i in payload["selected"]]
    async with s.git.transaction(f"synote {payload['rel']}") as warnings:
        path = s.vault.resolve(payload["rel"])
        if not path.is_file():
            raise NoteNotFoundError("La note n'existe plus.")
        titles = s.index.titles()
        links = [(c["title"], link_target(c["rel"], titles)) for c in chosen]
        s.vault.write(path, apply_links(s.vault.read(path), links))
    s.index.mark_dirty()
    names = ", ".join(f"[[{c['title']}]]" for c in chosen)
    await query.edit_message_text("\n".join([f"✅ {len(chosen)} lien(s) ajouté(s) à « {title} » : {names}", *warnings]))
