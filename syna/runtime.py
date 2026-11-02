"""Conteneur des services partagés par les handlers (injecté dans ``bot_data``)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .config import Settings
from .core.git_sync import GitSync
from .core.pending import PendingStore
from .core.vault import Vault
from .services.search_external import Synthesizer, WebSearchProvider, build_provider, build_synthesizer
from .services.search_internal import Embedder, VaultIndex
from .services.watcher import VaultWatcher

log = logging.getLogger(__name__)


@dataclass
class Services:
    settings: Settings
    vault: Vault
    index: VaultIndex
    git: GitSync
    provider: WebSearchProvider | None = None
    synthesizer: Synthesizer | None = None
    watcher: VaultWatcher | None = None
    pending: PendingStore = field(default_factory=PendingStore)


def build_services(settings: Settings) -> Services:
    vault = Vault(settings.vault_path, trash_folder=settings.trash_folder)
    embedder = None
    if settings.embeddings_model:
        try:
            embedder = Embedder(settings.embeddings_model)
            log.info("Recherche sémantique activée (%s).", settings.embeddings_model)
        except ImportError:
            log.warning("EMBEDDINGS_MODEL défini mais sentence-transformers n'est pas installé.")
        except Exception:
            log.exception("Impossible de charger le modèle d'embeddings ; recherche lexicale seule.")
    index = VaultIndex(vault, embedder=embedder)
    git = GitSync(
        settings.vault_path,
        enabled=settings.git_sync,
        remote=settings.git_remote,
        branch=settings.git_branch,
        commit_message=settings.git_commit_message,
    )
    return Services(
        settings=settings,
        vault=vault,
        index=index,
        git=git,
        provider=build_provider(settings),
        synthesizer=build_synthesizer(settings),
        watcher=VaultWatcher(index) if settings.watch_vault else None,
    )
