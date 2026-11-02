"""Outils communs aux handlers : décorateur de commande, parsing des arguments."""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from ..core.git_sync import SyncError
from ..core.security import restricted
from ..core.vault import VaultError
from ..runtime import Services

log = logging.getLogger(__name__)

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[Any]]

_COMMAND_RE = re.compile(r"^/[A-Za-z0-9_]+(?:@\w+)?[ \t]*")


def get_services(context: ContextTypes.DEFAULT_TYPE) -> Services:
    return context.application.bot_data["services"]


def command(func: Handler) -> Handler:
    """``@restricted`` + conversion des erreurs fonctionnelles en réponses."""

    @restricted
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            return await func(update, context)
        except (VaultError, SyncError) as exc:
            await _reply_error(update, str(exc))
        except ValueError as exc:
            await _reply_error(update, str(exc))

    return wrapper


async def _reply_error(update: Update, text: str) -> None:
    text = f"❌ {text}"
    if update.callback_query is not None:
        await update.callback_query.answer()
        if update.callback_query.message is not None:
            await update.callback_query.message.reply_text(text)
    elif update.effective_message is not None:
        await update.effective_message.reply_text(text)


@dataclass
class CommandArgs:
    target: str
    body: str

    @property
    def empty(self) -> bool:
        return not self.target and not self.body


def parse_args(update: Update) -> CommandArgs:
    """Analyse ``/commande cible`` puis un contenu facultatif.

    Formats acceptés ::

        /mknot Dossier/Titre
        Contenu sur les lignes suivantes…

        /mknot Titre | contenu sur une ligne

    Si aucun contenu n'est fourni et que la commande répond à un message, le
    texte de ce message est utilisé comme contenu.
    """
    message = update.effective_message
    text = (message.text or message.caption or "") if message else ""
    # Retire la commande (et l'éventuel @NomDuBot).
    m = _COMMAND_RE.match(text)
    if m:
        text = text[m.end() :]
        if text.startswith("\n"):
            text = text[1:]
    first_line, _, body = text.partition("\n")
    target = first_line.strip()
    if " | " in target:
        target, _, inline = target.partition(" | ")
        body = inline + ("\n" + body if body else "")
    target = target.strip()
    body = body.strip("\n")
    reply = message.reply_to_message if message else None
    if not body and reply is not None:
        body = (reply.text or reply.caption or "").strip()
    return CommandArgs(target=target, body=body)
