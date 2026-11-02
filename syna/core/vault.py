"""Accès au vault Obsidian.

Toutes les lectures/écritures de fichiers passent par ce module : le reste du
code ne manipule jamais directement des chemins bruts. Chaque chemin fourni
par l'utilisateur est validé puis « sanctuarisé » à l'intérieur de la racine
du vault (protection contre le path traversal).
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

NOTE_SUFFIX = ".md"

# Caractères interdits dans un nom de fichier (Windows/macOS/Linux) ou qui
# cassent les wikilinks Obsidian ([[...]], #, ^, |).
_FORBIDDEN_CHARS = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f\x7f]')
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
MAX_NAME_LENGTH = 120


class VaultError(Exception):
    """Erreur fonctionnelle (message affichable à l'utilisateur)."""


class UnsafePathError(VaultError):
    """Chemin rejeté : tentative de sortie du vault ou zone protégée."""


class NoteNotFoundError(VaultError):
    pass


class NoteExistsError(VaultError):
    pass


def sanitize_name(name: str) -> str:
    """Transforme un titre libre en nom de fichier valide (sans extension)."""
    name = unicodedata.normalize("NFC", name)
    name = _FORBIDDEN_CHARS.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip()
    # Pas de nom commençant par un point (fichiers cachés) ni finissant par
    # un point/espace (invalide sous Windows).
    name = name.lstrip(".").rstrip(". ")
    if len(name) > MAX_NAME_LENGTH:
        name = name[:MAX_NAME_LENGTH].rstrip(". ")
    if not name:
        raise VaultError("Nom vide ou invalide après nettoyage.")
    if name.upper() in _WINDOWS_RESERVED:
        name = f"{name}_"
    return name


@dataclass(frozen=True)
class DirListing:
    path: str
    folders: list[str]
    notes: list[str]


class Vault:
    """Opérations CRUD sécurisées sur un vault Obsidian."""

    #: Dossiers jamais accessibles via le bot.
    PROTECTED_DIRS = frozenset({".git", ".obsidian"})

    def __init__(self, root: Path | str, *, trash_folder: str = ".trash") -> None:
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise VaultError(f"Le vault {self.root} n'existe pas.")
        self.trash_folder = trash_folder.strip("/") or ".trash"

    # ------------------------------------------------------------------ chemins
    def resolve(self, user_path: str, *, allow_root: bool = False) -> Path:
        """Valide un chemin relatif fourni par l'utilisateur et renvoie un
        chemin absolu garanti à l'intérieur du vault.

        Rejette : chemins absolus, composants ``..``, octets nuls, zones
        protégées (``.git``, ``.obsidian``, corbeille) et liens symboliques
        pointant hors du vault.
        """
        if user_path is None:
            raise UnsafePathError("Chemin manquant.")
        raw = user_path.strip().replace("\\", "/")
        if "\x00" in raw:
            raise UnsafePathError("Caractère nul interdit dans un chemin.")
        if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw) or raw.startswith("~"):
            raise UnsafePathError("Les chemins absolus sont interdits : utilisez un chemin relatif au vault.")
        parts = [p for p in PurePosixPath(raw).parts if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise UnsafePathError("Les composants '..' sont interdits.")
        if not parts and not allow_root:
            raise UnsafePathError("Chemin vide.")
        if parts and (parts[0] in self.PROTECTED_DIRS or parts[0] == self.trash_folder.split("/")[0]):
            raise UnsafePathError(f"Le dossier '{parts[0]}' est protégé.")

        target = self.root.joinpath(*parts).resolve()
        # Double vérification après résolution (liens symboliques inclus).
        if not target.is_relative_to(self.root) or os.path.commonpath([self.root, target]) != str(self.root):
            raise UnsafePathError("Tentative d'accès en dehors du vault.")
        return target

    def relpath(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def note_path(self, user_path: str, *, sanitize: bool = True) -> Path:
        """Chemin d'une note (ajoute ``.md``) avec nettoyage de chaque composant."""
        raw = user_path.strip().replace("\\", "/")
        if raw.lower().endswith(NOTE_SUFFIX):
            raw = raw[: -len(NOTE_SUFFIX)]
        parts = [p for p in raw.split("/") if p.strip()]
        if not parts:
            raise VaultError("Titre de note manquant.")
        if sanitize:
            # Vérifie d'abord le chemin brut (refuse '..' plutôt que de le « nettoyer »).
            self.resolve("/".join(parts))
            parts = [sanitize_name(p) for p in parts]
        parts[-1] = parts[-1] + NOTE_SUFFIX
        return self.resolve("/".join(parts))

    def folder_path(self, user_path: str) -> Path:
        raw = user_path.strip().replace("\\", "/")
        parts = [p for p in raw.split("/") if p.strip()]
        if not parts:
            raise VaultError("Nom de dossier manquant.")
        self.resolve("/".join(parts))
        return self.resolve("/".join(sanitize_name(p) for p in parts))

    # ------------------------------------------------------------------ notes
    def iter_notes(self):
        """Toutes les notes ``.md`` du vault (hors dossiers cachés/protégés)."""
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for fn in sorted(filenames):
                if fn.lower().endswith(NOTE_SUFFIX) and not fn.startswith("."):
                    yield Path(dirpath) / fn

    def find_notes(self, query: str) -> list[Path]:
        """Retrouve une note par chemin exact, sinon par nom (style Obsidian)."""
        query = query.strip()
        if not query:
            raise VaultError("Nom de note manquant.")
        try:
            exact = self.note_path(query, sanitize=False)
            if exact.is_file():
                return [exact]
        except UnsafePathError:
            raise
        except VaultError:
            pass
        name = PurePosixPath(query.replace("\\", "/")).name
        if name.lower().endswith(NOTE_SUFFIX):
            name = name[: -len(NOTE_SUFFIX)]
        wanted = name.casefold()
        exact_matches = [p for p in self.iter_notes() if p.stem.casefold() == wanted]
        if exact_matches:
            return exact_matches
        return [p for p in self.iter_notes() if wanted in p.stem.casefold()]

    def get_note(self, query: str) -> Path:
        """Renvoie une note unique ou lève une erreur explicite."""
        matches = self.find_notes(query)
        if not matches:
            raise NoteNotFoundError(f"Aucune note trouvée pour « {query} ».")
        if len(matches) > 1:
            listing = "\n".join(f"• {self.relpath(p)}" for p in matches[:10])
            raise VaultError(f"Plusieurs notes correspondent, précisez le chemin :\n{listing}")
        return matches[0]

    def read(self, path: Path) -> str:
        self._check_inside(path)
        return path.read_text(encoding="utf-8")

    def create_note(self, user_path: str, content: str = "", *, overwrite: bool = False) -> Path:
        path = self.note_path(user_path)
        if path.exists() and not overwrite:
            raise NoteExistsError(f"La note « {self.relpath(path)} » existe déjà.")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(path, content)
        return path

    def write(self, path: Path, content: str) -> None:
        self._check_inside(path)
        if path.suffix.lower() != NOTE_SUFFIX:
            raise VaultError("Seules les notes Markdown peuvent être modifiées.")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(path, content)

    def append(self, path: Path, text: str) -> None:
        current = self.read(path)
        sep = "" if not current or current.endswith("\n\n") else ("\n" if current.endswith("\n") else "\n\n")
        self.write(path, current + sep + text.rstrip("\n") + "\n")

    def delete_note(self, path: Path) -> Path:
        """Déplace la note dans la corbeille du vault (récupérable)."""
        self._check_inside(path)
        if not path.is_file():
            raise NoteNotFoundError("Note introuvable.")
        return self._to_trash(path)

    def backup(self, path: Path) -> Path:
        """Copie la version actuelle d'une note dans ``<corbeille>/versions``."""
        self._check_inside(path)
        if not path.is_file():
            raise NoteNotFoundError("Note introuvable.")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        rel = path.relative_to(self.root)
        dest = self.root / self.trash_folder / "versions" / rel.parent / f"{path.stem} ({stamp}){path.suffix}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        return dest

    # ---------------------------------------------------------------- dossiers
    def create_folder(self, user_path: str) -> Path:
        path = self.folder_path(user_path)
        if path.exists():
            raise VaultError(f"Le dossier « {self.relpath(path)} » existe déjà.")
        path.mkdir(parents=True)
        return path

    def get_folder(self, user_path: str) -> Path:
        path = self.resolve(user_path)
        if not path.is_dir():
            raise VaultError(f"Dossier introuvable : « {user_path} ».")
        return path

    def delete_folder(self, path: Path) -> Path:
        self._check_inside(path)
        if path == self.root:
            raise UnsafePathError("Impossible de supprimer la racine du vault.")
        if not path.is_dir():
            raise VaultError("Dossier introuvable.")
        return self._to_trash(path)

    def list_dir(self, user_path: str = "") -> DirListing:
        path = self.resolve(user_path, allow_root=True) if user_path.strip() else self.root
        if not path.is_dir():
            raise VaultError(f"Dossier introuvable : « {user_path} ».")
        folders, notes = [], []
        for child in sorted(path.iterdir(), key=lambda p: p.name.casefold()):
            if child.name.startswith("."):
                continue
            if child.is_dir():
                folders.append(child.name)
            elif child.suffix.lower() == NOTE_SUFFIX:
                notes.append(child.stem)
        rel = "" if path == self.root else self.relpath(path)
        return DirListing(rel, folders, notes)

    def count_items(self, path: Path) -> tuple[int, int]:
        """(nombre de notes, nombre de sous-dossiers) sous ``path``."""
        n_notes = n_dirs = 0
        for _, dirnames, filenames in os.walk(path):
            n_dirs += len(dirnames)
            n_notes += sum(1 for f in filenames if f.lower().endswith(NOTE_SUFFIX))
        return n_notes, n_dirs

    # ---------------------------------------------------------------- interne
    def _check_inside(self, path: Path) -> None:
        resolved = Path(path).resolve()
        if not resolved.is_relative_to(self.root):
            raise UnsafePathError("Tentative d'accès en dehors du vault.")
        rel_parts = resolved.relative_to(self.root).parts
        if rel_parts and rel_parts[0] in self.PROTECTED_DIRS:
            raise UnsafePathError(f"Le dossier '{rel_parts[0]}' est protégé.")

    def _to_trash(self, path: Path) -> Path:
        trash = self.root / self.trash_folder
        rel = path.relative_to(self.root)
        dest = trash / rel
        if dest.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            dest = dest.with_name(f"{dest.stem} ({stamp}){dest.suffix}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))
        return dest

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".syna-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
