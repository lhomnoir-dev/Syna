"""/synote : connexion automatique des notes par wikilinks.

Pour limiter le bruit sémantique (liens non pertinents qui polluent le graphe
Obsidian) :

* un **seuil de similarité strict** (cosinus) filtre les candidats ;
* les notes déjà liées sont ignorées ;
* une mention littérale du titre d'une autre note est un signal fort ;
* rien n'est écrit sans **validation explicite** de l'utilisateur via les
  boutons Telegram (voir ``handlers/notes.py``).

Lors de l'application, la première mention littérale du titre est
transformée en ``[[Titre]]`` dans le texte ; sinon le lien est ajouté dans
une section « ## Notes liées » en fin de note.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..core.vault import Vault
from .markdown import extract_wikilinks, split_frontmatter, strip_accents
from .search_internal import VaultIndex

RELATED_HEADING = "## Notes liées"
MENTION_SCORE = 0.9
MIN_TITLE_LEN = 4

# Segments à ne jamais modifier : liens existants, liens Markdown, code inline, URLs.
_PROTECTED_SEGMENT = re.compile(r"(\[\[.*?\]\]|\[[^\]]*\]\([^)]*\)|`[^`]*`|https?://\S+)")


@dataclass
class LinkCandidate:
    rel: str
    title: str
    score: float
    mentioned: bool

    @property
    def reason(self) -> str:
        return "mentionnée dans le texte" if self.mentioned else f"similarité {self.score:.2f}"


def _fold(text: str) -> str:
    return strip_accents(text.casefold())


def _mention_regex(title: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w])({re.escape(title)})(?![\w])", re.IGNORECASE)


def find_link_candidates(
    vault: Vault,
    index: VaultIndex,
    note: Path,
    *,
    threshold: float,
    max_links: int = 5,
) -> list[LinkCandidate]:
    index.refresh()
    rel = vault.relpath(note)
    content = vault.read(note)
    _, body = split_frontmatter(content)
    already = {_fold(PurePosixPath(link).name) for link in extract_wikilinks(content)}
    titles = index.titles()

    def is_linked(other_rel: str) -> bool:
        return _fold(titles[other_rel]) in already or _fold(other_rel[:-3]) in already

    candidates: dict[str, LinkCandidate] = {}
    # 1) Mentions littérales des titres d'autres notes.
    searchable = _PROTECTED_SEGMENT.sub(" ", body)
    for other_rel, title in titles.items():
        if other_rel == rel or len(title) < MIN_TITLE_LEN or is_linked(other_rel):
            continue
        if _mention_regex(title).search(searchable):
            candidates[other_rel] = LinkCandidate(other_rel, title, MENTION_SCORE, True)

    # 2) Similarité de contenu (TF-IDF / embeddings) au-dessus du seuil.
    title_text = " ".join([note.stem] * 3)
    for other_rel, score in index.similar_notes(f"{title_text}\n{body}", exclude={rel}, k=max_links * 4):
        if score < threshold or other_rel in candidates or is_linked(other_rel):
            continue
        candidates[other_rel] = LinkCandidate(other_rel, titles[other_rel], score, False)

    ranked = sorted(candidates.values(), key=lambda c: (not c.mentioned, -c.score, c.title.casefold()))
    return ranked[:max_links]


def link_target(rel: str, titles: dict[str, str]) -> str:
    """Cible de wikilink : le titre seul s'il est unique, sinon le chemin."""
    title = titles.get(rel, PurePosixPath(rel).stem)
    counts = Counter(_fold(t) for t in titles.values())
    if counts[_fold(title)] > 1:
        return rel[:-3] if rel.endswith(".md") else rel
    return title


def apply_links(content: str, links: list[tuple[str, str]]) -> str:
    """Insère les liens dans ``content``.

    ``links`` : liste de (titre, cible). La première mention libre du titre
    est convertie en wikilink ; à défaut, le lien est ajouté à la section
    « Notes liées ».
    """
    _, body = split_frontmatter(content)
    head = content[: len(content) - len(body)]

    to_append: list[str] = []
    for title, target in links:
        new_body = _link_first_mention(body, title, target)
        if new_body is None:
            to_append.append(target)
        else:
            body = new_body

    if to_append:
        body = _append_related(body, to_append)
    return head + body


def _wikilink(title: str, target: str, shown: str) -> str:
    if target == title and shown == title:
        return f"[[{title}]]"
    return f"[[{target}|{shown}]]"


def _link_first_mention(body: str, title: str, target: str) -> str | None:
    if len(title) < MIN_TITLE_LEN:
        return None
    rx = _mention_regex(title)
    lines = body.split("\n")
    in_code = False
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code = not in_code
            continue
        if in_code or stripped.startswith("#") or stripped == RELATED_HEADING:
            continue
        segments = _PROTECTED_SEGMENT.split(line)
        for j, seg in enumerate(segments):
            if j % 2 == 1:  # segment protégé
                continue
            m = rx.search(seg)
            if m:
                shown = m.group(1)
                segments[j] = seg[: m.start()] + _wikilink(title, target, shown) + seg[m.end() :]
                lines[i] = "".join(segments)
                return "\n".join(lines)
    return None


def _append_related(body: str, targets: list[str]) -> str:
    bullets = [f"- [[{t}]]" for t in targets]
    lines = body.rstrip("\n").split("\n")
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == RELATED_HEADING)
    except StopIteration:
        sep = "\n\n" if body.strip() else ""
        return body.rstrip("\n") + sep + RELATED_HEADING + "\n" + "\n".join(bullets) + "\n"
    end = start + 1
    while end < len(lines) and not lines[end].startswith("#"):
        end += 1
    # Insère après la dernière puce de la section (en ignorant les lignes vides finales).
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines[insert_at:insert_at] = bullets
    return "\n".join(lines) + "\n"
