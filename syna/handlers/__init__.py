"""Enregistrement des handlers Telegram."""

from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, TypeHandler

from ..core.security import whitelist_guard
from . import commands, notes, search


def register_handlers(app: Application) -> None:
    # Garde global : s'exécute avant tout autre handler (groupe -1) et bloque
    # les utilisateurs hors whitelist.
    app.add_handler(TypeHandler(Update, whitelist_guard), group=-1)

    routes = {
        "start": commands.start,
        "help": commands.help_command,
        "status": commands.status,
        "ls": commands.ls,
        "sync": commands.sync,
        "reindex": commands.reindex,
        "mknot": notes.mknot,
        "getnot": notes.getnot,
        "update": notes.update_note,
        "append": notes.append,
        "rmnote": notes.rmnote,
        "mkfol": notes.mkfol,
        "mkdir": notes.mkfol,
        "rmdir": notes.rmdir,
        "synote": notes.synote,
        "search": search.search,
    }
    for name, callback in routes.items():
        app.add_handler(CommandHandler(name, callback))

    app.add_handler(CallbackQueryHandler(notes.on_delete_callback, pattern=rf"^{notes.CB_DELETE}:"))
    app.add_handler(CallbackQueryHandler(notes.on_synote_start, pattern=rf"^{notes.CB_SYNOTE_START}:"))
    app.add_handler(CallbackQueryHandler(notes.on_synote_callback, pattern=rf"^{notes.CB_SYNOTE}:"))
    app.add_handler(CallbackQueryHandler(search.on_search_callback, pattern=rf"^{search.CB_SEARCH}:"))
