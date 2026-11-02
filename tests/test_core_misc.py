from __future__ import annotations

import pytest

from syna.config import ConfigError, load_settings, parse_user_ids
from syna.core.messaging import _chunk_html, chunk_text
from syna.core.pending import PendingStore


def test_chunk_text_respects_limit_and_content() -> None:
    text = "\n\n".join(f"Paragraphe {i} " + "mot " * 200 for i in range(20))
    chunks = chunk_text(text, 4000)
    assert all(len(c) <= 4000 for c in chunks)
    assert len(chunks) > 1
    assert "".join(chunks).replace("\n", "").replace(" ", "") == text.replace("\n", "").replace(" ", "")


def test_chunk_text_hard_split() -> None:
    chunks = chunk_text("x" * 10000, 4000)
    assert [len(c) for c in chunks] == [4000, 4000, 2000]


def test_chunk_html_never_breaks_tags() -> None:
    lines = [f"<b>ligne {i}</b> " + "texte " * 50 for i in range(100)]
    lines.append("<i>" + "y" * 9000 + "</i>")
    for chunk in _chunk_html("\n".join(lines)):
        assert len(chunk) <= 4000
        assert chunk.count("<b>") == chunk.count("</b>")
        assert chunk.count("<i>") == chunk.count("</i>")


def test_parse_user_ids() -> None:
    assert parse_user_ids("1, 2;3") == {1, 2, 3}
    assert parse_user_ids("") == frozenset()
    with pytest.raises(ConfigError):
        parse_user_ids("abc")


def test_load_settings_requires_whitelist(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
    monkeypatch.delenv("ALLOWED_USER_IDS", raising=False)
    monkeypatch.delenv("ALLOWED_USER_ID", raising=False)
    with pytest.raises(ConfigError, match="ALLOWED_USER_IDS"):
        load_settings(tmp_path / "absent.env")
    monkeypatch.setenv("ALLOWED_USER_IDS", "42")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = load_settings(tmp_path / "absent.env")
    assert settings.allowed_user_ids == {42}
    assert settings.vault_path == tmp_path.resolve()
    assert settings.llm_enabled is False
    assert settings.llm_model == "claude-haiku-5-5"


def test_load_settings_rejects_missing_vault(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("ALLOWED_USER_IDS", "1")
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path / "nope"))
    with pytest.raises(ConfigError):
        load_settings(tmp_path / "absent.env")


def test_pending_store_owner_and_expiry(monkeypatch) -> None:
    store = PendingStore(ttl=10)
    token = store.add("del", 1, {"x": 1})
    assert store.get(token, 2) is None  # autre utilisateur
    assert store.get(token, 1, "syn") is None  # autre type d'action
    assert store.pop(token, 1, "del").payload == {"x": 1}
    assert store.get(token, 1) is None
    token = store.add("del", 1, {})
    store._items[token].created -= 11
    assert store.get(token, 1) is None
