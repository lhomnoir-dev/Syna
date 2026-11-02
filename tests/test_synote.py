from __future__ import annotations

from syna.services.synote_ai import RELATED_HEADING, apply_links, find_link_candidates, link_target


def test_candidates_include_mentions_and_similar(vault, index) -> None:
    note = vault.get_note("Notes réseau")
    cands = find_link_candidates(vault, index, note, threshold=0.1, max_links=5)
    titles = [c.title for c in cands]
    # Les mentions littérales (insensibles à la casse) passent en premier.
    assert titles[:2] == ["Microservices", "Python asyncio"]
    assert cands[0].mentioned and cands[1].mentioned
    assert "Recette crêpes" not in titles


def test_strict_threshold_filters_noise(vault, index) -> None:
    note = vault.get_note("Recette crêpes")
    assert find_link_candidates(vault, index, note, threshold=0.3) == []


def test_already_linked_notes_are_skipped(vault, index) -> None:
    note = vault.get_note("Notes réseau")
    vault.append(note, "Lié : [[Python asyncio]]")
    index.mark_dirty()
    cands = find_link_candidates(vault, index, note, threshold=0.1)
    assert "Python asyncio" not in [c.title for c in cands]


def test_apply_links_inline_mention() -> None:
    content = "---\ntags: [x]\n---\n# Titre\nJ'utilise python asyncio ici. `Python asyncio` en code.\n"
    out = apply_links(content, [("Python asyncio", "Python asyncio")])
    assert "[[Python asyncio|python asyncio]] ici" in out
    assert "`Python asyncio`" in out
    assert out.startswith("---\ntags: [x]\n---\n")


def test_apply_links_does_not_touch_code_links_or_headings() -> None:
    content = "# Python asyncio\n```\nPython asyncio\n```\nVoir [[Autre]] et https://x.org/Python asyncio\n"
    out = apply_links(content, [("Python asyncio", "Python asyncio")])
    assert out.count("[[Python asyncio]]") == 1
    assert out.rstrip().endswith(f"{RELATED_HEADING}\n- [[Python asyncio]]")


def test_apply_links_appends_to_existing_section() -> None:
    content = f"Texte\n\n{RELATED_HEADING}\n- [[A]]\n\n## Autre\nfin\n"
    out = apply_links(content, [("Zeta", "Zeta")])
    assert f"{RELATED_HEADING}\n- [[A]]\n- [[Zeta]]\n\n## Autre" in out


def test_link_target_disambiguates() -> None:
    titles = {"A/Doublon.md": "Doublon", "B/Doublon.md": "Doublon", "C/Unique.md": "Unique"}
    assert link_target("A/Doublon.md", titles) == "A/Doublon"
    assert link_target("C/Unique.md", titles) == "Unique"
