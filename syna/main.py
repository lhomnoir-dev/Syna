"""Point d'entrée : initialisation des services et lancement du bot (polling)."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys

from telegram import BotCommand, Update
from telegram.ext import Application, ContextTypes

from .config import ConfigError, Settings, load_settings
from .handlers import register_handlers
from .handlers.commands import BOT_COMMANDS
from .runtime import Services, build_services

log = logging.getLogger("syna")


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Erreur non gérée", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message is not None:
        with contextlib.suppress(Exception):
            await update.effective_message.reply_text("❌ Erreur interne, consultez les logs du bot.")


async def _periodic_pull(services: Services, interval: int) -> None:
    """Récupère régulièrement les modifications faites depuis Obsidian."""
    while True:
        await asyncio.sleep(interval)
        if await services.git.pull_only():
            services.index.mark_dirty()


def build_application(settings: Settings, services: Services | None = None) -> Application:
    services = services or build_services(settings)

    async def post_init(app: Application) -> None:
        await services.git.setup()
        stats_before = await asyncio.to_thread(services.index.refresh, True)
        log.info("Index initial : %d note(s).", stats_before[0])
        if services.watcher is not None:
            services.watcher.start()
        if services.git.enabled and services.git.has_remote and settings.git_pull_interval > 0:
            app.bot_data["pull_task"] = asyncio.create_task(_periodic_pull(services, settings.git_pull_interval))
        await app.bot.set_my_commands([BotCommand(name, desc) for name, desc in BOT_COMMANDS])

    async def post_shutdown(app: Application) -> None:
        task = app.bot_data.get("pull_task")
        if task:
            task.cancel()
        if services.watcher is not None:
            services.watcher.stop()

    app = (
        Application.builder()
        .token(settings.telegram_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .concurrent_updates(True)
        .build()
    )
    app.bot_data["services"] = services
    register_handlers(app)
    app.add_error_handler(_error_handler)
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="syna", description="Syna — bot Telegram pour Obsidian")
    parser.add_argument("--env", default=None, help="Chemin du fichier .env (défaut : ./.env)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Le token du bot apparaît dans les URLs des requêtes : on ne journalise pas httpx.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        settings = load_settings(args.env)
    except ConfigError as exc:
        print(f"Configuration invalide : {exc}", file=sys.stderr)
        return 2

    app = build_application(settings)
    log.info("Syna démarre (vault : %s, %d utilisateur(s) autorisé(s)).", settings.vault_path, len(settings.allowed_user_ids))
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
