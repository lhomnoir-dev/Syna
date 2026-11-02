"""Envoi de messages longs sur Telegram.

Telegram limite un message à 4096 caractères. Les textes plus longs sont
découpés proprement (paragraphes, puis lignes, puis mots) ; au-delà d'un
seuil configurable, le contenu est envoyé en pièce jointe ``.md``.
"""

from __future__ import annotations

import html
import io
import re
from collections.abc import Sequence

from telegram import InlineKeyboardMarkup, Message
from telegram.constants import ParseMode

TELEGRAM_LIMIT = 4096
SAFE_LIMIT = 4000  # marge pour les entités / numérotation


def chunk_text(text: str, limit: int = SAFE_LIMIT) -> list[str]:
    """Découpe ``text`` en morceaux de ``limit`` caractères au plus, en
    privilégiant les coupures de paragraphe, puis de ligne, puis d'espace."""
    if limit <= 0:
        raise ValueError("limit doit être positif")
    text = text.strip("\n")
    if not text:
        return []
    chunks: list[str] = []
    while len(text) > limit:
        window = text[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 3:
            cut = window.rfind("\n")
        if cut < limit // 3:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip("\n ")
    if text:
        chunks.append(text)
    return chunks


def esc(text: str) -> str:
    """Échappement HTML pour ``parse_mode=HTML``."""
    return html.escape(text, quote=False)


async def send_long(
    message: Message,
    text: str,
    *,
    html_mode: bool = False,
    max_inline_chars: int = 8000,
    filename: str = "syna.md",
    file_content: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Répond à ``message`` avec ``text`` en gérant la limite de Telegram.

    * ``len(text) <= max_inline_chars`` : un ou plusieurs messages découpés.
    * sinon : envoi d'un fichier ``.md`` (``file_content`` ou ``text``).

    En mode HTML, le découpage ne se fait qu'aux sauts de ligne : les
    appelants doivent garder leurs balises sur une seule ligne.
    """
    parse_mode = ParseMode.HTML if html_mode else None
    if len(text) > max_inline_chars:
        payload = (file_content if file_content is not None else text).encode("utf-8")
        await message.reply_document(
            document=io.BytesIO(payload),
            filename=filename,
            caption="📎 Contenu trop long pour un message : envoyé en fichier.",
            reply_markup=reply_markup,
        )
        return
    chunks = _chunk_html(text) if html_mode else chunk_text(text)
    if not chunks:
        chunks = ["(vide)"]
    for i, chunk in enumerate(chunks):
        is_last = i == len(chunks) - 1
        await message.reply_text(
            chunk,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
            reply_markup=reply_markup if is_last else None,
        )


def _chunk_html(text: str, limit: int = SAFE_LIMIT) -> list[str]:
    """Découpe du HTML uniquement aux sauts de ligne ; une ligne trop longue
    est tronquée (après retrait des balises) pour ne jamais casser une balise."""
    lines: list[str] = []
    for line in text.split("\n"):
        if len(line) > limit:
            plain = html.unescape(_strip_tags(line))
            line = esc(plain[: limit - 10]) + "…"
        lines.append(line)
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) + 1 > limit and current:
            chunks.append("\n".join(current).strip("\n"))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current and "\n".join(current).strip():
        chunks.append("\n".join(current).strip("\n"))
    return chunks


def _strip_tags(line: str) -> str:
    return re.sub(r"<[^>]+>", "", line)


def bullet_list(items: Sequence[str], limit: int = 50) -> str:
    shown = list(items[:limit])
    lines = [f"• {item}" for item in shown]
    if len(items) > limit:
        lines.append(f"… et {len(items) - limit} autre(s)")
    return "\n".join(lines)
