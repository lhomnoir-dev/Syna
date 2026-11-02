"""Écoute des modifications du vault (watchdog) pour garder l'index frais.

Dès qu'un fichier ``.md`` est créé, modifié, déplacé ou supprimé (par le bot,
par Obsidian ou par un ``git pull``), l'index est marqué « sale » ; il est
ré-indexé de façon incrémentale, en arrière-plan, après un court délai
(regroupement des rafales d'événements).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from .search_internal import VaultIndex

log = logging.getLogger(__name__)


class VaultWatcher:
    def __init__(self, index: VaultIndex, *, debounce: float = 1.5) -> None:
        self.index = index
        self.debounce = debounce
        self._observer = None
        self._timer: threading.Timer | None = None
        self._timer_lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._observer is not None

    def start(self) -> bool:
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            log.info("watchdog non installé : l'index sera rafraîchi à chaque recherche.")
            return False

        watcher = self

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                paths = [getattr(event, "src_path", ""), getattr(event, "dest_path", "")]
                if any(_is_note(p) for p in paths if p):
                    watcher._schedule()

        observer = Observer()
        observer.schedule(_Handler(), str(self.index.vault.root), recursive=True)
        observer.daemon = True
        observer.start()
        self._observer = observer
        log.info("Surveillance du vault activée (%s).", self.index.vault.root)
        return True

    def stop(self) -> None:
        with self._timer_lock:
            if self._timer:
                self._timer.cancel()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

    def _schedule(self) -> None:
        self.index.mark_dirty()
        with self._timer_lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce, self._run)
            self._timer.daemon = True
            self._timer.start()

    def _run(self) -> None:
        try:
            self.index.refresh()
        except Exception:  # pragma: no cover - journalisation seulement
            log.exception("Échec du rafraîchissement de l'index")


def _is_note(path: str | bytes) -> bool:
    if isinstance(path, bytes):
        path = path.decode(errors="ignore")
    p = Path(path)
    if any(part in {".git", ".obsidian"} for part in p.parts):
        return False
    return p.suffix.lower() == ".md"
