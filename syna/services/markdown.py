"""Outils d'analyse Markdown/Obsidian : frontmatter, tags, wikilinks, chunks,
tokenisation."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
WIKILINK_RE = re.compile(r"\[\[([^\]\|#\^]+)(?:[#\^][^\]\|]*)?(?:\|[^\]]*)?\]\]")
TAG_RE = re.compile(r"(?<![\w/#&])#([\w][\w/\-]*)", re.UNICODE)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
WORD_RE = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    """
    a ai aie ait as au aux avec avoir c ca ce ceci cela celle celles celui ces cet cette
    ceux chaque comme comment d dans de des du donc elle elles en encore est et etaient
    etait ete etre eu fait faire il ils j je l la le les leur leurs lui m ma mais me meme
    mes moi mon n ne ni nos notre nous on ont ou par pas peu peut plus pour pourquoi qu
    quand que quel quelle quelles quels qui s sa sans se ses si son sont sous sur t ta te
    tes toi ton tous tout toute toutes tres tu un une vos votre vous y deja aussi alors
    apres avant bien cest cet entre ici la lors meme non oui puis quoi sera seront soit
    the an and are be been but by for from has have he her his i if in into is it its
    me my no not of on or our she so that the their them then there these they this to
    was we were what when where which who why will with you your
    """.split()
)


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def tokenize(text: str) -> list[str]:
    """Minuscules, sans accents, sans mots vides, longueur ≥ 2."""
    text = strip_accents(text.lower())
    return [w for w in WORD_RE.findall(text) if len(w) >= 2 and w not in STOPWORDS]


def split_frontmatter(content: str) -> tuple[str, str]:
    """Renvoie (frontmatter brut, corps)."""
    m = FRONTMATTER_RE.match(content)
    if not m:
        return "", content
    return m.group(1), content[m.end() :]


def frontmatter_tags(frontmatter: str) -> list[str]:
    tags: list[str] = []
    lines = frontmatter.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^\s*tags?\s*:\s*(.*)$", line)
        if not m:
            continue
        value = m.group(1).strip()
        if value.startswith("["):
            tags += [t.strip(" '\"#") for t in value.strip("[]").split(",")]
        elif value:
            tags += [t.strip(" '\"#") for t in re.split(r"[,\s]+", value)]
        else:
            for nxt in lines[i + 1 :]:
                item = re.match(r"^\s*-\s*(.+)$", nxt)
                if not item:
                    break
                tags.append(item.group(1).strip(" '\"#"))
    return [t for t in tags if t]


def extract_tags(content: str) -> list[str]:
    fm, body = split_frontmatter(content)
    body = CODE_FENCE_RE.sub(" ", body)
    found = frontmatter_tags(fm) + TAG_RE.findall(body)
    seen: dict[str, None] = {}
    for t in found:
        seen.setdefault(t.lower(), None)
    return list(seen)


def extract_wikilinks(content: str) -> list[str]:
    seen: dict[str, None] = {}
    for target in WIKILINK_RE.findall(content):
        seen.setdefault(target.strip(), None)
    return list(seen)


@dataclass
class Chunk:
    heading: str
    text: str
    terms: dict[str, int] = field(default_factory=dict)
    length: int = 0


def chunk_markdown(body: str, max_chars: int = 900) -> list[tuple[str, str]]:
    """Découpe le corps d'une note en (titre de section, texte), en respectant
    les titres Markdown puis les paragraphes."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    in_code = False
    for line in body.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        m = None if in_code else HEADING_RE.match(line)
        if m:
            sections.append((m.group(2).strip(), []))
        else:
            sections[-1][1].append(line)

    chunks: list[tuple[str, str]] = []
    for heading, lines in sections:
        text = "\n".join(lines).strip()
        if not text and not heading:
            continue
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()] or [""]
        buf = ""
        for para in paragraphs:
            if buf and len(buf) + len(para) + 2 > max_chars:
                chunks.append((heading, buf))
                buf = ""
            while len(para) > max_chars:
                chunks.append((heading, para[:max_chars]))
                para = para[max_chars:]
            buf = f"{buf}\n\n{para}" if buf else para
        if buf or heading:
            chunks.append((heading, buf))
    return chunks


def make_excerpt(text: str, terms: set[str], width: int = 280) -> str:
    """Extrait centré sur la première occurrence d'un terme de la requête."""
    flat = re.sub(r"\s+", " ", text).strip()
    if len(flat) <= width:
        return flat
    folded = strip_accents(flat.lower())
    pos = -1
    for t in terms:
        m = re.search(rf"\b{re.escape(t)}", folded)
        if m and (pos == -1 or m.start() < pos):
            pos = m.start()
    if pos == -1:
        return flat[:width].rstrip() + "…"
    start = max(0, pos - width // 3)
    end = min(len(flat), start + width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(flat) else ""
    return prefix + flat[start:end].strip() + suffix
