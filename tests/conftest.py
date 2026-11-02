from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from syna.core.vault import Vault
from syna.services.search_internal import VaultIndex


def write(root: Path, rel: str, content: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")
    return path


@pytest.fixture
def vault_dir(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    write(
        root,
        "Dev/Python asyncio.md",
        """
        ---
        tags: [python, dev]
        ---
        # Python asyncio

        La boucle d'événements asyncio permet la programmation asynchrone en Python.
        Les coroutines sont déclarées avec async def et attendues avec await.

        ## Tâches
        asyncio.create_task planifie une coroutine en parallèle.
        """,
    )
    write(
        root,
        "Dev/Microservices.md",
        """
        # Architecture microservices

        Une architecture microservices découpe une application en services indépendants
        qui communiquent par API. Chaque service possède sa base de données.
        #architecture
        """,
    )
    write(
        root,
        "Perso/Recette crêpes.md",
        """
        Farine, œufs, lait et une pincée de sel. Laisser reposer la pâte une heure.
        #cuisine
        """,
    )
    write(
        root,
        "Dev/Notes réseau.md",
        """
        Les microservices communiquent souvent via HTTP ou gRPC. Voir aussi Python asyncio
        pour écrire des clients réseau asynchrones.
        """,
    )
    (root / ".obsidian").mkdir()
    (root / ".obsidian" / "app.json").write_text("{}")
    return root


@pytest.fixture
def vault(vault_dir: Path) -> Vault:
    return Vault(vault_dir)


@pytest.fixture
def index(vault: Vault) -> VaultIndex:
    idx = VaultIndex(vault)
    idx.refresh(force=True)
    return idx
