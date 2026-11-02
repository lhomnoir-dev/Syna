"""/search : recherche hybride (vault + web) et boutons interactifs."""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from ..core.messaging import esc, send_long
from ..core.vault import NoteExistsError
from ..services.search_external import LLMError, SearchProviderError, WebResult
from ..services.search_internal import SearchHit
from .common import command, get_services, parse_args
from .notes import synote_button

log = logging.getLogger(__name__)

CB_SEARCH = "srch"
MODES = {"-i": "internal", "--interne": "internal", "-w": "web", "--web": "web"}


def parse_search(text: str) -> tuple[str, str]:
    """Renvoie (mode, requête) ; mode ∈ {hybrid, internal, web}."""
    words = text.split()
    mode = "hybrid"
    while words and words[0].lower() in MODES:
        mode = MODES[words.pop(0).lower()]
    return mode, " ".join(words)


@command
async def search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = get_services(context)
    args = parse_args(update)
    raw = " ".join(x for x in (args.target, args.body) if x)
    mode, query = parse_search(raw)
    if not query:
        await update.effective_message.reply_text(
            "Usage : /search [-i|-w] requête\n-i : notes uniquement · -w : web uniquement\n"
            "Astuces : #tag pour filtrer, /regex/ pour une expression régulière."
        )
        return
    if len(query) > 500:
        raise ValueError("Requête trop longue (500 caractères max).")

    await update.effective_chat.send_action(ChatAction.TYPING)

    hits: list[SearchHit] = []
    if mode in ("hybrid", "internal"):
        hits = await asyncio.to_thread(s.index.search, query, s.settings.internal_results)

    web: list[WebResult] = []
    answer: str | None = None
    notices: list[str] = []
    is_special = query.startswith("/") or query.startswith("#")  # regex / tags : vault uniquement
    if mode in ("hybrid", "web") and not is_special:
        if s.provider is None:
            if mode == "web":
                notices.append("Recherche web désactivée : configurez TAVILY_API_KEY ou EXA_API_KEY.")
        else:
            try:
                web = await s.provider.search(query, s.settings.web_results)
            except SearchProviderError as exc:
                notices.append(str(exc))
        if s.synthesizer is not None and (web or hits):
            # On n'envoie au modèle que les extraits les plus pertinents.
            context_chunks = (
                await asyncio.to_thread(s.index.top_chunks, query, s.settings.context_chunks) if mode == "hybrid" else []
            )
            try:
                await update.effective_chat.send_action(ChatAction.TYPING)
                answer = await s.synthesizer.synthesize(query, context_chunks, web)
            except LLMError as exc:
                notices.append(f"Synthèse indisponible : {exc}")

    text = format_results(query, mode, hits, web, answer, notices)
    token = s.pending.add(
        CB_SEARCH,
        update.effective_user.id,
        {
            "query": query,
            "answer": answer,
            "web": [r.__dict__ for r in web],
            "hits": [{"rel": h.rel, "title": h.title} for h in hits],
        },
    )
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💾 Enregistrer comme note", callback_data=f"{CB_SEARCH}:{token}:save"),
                InlineKeyboardButton("🔗 Notes liées", callback_data=f"{CB_SEARCH}:{token}:related"),
            ]
        ]
    )
    await send_long(
        update.effective_message,
        text,
        html_mode=True,
        max_inline_chars=s.settings.max_inline_chars,
        filename="recherche.md",
        file_content=research_note_body(query, answer, web, hits),
        reply_markup=keyboard,
    )


