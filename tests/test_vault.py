from __future__ import annotations

import os

import pytest

from syna.core.vault import NoteExistsError, NoteNotFoundError, UnsafePathError, Vault, VaultError, sanitize_name


@pytest.mark.parametrize(
    "bad",
    [
        "../secret",
        "../../etc/passwd",
        "Dev/../../etc",
        "/etc/passwd",
        "~/notes",
        "C:/Windows",
        "..\\..\\etc",
        "a/\x00b",
        ".git/config",
        ".obsidian/app.json",
        ".trash/old",
    ],
)
def test_resolve_rejects_unsafe_paths(vault: Vault, bad: str) -> None:
    with pytest.raises(UnsafePathError):
        vault.resolve(bad)


def test_resolve_stays_inside_vault(vault: Vault) -> None:
    path = vault.resolve("Dev/Python asyncio.md")
    assert path.is_relative_to(vault.root)
    assert os.path.commonpath([vault.root, path]) == str(vault.root)


def test_symlink_escape_is_rejected(vault: Vault, tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (vault.root / "link").symlink_to(outside)
    with pytest.raises(UnsafePathError):
        vault.resolve("link/file.md")


def test_sanitize_name() -> None:
    assert sanitize_name('  Mon: titre / "spécial" ?  ') == "Mon titre spécial"
    assert sanitize_name("...caché") == "caché"
    assert sanitize_name("Lien [[x]] #tag|y") == "Lien x tag y"
    assert sanitize_name("CON") == "CON_"
    assert len(sanitize_name("a" * 500)) == 120
    with pytest.raises(VaultError):
        sanitize_name("///")


def test_create_and_read_note(vault: Vault) -> None:
    path = vault.create_note("Projets/Idée: géniale", "Contenu")
    assert path == vault.root / "Projets" / "Idée géniale.md"
    assert vault.read(path) == "Contenu"
    with pytest.raises(NoteExistsError):
        vault.create_note("Projets/Idée géniale", "x")


def test_create_note_refuses_traversal(vault: Vault) -> None:
    with pytest.raises(UnsafePathError):
        vault.create_note("../evil", "x")
    assert not (vault.root.parent / "evil.md").exists()


def test_find_notes_by_name_and_path(vault: Vault) -> None:
    assert vault.get_note("Dev/Microservices").name == "Microservices.md"
    assert vault.get_note("microservices").name == "Microservices.md"
    assert vault.get_note("Recette crêpes.md").name == "Recette crêpes.md"
    with pytest.raises(NoteNotFoundError):
        vault.get_note("inexistante")


def test_get_note_ambiguous(vault: Vault) -> None:
    vault.create_note("A/Doublon", "1")
    vault.create_note("B/Doublon", "2")
    with pytest.raises(VaultError, match="Plusieurs notes"):
        vault.get_note("Doublon")
    assert vault.get_note("A/Doublon").parent.name == "A"


def test_delete_moves_to_trash(vault: Vault) -> None:
    path = vault.get_note("Microservices")
    dest = vault.delete_note(path)
    assert not path.exists()
    assert dest.exists() and ".trash" in dest.parts
    # Une seconde suppression du même nom ne doit pas écraser la première.
    vault.create_note("Dev/Microservices", "v2")
    dest2 = vault.delete_note(vault.get_note("Dev/Microservices"))
    assert dest2 != dest and dest.exists() and dest2.exists()


def test_delete_folder_and_root_protection(vault: Vault) -> None:
    folder = vault.get_folder("Perso")
    vault.delete_folder(folder)
    assert not folder.exists()
    with pytest.raises(UnsafePathError):
        vault.delete_folder(vault.root)


def test_iter_notes_skips_hidden(vault: Vault) -> None:
    vault.delete_note(vault.get_note("Microservices"))
    rels = {vault.relpath(p) for p in vault.iter_notes()}
    assert "Dev/Microservices.md" not in rels
    assert not any(r.startswith(".") for r in rels)


def test_list_dir(vault: Vault) -> None:
    listing = vault.list_dir("")
    assert listing.folders == ["Dev", "Perso"]
    dev = vault.list_dir("Dev")
    assert "Python asyncio" in dev.notes


def test_append_and_backup(vault: Vault) -> None:
    path = vault.get_note("Recette crêpes")
    backup = vault.backup(path)
    vault.append(path, "Ajout")
    assert vault.read(path).endswith("Ajout\n")
    assert "Ajout" not in backup.read_text(encoding="utf-8")
    assert "versions" in backup.parts


def test_write_rejects_protected(vault: Vault) -> None:
    with pytest.raises(UnsafePathError):
        vault.write(vault.root / ".obsidian" / "x.md", "x")
    with pytest.raises(UnsafePathError):
        vault.write(vault.root.parent / "x.md", "x")
