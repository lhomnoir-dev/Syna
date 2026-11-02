"""Tests des handlers Telegram avec des objets simulés (aucun appel réseau)."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from telegram import Chat, Message, Update, User
from telegram.ext import ApplicationHandlerStop

from syna.config import Settings
from syna.core.git_sync import GitSync
from syna.core.pending import PendingStore
from syna.core.security import whitelist_guard
from syna.handlers import commands, notes, search
from syna.runtime import Services
from syna.services.search_external import WebResult
from syna.services.search_internal import VaultIndex

OWNER = 42
STRANGER = 666


class FakeMessage:
    def __init__(self, text: str = "", reply_to=None) -> None:
        self.text = text
        self.caption = None
        self.reply_to_message = reply_to
        self.replies: list[tuple[str, dict]] = []
        self.documents: list[tuple[str, bytes]] = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return FakeMessage(text)

    async def reply_document(self, document, filename, **kwargs):
        self.documents.append((filename, document.read()))

    @property
    def last(self) -> str:
        return self.replies[-1][0]

    def last_buttons(self) -> list[str]:
        markup = self.replies[-1][1].get("reply_markup")
        return [b.callback_data for row in markup.inline_keyboard for b in row]


class FakeChat:
    async def send_action(self, action):
        return None


class FakeQuery:
    def __init__(self, data: str, message: FakeMessage) -> None:
        self.data = data
        self.message = message
        self.answers: list[tuple] = []
        self.edits: list[tuple[str, dict]] = []

    async def answer(self, *args, **kwargs):
        self.answers.append(args)

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))

    async def edit_message_reply_markup(self, markup=None):
        return None


def make_update(text: str = "", user_id: int = OWNER, query: FakeQuery | None = None, reply_to=None):
    message = query.message if query else FakeMessage(text, reply_to)
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id, first_name="Ali", username="ali"),
        effective_message=message,
        effective_chat=FakeChat(),
        callback_query=query,
    )


class FakeProvider:
    name = "Fake"

    async def search(self, query, max_results):
        return [WebResult("Doc asyncio", "https://docs.python.org/asyncio", "asyncio est une bibliothèque...")]


class FakeSynth:
    model = "fake"

    def __init__(self) -> None:
        self.calls = []

    async def synthesize(self, query, notes_, web):
        self.calls.append((query, list(notes_), list(web)))
        return "asyncio permet l'asynchronisme [N1][W1]."


@pytest.fixture
def services(vault):
    settings = Settings(telegram_token="t", allowed_user_ids=frozenset({OWNER}), vault_path=vault.root, max_inline_chars=3000)
    index = VaultIndex(vault)
    index.refresh(force=True)
    return Services(
        settings=settings,
        vault=vault,
        index=index,
        git=GitSync(vault.root, enabled=False),
        provider=FakeProvider(),
        synthesizer=FakeSynth(),
        pending=PendingStore(),
    )


@pytest.fixture
def ctx(services):
    return SimpleNamespace(application=SimpleNamespace(bot_data={"services": services}))


async def test_unauthorized_user_is_ignored(ctx, vault) -> None:
    upd = make_update("/mknot Intrus | spam", user_id=STRANGER)
    await notes.mknot(upd, ctx)
    assert upd.effective_message.replies == []
    assert not list(vault.root.glob("Intrus*"))


async def test_global_guard_blocks_unknown_users(ctx) -> None:
    def real_update(uid: int) -> Update:
        user = User(id=uid, first_name="x", is_bot=False)
        msg = Message(message_id=1, date=datetime.now(), chat=Chat(id=uid, type="private"), from_user=user, text="/ls")
        return Update(update_id=1, message=msg)

    with pytest.raises(ApplicationHandlerStop):
        await whitelist_guard(real_update(STRANGER), ctx)
    await whitelist_guard(real_update(OWNER), ctx)  # ne lève rien


async def test_mknot_multiline_and_inline(ctx, vault) -> None:
    upd = make_update("/mknot Projets/Idée géniale\nPremière ligne\nDeuxième ligne")
    await notes.mknot(upd, ctx)
    content = (vault.root / "Projets" / "Idée géniale.md").read_text(encoding="utf-8")
    assert content.startswith("---\ncreated:")
    assert "Première ligne\nDeuxième ligne" in content
    assert "Note créée" in upd.effective_message.last
    assert upd.effective_message.last_buttons()[0].startswith("synstart:")

    upd = make_update("/mknot@SynaBot Rapide | contenu court")
    await notes.mknot(upd, ctx)
    assert "contenu court" in (vault.root / "Rapide.md").read_text(encoding="utf-8")


async def test_mknot_from_replied_message(ctx, vault) -> None:
    upd = make_update("/mknot Transfert", reply_to=FakeMessage("texte transféré"))
    await notes.mknot(upd, ctx)
    assert "texte transféré" in (vault.root / "Transfert.md").read_text(encoding="utf-8")


async def test_mknot_rejects_path_traversal(ctx, vault) -> None:
    upd = make_update("/mknot ../../evil | x")
    await notes.mknot(upd, ctx)
    assert upd.effective_message.last.startswith("❌")
    assert not (vault.root.parent / "evil.md").exists()


async def test_getnot_short_and_long(ctx, vault) -> None:
    upd = make_update("/getnot Recette crêpes")
    await notes.getnot(upd, ctx)
    assert "Farine" in upd.effective_message.last
    vault.create_note("Longue", "mot " * 1000)
    upd = make_update("/getnot Longue")
    await notes.getnot(upd, ctx)
    assert upd.effective_message.documents[0][0] == "Longue.md"


async def test_update_keeps_frontmatter_and_backup(ctx, vault) -> None:
    upd = make_update("/update Python asyncio\nNouveau contenu")
    await notes.update_note(upd, ctx)
    content = vault.get_note("Python asyncio").read_text(encoding="utf-8")
    assert content.startswith("---\ntags: [python, dev]\n---\n\nNouveau contenu")
    assert list((vault.root / ".trash" / "versions").rglob("Python asyncio (*).md"))


async def test_rmnote_requires_confirmation_by_owner(ctx, vault) -> None:
    upd = make_update("/rmnote Microservices")
    await notes.rmnote(upd, ctx)
    buttons = upd.effective_message.last_buttons()
    yes = next(b for b in buttons if b.endswith(":yes"))
    assert vault.get_note("Microservices").exists()

    # Un autre utilisateur (même s'il passait la whitelist) ne peut pas confirmer.
    q = FakeQuery(yes, FakeMessage())
    await notes.on_delete_callback(make_update(user_id=STRANGER, query=q), ctx)
    assert vault.find_notes("Microservices")

    q = FakeQuery(yes, FakeMessage())
    await notes.on_delete_callback(make_update(query=q), ctx)
    assert not vault.find_notes("Microservices")
    assert "Supprimé" in q.edits[-1][0]
    assert list((vault.root / ".trash").rglob("Microservices.md"))


async def test_rmdir_and_mkfol(ctx, vault) -> None:
    upd = make_update("/mkfol Archives/2026")
    await notes.mkfol(upd, ctx)
    assert (vault.root / "Archives" / "2026").is_dir()
    upd = make_update("/rmdir Archives")
    await notes.rmdir(upd, ctx)
    yes = next(b for b in upd.effective_message.last_buttons() if b.endswith(":yes"))
    await notes.on_delete_callback(make_update(query=FakeQuery(yes, FakeMessage())), ctx)
    assert not (vault.root / "Archives").exists()

    upd = make_update("/rmdir ../")
    await notes.rmdir(upd, ctx)
    assert upd.effective_message.last.startswith("❌")


async def test_search_hybrid_and_save(ctx, services, vault) -> None:
    upd = make_update("/search coroutines asyncio")
    await search.search(upd, ctx)
    text = upd.effective_message.last
    assert "Sources internes (Obsidian)" in text
    assert "Python asyncio" in text
    assert "Synthèse externe (Web)" in text and "[N1][W1]" in text
    assert "docs.python.org" in text
    query, ctx_notes, web = services.synthesizer.calls[0]
    assert query == "coroutines asyncio" and ctx_notes and web

    save = next(b for b in upd.effective_message.last_buttons() if b.endswith(":save"))
    msg = FakeMessage()
    await search.on_search_callback(make_update(query=FakeQuery(save, msg)), ctx)
    saved = vault.root / "Syna" / "Recherches" / "coroutines asyncio.md"
    body = saved.read_text(encoding="utf-8")
    assert "## Synthèse" in body and "[[Python asyncio]]" in body and "https://docs.python.org/asyncio" in body
    # Enregistrer deux fois ne doit pas écraser la première note.
    await search.on_search_callback(make_update(query=FakeQuery(save, FakeMessage())), ctx)
    assert len(list(saved.parent.glob("coroutines asyncio*.md"))) == 2


async def test_search_internal_only_skips_web(ctx, services) -> None:
    upd = make_update("/search -i microservices")
    await search.search(upd, ctx)
    assert "Synthèse externe" not in upd.effective_message.last
    assert services.synthesizer.calls == []


async def test_search_related_button(ctx) -> None:
    upd = make_update("/search architecture microservices API")
    await search.search(upd, ctx)
    related = next(b for b in upd.effective_message.last_buttons() if b.endswith(":related"))
    msg = FakeMessage()
    await search.on_search_callback(make_update(query=FakeQuery(related, msg)), ctx)
    assert "Microservices" in msg.last


async def test_synote_flow(ctx, vault) -> None:
    upd = make_update("/synote Notes réseau")
    await notes.synote(upd, ctx)
    assert "Liens proposés" in upd.effective_message.last
    buttons = upd.effective_message.last_buttons()
    toggles = [b for b in buttons if ":t" in b]
    apply_btn = next(b for b in buttons if b.endswith(":apply"))

    # Désélectionne le premier candidat puis applique.
    q = FakeQuery(toggles[0], FakeMessage())
    await notes.on_synote_callback(make_update(query=q), ctx)
    assert "⬜" in q.edits[-1][0]
    q = FakeQuery(apply_btn, FakeMessage())
    await notes.on_synote_callback(make_update(query=q), ctx)
    content = vault.get_note("Notes réseau").read_text(encoding="utf-8")
    assert "[[Python asyncio]]" in content
    assert "[[Microservices" not in content
    assert "lien(s) ajouté(s)" in q.edits[-1][0]


async def test_ls_and_status(ctx) -> None:
    upd = make_update("/ls Dev")
    await commands.ls(upd, ctx)
    assert "Python asyncio" in upd.effective_message.last
    upd = make_update("/status")
    await commands.status(upd, ctx)
    assert "Notes indexées : 4" in upd.effective_message.last
