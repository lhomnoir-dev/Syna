"""Authentification : whitelist stricte d'IDs Telegram.

Deux niveaux de protection :

1. ``WhitelistGuard`` : un handler enregistré dans le groupe ``-1`` qui voit
   *toutes* les mises à jour avant les autres handlers et stoppe la
   propagation (``ApplicationHandlerStop``) pour tout expéditeur inconnu.
2. ``restricted`` : un décorateur appliqué à chaque commande, en défense en
   profondeur (si un handler était un jour enregistré sans le garde global).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

log = logging.getLogger(__name__)

UNAUTHORIZED_MESSAGE = "⛔ Accès non autorisé. Ce bot est privé."

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[Any]]


def is_authorized(update: Update, allowed: frozenset[int]) -> bool:
    user = update.effective_user
    return user is not None and user.id in allowed


def _settings(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data["services"].settings


async def _reject(update: Update, reply: bool) -> None:
    user = update.effective_user
    log.warning(
        "Accès refusé : user_id=%s username=%s",
        getattr(user, "id", None),
        getattr(user, "username", None),
    )
    if not reply:
        return
    if update.callback_query is not None:
        await update.callback_query.answer(UNAUTHORIZED_MESSAGE, show_alert=True)
    elif update.effective_message is not None:
        await update.effective_message.reply_text(UNAUTHORIZED_MESSAGE)


async def whitelist_guard(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler global (groupe -1) : bloque tout expéditeur hors whitelist."""
    if not isinstance(update, Update):
        raise ApplicationHandlerStop
    settings = _settings(context)
    if not is_authorized(update, settings.allowed_user_ids):
        await _reject(update, settings.reply_unauthorized)
        raise ApplicationHandlerStop


def restricted(func: Handler) -> Handler:
    """Décorateur : n'exécute la commande que pour un utilisateur autorisé."""

    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args: Any, **kwargs: Any):
        settings = _settings(context)
        if not is_authorized(update, settings.allowed_user_ids):
            await _reject(update, settings.reply_unauthorized)
            return None
        return await func(update, context, *args, **kwargs)

    return wrapper
