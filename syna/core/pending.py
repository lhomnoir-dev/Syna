"""Stockage en mémoire des actions en attente de confirmation (boutons inline).

Les ``callback_data`` Telegram sont limitées à 64 octets : on n'y place
qu'un jeton court qui référence l'action stockée ici, avec son propriétaire
(seul l'utilisateur qui a lancé l'action peut la confirmer) et une durée de
validité.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PendingAction:
    kind: str
    user_id: int
    payload: dict[str, Any]
    created: float = field(default_factory=time.monotonic)


class PendingStore:
    def __init__(self, ttl: float = 30 * 60, max_items: int = 500) -> None:
        self.ttl = ttl
        self.max_items = max_items
        self._items: dict[str, PendingAction] = {}

    def add(self, kind: str, user_id: int, payload: dict[str, Any]) -> str:
        self._purge()
        token = secrets.token_urlsafe(6)
        self._items[token] = PendingAction(kind, user_id, payload)
        return token

    def get(self, token: str, user_id: int, kind: str | None = None) -> PendingAction | None:
        self._purge()
        action = self._items.get(token)
        if action is None or action.user_id != user_id:
            return None
        if kind is not None and action.kind != kind:
            return None
        return action

    def pop(self, token: str, user_id: int, kind: str | None = None) -> PendingAction | None:
        action = self.get(token, user_id, kind)
        if action is not None:
            self._items.pop(token, None)
        return action

    def __len__(self) -> int:
        return len(self._items)

    def _purge(self) -> None:
        now = time.monotonic()
        expired = [k for k, v in self._items.items() if now - v.created > self.ttl]
        for k in expired:
            del self._items[k]
        # Limite dure : on retire les plus anciennes.
        while len(self._items) > self.max_items:
            oldest = min(self._items, key=lambda k: self._items[k].created)
            del self._items[oldest]
