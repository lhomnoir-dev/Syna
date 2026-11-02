"""Chargement de la configuration depuis l'environnement (.env).

Aucun secret n'est codé en dur : tout passe par des variables d'environnement,
chargées depuis un fichier ``.env`` (jamais versionné) via python-dotenv.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Configuration invalide ou incomplète."""


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "oui"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} doit être un entier (reçu : {raw!r}).") from exc


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} doit être un nombre (reçu : {raw!r}).") from exc


def parse_user_ids(raw: str | None) -> frozenset[int]:
    """Parse ``"123, 456"`` en ``{123, 456}``. Les valeurs invalides lèvent une erreur."""
    if not raw:
        return frozenset()
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError as exc:
            raise ConfigError(f"ID Telegram invalide dans la whitelist : {part!r}") from exc
    return frozenset(ids)


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    allowed_user_ids: frozenset[int]
    vault_path: Path

    # Notes
    inbox_folder: str = ""
    research_folder: str = "Syna/Recherches"
    trash_folder: str = ".trash"

    # Git
    git_sync: bool = False
    git_remote: str = "origin"
    git_branch: str | None = None
    git_commit_message: str = "Syna bot update"
    git_pull_interval: int = 300

    # Recherche externe
    search_provider: str = "tavily"  # tavily | exa | none
    tavily_api_key: str | None = None
    exa_api_key: str | None = None
    web_results: int = 4

    # LLM (synthèse)
    anthropic_api_key: str | None = None
    llm_enabled: bool = False
    llm_model: str = "claude-haiku-5-5"
    llm_effort: str = "low"
    llm_max_tokens: int = 4000

    # Recherche interne
    embeddings_model: str | None = None
    internal_results: int = 5
    context_chunks: int = 6
    watch_vault: bool = True

    # Synote
    synote_threshold: float = 0.30
    synote_max_links: int = 5

    # Telegram
    reply_unauthorized: bool = False
    max_inline_chars: int = 8000

    @property
    def web_search_enabled(self) -> bool:
        if self.search_provider == "tavily":
            return bool(self.tavily_api_key)
        if self.search_provider == "exa":
            return bool(self.exa_api_key)
        return False


def load_settings(env_file: str | os.PathLike[str] | None = None, *, require_token: bool = True) -> Settings:
    """Charge et valide la configuration."""
    load_dotenv(env_file, override=False)

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if require_token and not token:
        raise ConfigError("TELEGRAM_BOT_TOKEN est obligatoire (voir .env.example).")

    # ALLOWED_USER_IDS (liste) ou ALLOWED_USER_ID (compatibilité).
    allowed = parse_user_ids(os.getenv("ALLOWED_USER_IDS") or os.getenv("ALLOWED_USER_ID"))
    if require_token and not allowed:
        raise ConfigError(
            "ALLOWED_USER_IDS est obligatoire : sans whitelist, le bot serait public. Récupérez votre ID via @userinfobot."
        )

    vault_raw = os.getenv("OBSIDIAN_VAULT_PATH", "").strip()
    if not vault_raw:
        raise ConfigError("OBSIDIAN_VAULT_PATH est obligatoire (chemin absolu du vault).")
    vault_path = Path(vault_raw).expanduser().resolve()
    if not vault_path.is_dir():
        raise ConfigError(f"Le vault {vault_path} n'existe pas ou n'est pas un dossier.")

    anthropic_key = os.getenv("ANTHROPIC_API_KEY") or None
    provider = os.getenv("SEARCH_PROVIDER", "tavily").strip().lower()
    if provider not in {"tavily", "exa", "none"}:
        raise ConfigError("SEARCH_PROVIDER doit valoir 'tavily', 'exa' ou 'none'.")

    effort = os.getenv("SYNA_LLM_EFFORT", "low").strip().lower()
    if effort not in {"low", "medium", "high", "xhigh", "max"}:
        raise ConfigError("SYNA_LLM_EFFORT doit valoir low, medium, high, xhigh ou max.")

    threshold = _float("SYNOTE_THRESHOLD", 0.30)
    if not 0.0 < threshold <= 1.0:
        raise ConfigError("SYNOTE_THRESHOLD doit être compris dans ]0, 1].")

    return Settings(
        telegram_token=token,
        allowed_user_ids=allowed,
        vault_path=vault_path,
        inbox_folder=os.getenv("SYNA_INBOX_FOLDER", "").strip("/ "),
        research_folder=os.getenv("SYNA_RESEARCH_FOLDER", "Syna/Recherches").strip("/ "),
        trash_folder=os.getenv("SYNA_TRASH_FOLDER", ".trash").strip("/ "),
        git_sync=_bool(os.getenv("GIT_SYNC"), False),
        git_remote=os.getenv("GIT_REMOTE", "origin"),
        git_branch=os.getenv("GIT_BRANCH") or None,
        git_commit_message=os.getenv("GIT_COMMIT_MESSAGE", "Syna bot update"),
        git_pull_interval=_int("GIT_PULL_INTERVAL", 300),
        search_provider=provider,
        tavily_api_key=os.getenv("TAVILY_API_KEY") or None,
        exa_api_key=os.getenv("EXA_API_KEY") or None,
        web_results=_int("WEB_RESULTS", 4),
        anthropic_api_key=anthropic_key,
        llm_enabled=_bool(os.getenv("SYNA_LLM_ENABLED"), bool(anthropic_key)),
        llm_model=os.getenv("SYNA_LLM_MODEL", "claude-haiku-5-5"),
        llm_effort=effort,
        llm_max_tokens=_int("SYNA_LLM_MAX_TOKENS", 4000),
        embeddings_model=os.getenv("EMBEDDINGS_MODEL") or None,
        internal_results=_int("INTERNAL_RESULTS", 5),
        context_chunks=_int("CONTEXT_CHUNKS", 6),
        watch_vault=_bool(os.getenv("WATCH_VAULT"), True),
        synote_threshold=threshold,
        synote_max_links=_int("SYNOTE_MAX_LINKS", 5),
        reply_unauthorized=_bool(os.getenv("REPLY_UNAUTHORIZED"), False),
        max_inline_chars=_int("MAX_INLINE_CHARS", 8000),
    )
