from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from syna.core.git_sync import GitSync, SyncError


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def repos(tmp_path: Path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(remote), str(seed)], check=True, capture_output=True)
    git(seed, "checkout", "-b", "main")
    (seed / "README.md").write_text("vault\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-m", "init")
    git(seed, "push", "-u", "origin", "main")
    bot = tmp_path / "bot"
    subprocess.run(["git", "clone", str(remote), str(bot)], check=True, capture_output=True)
    return remote, seed, bot


async def test_transaction_pulls_commits_and_pushes(repos) -> None:
    _remote, seed, bot = repos
    sync = GitSync(bot, enabled=True)
    await sync.setup()
    assert sync.has_remote and sync.branch == "main"

    # Modification faite « depuis Obsidian » sur un autre appareil.
    (seed / "pc.md").write_text("depuis le PC\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-m", "pc")
    git(seed, "push")

    async with sync.transaction("création de bot.md") as warnings:
        assert (bot / "pc.md").exists()  # pull effectué AVANT l'écriture
        (bot / "bot.md").write_text("depuis le bot\n")
    assert warnings == []

    git(seed, "pull")
    assert (seed / "bot.md").read_text() == "depuis le bot\n"
    assert "Syna bot update: création de bot.md" in git(seed, "log", "-1", "--format=%s")


async def test_pull_conflict_aborts_without_writing(repos) -> None:
    _remote, seed, bot = repos
    sync = GitSync(bot, enabled=True)
    await sync.setup()
    # Commit local non poussé en conflit avec le remote.
    (bot / "README.md").write_text("version bot\n")
    git(bot, "commit", "-am", "local")
    (seed / "README.md").write_text("version pc\n")
    git(seed, "commit", "-am", "pc")
    git(seed, "push")

    with pytest.raises(SyncError):
        async with sync.transaction("x"):
            pytest.fail("l'écriture ne doit pas avoir lieu")
    status = git(bot, "status")
    assert "rebase" not in status.lower()


async def test_disabled_sync_is_noop(tmp_path: Path) -> None:
    sync = GitSync(tmp_path, enabled=True)
    await sync.setup()  # pas un dépôt git → désactivé
    assert sync.enabled is False
    async with sync.transaction("x") as warnings:
        (tmp_path / "a.md").write_text("a")
    assert warnings == []
    assert "désactivée" in await sync.sync()


async def test_local_repo_without_remote_commits(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True, capture_output=True)
    sync = GitSync(tmp_path, enabled=True)
    await sync.setup()
    assert not sync.has_remote
    async with sync.transaction("note"):
        (tmp_path / "n.md").write_text("n")
    assert "note" in git(tmp_path, "log", "-1", "--format=%s")
