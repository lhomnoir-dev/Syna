from __future__ import annotations

import os
import time

import pytest

from syna.services.markdown import chunk_markdown, extract_tags, extract_wikilinks, make_excerpt, tokenize


def test_tokenize_strips_accents_and_stopwords() -> None:
    assert tokenize("L'événement de la Boucle") == ["evenement", "boucle"]


def test_extract_tags_and_links() -> None:
    content = "---\ntags:\n  - a\n  - b/c\n---\nTexte #python et [[Note A|alias]] [[Note B#titre]]\n```\n#pasuntag\n```"
    assert extract_tags(content) == ["a", "b/c", "python"]
    assert extract_wikilinks(content) == ["Note A", "Note B"]


def test_chunk_markdown_by_headings() -> None:
    chunks = chunk_markdown("intro\n\n# A\ntexte a\n## B\ntexte b\n```\n# pas un titre\n```")
    assert [h for h, _ in chunks] == ["", "A", "B"]
    assert "# pas un titre" in chunks[2][1]


def test_make_excerpt_centers_on_term() -> None:
    text = "x " * 300 + "asyncio est génial " + "y " * 300
    excerpt = make_excerpt(text, {"asyncio"}, width=80)
    assert "asyncio" in excerpt and excerpt.startswith("…")


def test_lexical_search_ranks_relevant_note_first(index) -> None:
    hits = index.search("coroutines asyncio")
    assert hits[0].rel == "Dev/Python asyncio.md"
    assert "asyncio" in hits[0].excerpt.lower() or "coroutines" in hits[0].excerpt.lower()


def test_search_accent_insensitive(index) -> None:
    assert index.search("crepes")[0].title == "Recette crêpes"


def test_tag_filter(index) -> None:
    hits = index.search("#cuisine")
    assert [h.title for h in hits] == ["Recette crêpes"]
    hits = index.search("#python boucle")
    assert [h.title for h in hits] == ["Python asyncio"]


def test_regex_search(index) -> None:
    hits = index.search("/gRPC|HTTP/")
    assert hits[0].title == "Notes réseau"
    with pytest.raises(ValueError):
        index.search("/([/")


def test_incremental_refresh(index, vault) -> None:
    vault.create_note("Nouveau/Kubernetes", "Kubernetes orchestre des conteneurs.")
    index.mark_dirty()
    assert index.search("kubernetes")[0].title == "Kubernetes"
    path = vault.get_note("Kubernetes")
    path.write_text("Docker uniquement maintenant.", encoding="utf-8")
    future = time.time() + 5
    os.utime(path, (future, future))
    index.mark_dirty()
    assert all(h.title != "Kubernetes" for h in index.search("orchestre conteneurs"))
    assert index.search("docker")[0].title == "Kubernetes"
    vault.delete_note(path)
    index.mark_dirty()
    index.refresh()
    assert "Nouveau/Kubernetes.md" not in index.notes


def test_df_counters_consistent_after_removal(index, vault) -> None:
    before = dict(index._chunk_df)
    vault.create_note("Tmp", "zorglub zorglub")
    index.refresh(force=True)
    assert index._chunk_df["zorglub"] == 1
    vault.delete_note(vault.get_note("Tmp"))
    index.mark_dirty()
    index.refresh()
    assert "zorglub" not in index._chunk_df
    assert dict(index._chunk_df) == before


def test_top_chunks_and_similar_notes(index) -> None:
    chunks = index.top_chunks("microservices API", k=3)
    assert chunks and chunks[0].text
    similar = index.similar_notes("services indépendants communiquent par API microservices")
    assert similar[0][0] == "Dev/Microservices.md"


def test_empty_query(index) -> None:
    assert index.search("   ") == []
