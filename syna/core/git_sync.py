"""Synchronisation Git du vault, avec verrou pour éviter les conflits.

Cycle appliqué à chaque écriture du bot :

    verrou → git pull --rebase --autostash → écriture → git add/commit → git push

Le verrou (``asyncio.Lock``) sérialise toutes les écritures du bot : deux
commandes Telegram ne peuvent jamais modifier le vault en même temps. Le
``pull`` préalable intègre les modifications faites depuis Obsidian sur un
autre appareil, ce qui réduit fortement le risque de conflit de fusion.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


class SyncError(Exception):
    """Échec de synchronisation empêchant une écriture sûre."""


@dataclass
class GitResult:
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0


class GitSync:
    def __init__(
        self,
        repo: Path,
        *,
        enabled: bool = False,
        remote: str = "origin",
        branch: str | None = None,
        commit_message: str = "Syna bot update",
    ) -> None:
        self.repo = Path(repo)
        self.enabled = enabled
        self.remote = remote
        self.branch = branch
        self.commit_message = commit_message
        self.has_remote = False
        self._identity: list[str] = []
        self._lock = asyncio.Lock()

    # ----------------------------------------------------------------- setup
    async def setup(self) -> None:
        if not self.enabled:
            return
        res = await self._git("rev-parse", "--is-inside-work-tree")
        if not res.ok or res.out.strip() != "true":
            log.warning("GIT_SYNC activé mais %s n'est pas un dépôt Git : synchro désactivée.", self.repo)
            self.enabled = False
            return
        if not self.branch:
            res = await self._git("rev-parse", "--abbrev-ref", "HEAD")
            self.branch = res.out.strip() if res.ok and res.out.strip() != "HEAD" else None
        res = await self._git("remote")
        self.has_remote = self.remote in res.out.split()
        if not self.has_remote:
            log.warning("Remote '%s' absent : commits locaux uniquement.", self.remote)
        name = await self._git("config", "user.name")
        email = await self._git("config", "user.email")
        if not name.out.strip() or not email.out.strip():
            self._identity = ["-c", "user.name=Syna", "-c", "user.email=syna@localhost"]
        log.info("Synchro Git active (branche=%s, remote=%s).", self.branch, self.has_remote)

    # ------------------------------------------------------------ opérations
    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    @asynccontextmanager
    async def transaction(self, description: str):
        """Contexte d'écriture : verrou + pull avant, commit + push après.

        Renvoie (via ``yield``) une liste où sont ajoutés d'éventuels
        avertissements à remonter à l'utilisateur.
        """
        warnings: list[str] = []
        async with self._lock:
            if self.enabled:
                await self._pull()
            yield warnings
            if self.enabled:
                warning = await self._commit_and_push(description)
                if warning:
                    warnings.append(warning)

    async def sync(self) -> str:
        """Pull + commit/push des éventuelles modifications locales en attente."""
        if not self.enabled:
            return "Synchro Git désactivée (GIT_SYNC=false)."
        async with self._lock:
            await self._pull()
            warning = await self._commit_and_push("synchronisation manuelle")
        return warning or "✅ Vault synchronisé."

    async def pull_only(self) -> bool:
        """Pull périodique (ignoré si une écriture est en cours)."""
        if not self.enabled or not self.has_remote or self._lock.locked():
            return False
        async with self._lock:
            try:
                await self._pull()
                return True
            except SyncError as exc:
                log.warning("Pull périodique échoué : %s", exc)
                return False

    # --------------------------------------------------------------- interne
    async def _pull(self) -> None:
        if not self.has_remote:
            return
        args = ["pull", "--rebase", "--autostash", self.remote]
        if self.branch:
            args.append(self.branch)
        res = await self._git(*args)
        if not res.ok:
            # Ne jamais laisser le dépôt dans un état de rebase à moitié fait.
            await self._git("rebase", "--abort")
            raise SyncError(
                "git pull a échoué (conflit ou réseau). Aucune modification n'a été faite.\n"
                + (res.err.strip() or res.out.strip())[:500]
            )

    async def _commit_and_push(self, description: str) -> str | None:
        await self._git("add", "-A")
        status = await self._git("status", "--porcelain")
        if not status.out.strip():
            return None
        msg = f"{self.commit_message}: {description}"[:200]
        res = await self._git(*self._identity, "commit", "-m", msg)
        if not res.ok:
            return f"⚠️ Commit Git échoué : {(res.err or res.out).strip()[:300]}"
        if not self.has_remote:
            return None
        push_args = ["push", self.remote]
        if self.branch:
            push_args.append(f"HEAD:{self.branch}")
        res = await self._git(*push_args)
        if res.ok:
            return None
        # Le remote a bougé entre-temps : on réintègre puis on retente une fois.
        try:
            await self._pull()
        except SyncError as exc:
            return f"⚠️ Modification commitée localement mais non poussée : {exc}"
        res = await self._git(*push_args)
        if not res.ok:
            return f"⚠️ Modification commitée localement mais git push a échoué : {res.err.strip()[:300]}"
        return None

    async def _git(self, *args: str) -> GitResult:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=self.repo,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        out, err = await proc.communicate()
        result = GitResult(proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace"))
        if not result.ok:
            log.debug("git %s → %s : %s", " ".join(args), result.code, result.err.strip())
        return result