def format_results(
    query: str,
    mode: str,
    hits: list[SearchHit],
    web: list[WebResult],
    answer: str | None,
    notices: list[str],
) -> str:
    lines = [f"🔎 <b>{esc(query)}</b>", ""]
    if mode in ("hybrid", "internal"):
        lines.append("📚 <b>Sources internes (Obsidian)</b>")
        if hits:
            for h in hits:
                lines.append(f"• <b>{esc(h.location)}</b> <code>{esc(h.rel)}</code>")
                if h.excerpt:
                    lines.append(f"  <i>{esc(h.excerpt)}</i>")
        else:
            lines.append("Aucune note trouvée.")
        lines.append("")
    if mode in ("hybrid", "web") and (web or answer):
        lines.append("🌐 <b>Synthèse externe (Web)</b>")
        if answer:
            lines += [esc(line) for line in answer.splitlines()]
        elif web:
            for r in web:
                snippet = r.content.replace("\n", " ")[:250]
                lines.append(f"• {esc(snippet)}…" if snippet else f"• {esc(r.title)}")
        if web:
            lines.append("")
            lines.append("<b>Sources web</b>")
            for i, r in enumerate(web, 1):
                lines.append(f'[W{i}] <a href="{html.escape(r.url, quote=True)}">{esc(r.title or r.url)}</a>')
        lines.append("")
    for n in notices:
        lines.append(f"⚠️ {esc(n)}")
    return "\n".join(lines).strip()


def research_note_body(query: str, answer: str | None, web: list[WebResult], hits: list[SearchHit] | list[dict]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    query_yaml = query.replace('"', "'")
    parts = [
        "---",
        f"created: {now}",
        "tags: [syna, recherche]",
        f'query: "{query_yaml}"',
        "---",
        "",
        f"# {query}",
        "",
    ]
    if answer:
        parts += ["## Synthèse", "", answer.strip(), ""]
    elif web:
        parts += ["## Résultats", ""]
        for r in web:
            parts.append(f"- **{r.title}** — {r.content.strip()[:300]}")
        parts.append("")
    if web:
        parts += ["## Sources web", ""]
        parts += [f"- [W{i}] [{r.title or r.url}]({r.url})" for i, r in enumerate(web, 1)]
        parts.append("")
    if hits:
        parts += ["## Notes du vault", ""]
        for h in hits:
            title = h["title"] if isinstance(h, dict) else h.title
            parts.append(f"- [[{title}]]")
        parts.append("")
    return "\n".join(parts)


@command
async def on_search_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = get_services(context)
    query = update.callback_query
    _, token, choice = ([*query.data.split(":"), "", ""])[:3]
    action = s.pending.get(token, update.effective_user.id, CB_SEARCH)
    if action is None:
        await query.answer("Résultat expiré, relancez la recherche.", show_alert=True)
        return
    payload = action.payload
    web = [WebResult(**r) for r in payload["web"]]

    if choice == "save":
        await query.answer("Enregistrement…")
        body = research_note_body(payload["query"], payload["answer"], web, payload["hits"])
        folder = s.settings.research_folder
        base = f"{folder}/{payload['query']}" if folder else payload["query"]
        async with s.git.transaction(f"recherche « {payload['query'][:60]} »") as warnings:
            try:
                path = s.vault.create_note(base, body)
            except NoteExistsError:
                path = s.vault.create_note(f"{base} {datetime.now():%Y-%m-%d %H%M%S}", body)
        s.index.mark_dirty()
        rel = s.vault.relpath(path)
        payload["saved"] = rel
        text = f"💾 Réponse enregistrée : <code>{esc(rel)}</code>"
        if warnings:
            text += "\n" + esc("\n".join(warnings))
        await query.message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=synote_button(rel, s.pending, update.effective_user.id)
        )
        return

    if choice == "related":
        await query.answer()
        text = " ".join(x for x in (payload["query"], payload["answer"] or "") if x)
        exclude = {payload["saved"]} if payload.get("saved") else set()
        related = await asyncio.to_thread(s.index.similar_notes, text, exclude=exclude, k=8)
        related = [(rel, score) for rel, score in related if score >= s.settings.synote_threshold / 2]
        titles = s.index.titles()
        if not related:
            await query.message.reply_text("🔗 Aucune note liée trouvée dans le vault.")
            return
        lines = ["🔗 <b>Notes liées dans le vault</b>"]
        lines += [f"• {esc(titles.get(rel, rel))} <code>{esc(rel)}</code> — {score:.2f}" for rel, score in related]
        lines.append("\nUtilisez /synote <i>note</i> pour créer les liens.")
        await query.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return

    await query.answer()
