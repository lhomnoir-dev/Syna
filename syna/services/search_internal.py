"""Moteur de recherche interne (dans le vault Obsidian).

Deux approches combinées :

* **Lexicale** : BM25 sur des chunks de notes, avec bonus sur le titre et
  filtres par tag (``#python``) ; mode regex via ``/motif/``.
* **Sémantique** (optionnelle) : embeddings ``sentence-transformers`` en
  local, stockés dans un index vectoriel en mémoire (produit scalaire sur des
  vecteurs normalisés, équivalent à un index FAISS « flat »). Les deux
  classements sont fusionnés par *Reciprocal Rank Fusion*.

L'index est incrémental : ``refresh()`` ne ré-analyse que les fichiers dont la
date de modification a changé, et un observateur ``watchdog`` (voir
``services/watcher.py``) déclenche ce rafraîchissement dès qu'une note change.
"""

from __future__ import annotations

import logging
import math
import re
import threading
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .markdown import (
    Chunk,
    chunk_markdown,
    extract_tags,
    extract_wikilinks,
    make_excerpt,
    split_frontmatter,
    strip_accents,
    tokenize,
)

if TYPE_CHECKING:  # pragma: no cover
    from ..core.vault import Vault

log = logging.getLogger(__name__)

BM25_K1 = 1.4
BM25_B = 0.75
RRF_K = 60


@dataclass
class NoteDoc:
    rel: str
    title: str
    mtime: float
    tags: list[str]
    links: list[str]
    chunks: list[Chunk]
    terms: Counter = field(default_factory=Counter)
    raw: str = ""


@dataclass
class SearchHit:
    rel: str
    title: str
    heading: str
    excerpt: str
    score: float
    text: str = ""

    @property
    def location(self) -> str:
        return f"{self.title} › {self.heading}" if self.heading else self.title


class Embedder:
    """Embeddings locaux via sentence-transformers (dépendance optionnelle)."""

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer  # import paresseux

        self.model_name = model_name
        self._model = SentenceTransformer(model_name)

    def encode(self, texts: list[str]):
        return self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)


class VaultIndex:
    def __init__(self, vault: Vault, *, embedder: Embedder | None = None) -> None:
        self.vault = vault
        self.embedder = embedder
        self.notes: dict[str, NoteDoc] = {}
        self._chunk_df: Counter = Counter()
        self._note_df: Counter = Counter()
        self._n_chunks = 0
        self._total_chunk_len = 0
        self._vectors: dict[str, object] = {}  # rel -> np.ndarray (n_chunks, dim)
        self._lock = threading.RLock()
        self._dirty = True

    # ------------------------------------------------------------ indexation
    def mark_dirty(self) -> None:
        self._dirty = True

    def refresh(self, force: bool = False) -> tuple[int, int]:
        """Met à jour l'index ; renvoie (notes (ré)indexées, notes retirées)."""
        if not (self._dirty or force):
            return 0, 0
        with self._lock:
            self._dirty = False
            seen: set[str] = set()
            updated = 0
            for path in self.vault.iter_notes():
                rel = self.vault.relpath(path)
                seen.add(rel)
                try:
                    mtime = path.stat().st_mtime
                except FileNotFoundError:
                    continue
                current = self.notes.get(rel)
                if current is not None and current.mtime == mtime and not force:
                    continue
                try:
                    content = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    log.warning("Lecture impossible de %s : %s", rel, exc)
                    continue
                self._remove(rel)
                self._add(rel, path, mtime, content)
                updated += 1
            removed = [rel for rel in self.notes if rel not in seen]
            for rel in removed:
                self._remove(rel)
            if updated or removed:
                log.info("Index : %d note(s) indexée(s), %d retirée(s).", updated, len(removed))
            return updated, len(removed)

    def _add(self, rel: str, path: Path, mtime: float, content: str) -> None:
        _fm, body = split_frontmatter(content)
        title = path.stem
        chunks: list[Chunk] = []
        for heading, text in chunk_markdown(body):
            terms = Counter(tokenize(f"{heading} {text}"))
            chunks.append(Chunk(heading=heading, text=text, terms=dict(terms), length=sum(terms.values())))
        if not chunks:
            chunks.append(Chunk(heading="", text="", terms={}, length=0))
        note_terms: Counter = Counter()
        for c in chunks:
            note_terms.update(c.terms)
        note_terms.update({t: 3 for t in tokenize(title)})  # le titre pèse plus lourd
        doc = NoteDoc(
            rel=rel,
            title=title,
            mtime=mtime,
            tags=extract_tags(content),
            links=extract_wikilinks(content),
            chunks=chunks,
            terms=note_terms,
            raw=content,
        )
        self.notes[rel] = doc
        for c in chunks:
            self._chunk_df.update(c.terms.keys())
            self._n_chunks += 1
            self._total_chunk_len += c.length
        self._note_df.update(note_terms.keys())
        if self.embedder is not None:
            texts = [f"{title}\n{c.heading}\n{c.text}"[:2000] for c in chunks]
            try:
                self._vectors[rel] = self.embedder.encode(texts)
            except Exception:  # pragma: no cover - dépend du modèle
                log.exception("Échec des embeddings pour %s", rel)

    def _remove(self, rel: str) -> None:
        doc = self.notes.pop(rel, None)
        if doc is None:
            return
        for c in doc.chunks:
            self._chunk_df.subtract(c.terms.keys())
            self._n_chunks -= 1
            self._total_chunk_len -= c.length
        self._note_df.subtract(doc.terms.keys())
        self._chunk_df += Counter()  # purge des compteurs ≤ 0
        self._note_df += Counter()
        self._vectors.pop(rel, None)

    # ------------------------------------------------------------- recherche
    def search(self, query: str, k: int = 5) -> list[SearchHit]:
        """Recherche hybride ; renvoie les meilleurs chunks (un par note)."""
        self.refresh()
        query = query.strip()
        if not query:
            return []
        regex = re.fullmatch(r"/(.+)/(i?)", query)
        if regex:
            return self.regex_search(regex.group(1), ignore_case=bool(regex.group(2)), k=k)
        tags = {t.lower().lstrip("#") for t in re.findall(r"#[\w/\-]+", query)}
        text_query = re.sub(r"#[\w/\-]+", " ", query)
        with self._lock:
            candidates = self._tag_filter(tags)
            lexical = self._bm25(text_query, candidates)
            if not text_query.strip():
                # Requête composée uniquement de tags : toutes les notes taguées.
                lexical = [(rel, 0, 1.0) for rel in sorted(candidates or [])]
            semantic = self._semantic(text_query, candidates) if self.embedder else []
            ranked = self._fuse(lexical, semantic)
            terms = set(tokenize(text_query))
            hits: list[SearchHit] = []
            used: set[str] = set()
            for rel, idx, score in ranked:
                if rel in used:
                    continue
                used.add(rel)
                doc = self.notes[rel]
                chunk = doc.chunks[idx]
                hits.append(
                    SearchHit(
                        rel=rel,
                        title=doc.title,
                        heading=chunk.heading,
                        excerpt=make_excerpt(chunk.text or doc.raw, terms),
                        score=score,
                        text=chunk.text,
                    )
                )
                if len(hits) >= k:
                    break
            return hits

    def top_chunks(self, query: str, k: int = 6) -> list[SearchHit]:
        """Meilleurs chunks toutes notes confondues (contexte pour le LLM)."""
        self.refresh()
        with self._lock:
            lexical = self._bm25(query, None)
            semantic = self._semantic(query, None) if self.embedder else []
            ranked = self._fuse(lexical, semantic)[:k]
            terms = set(tokenize(query))
            return [
                SearchHit(
                    rel=rel,
                    title=self.notes[rel].title,
                    heading=self.notes[rel].chunks[idx].heading,
                    excerpt=make_excerpt(self.notes[rel].chunks[idx].text, terms),
                    score=score,
                    text=self.notes[rel].chunks[idx].text,
                )
                for rel, idx, score in ranked
            ]

    def regex_search(self, pattern: str, *, ignore_case: bool = False, k: int = 5) -> list[SearchHit]:
        try:
            rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as exc:
            raise ValueError(f"Expression régulière invalide : {exc}") from exc
        if len(pattern) > 200:
            raise ValueError("Expression régulière trop longue (200 caractères max).")
        hits: list[SearchHit] = []
        with self._lock:
            for rel, doc in sorted(self.notes.items()):
                matches = 0
                first_line = ""
                for line in doc.raw.splitlines():
                    if rx.search(line[:5000]):
                        matches += 1
                        first_line = first_line or line.strip()
                if rx.search(doc.title):
                    matches += 2
                if matches:
                    hits.append(SearchHit(rel, doc.title, "", first_line[:280] or doc.title, float(matches)))
        hits.sort(key=lambda h: -h.score)
        return hits[:k]

    def _tag_filter(self, tags: set[str]) -> set[str] | None:
        if not tags:
            return None
        out = set()
        for rel, doc in self.notes.items():
            note_tags = {t.lower() for t in doc.tags}
            # « #ai » correspond aussi aux tags imbriqués « #ai/llm ».
            if all(any(nt == t or nt.startswith(t + "/") for nt in note_tags) for t in tags):
                out.add(rel)
        return out

    def _bm25(self, query: str, candidates: set[str] | None) -> list[tuple[str, int, float]]:
        q_terms = tokenize(query)
        if not q_terms or self._n_chunks <= 0:
            return []
        avg_len = self._total_chunk_len / max(self._n_chunks, 1) or 1.0
        n = self._n_chunks
        idf = {t: math.log(1 + (n - self._chunk_df.get(t, 0) + 0.5) / (self._chunk_df.get(t, 0) + 0.5)) for t in set(q_terms)}
        q_folded = strip_accents(query.lower()).strip()
        results: list[tuple[str, int, float]] = []
        for rel, doc in self.notes.items():
            if candidates is not None and rel not in candidates:
                continue
            title_terms = set(tokenize(doc.title))
            title_bonus = sum(idf[t] for t in set(q_terms) if t in title_terms)
            if q_folded and q_folded == strip_accents(doc.title.lower()):
                title_bonus += 5.0
            for idx, c in enumerate(doc.chunks):
                score = 0.0
                for t in q_terms:
                    tf = c.terms.get(t, 0)
                    if not tf:
                        continue
                    denom = tf + BM25_K1 * (1 - BM25_B + BM25_B * c.length / avg_len)
                    score += idf[t] * tf * (BM25_K1 + 1) / denom
                if score > 0 or title_bonus > 0:
                    results.append((rel, idx, score + title_bonus))
        results.sort(key=lambda r: -r[2])
        return results

    def _semantic(self, query: str, candidates: set[str] | None, min_score: float = 0.25) -> list[tuple[str, int, float]]:
        if not query.strip() or not self._vectors:
            return []
        import numpy as np

        q = self.embedder.encode([query])[0]
        results: list[tuple[str, int, float]] = []
        for rel, mat in self._vectors.items():
            if candidates is not None and rel not in candidates:
                continue
            scores = np.asarray(mat) @ q
            for idx, s in enumerate(scores):
                if s >= min_score:
                    results.append((rel, idx, float(s)))
        results.sort(key=lambda r: -r[2])
        return results[:200]

    @staticmethod
    def _fuse(*rankings: Iterable[tuple[str, int, float]]) -> list[tuple[str, int, float]]:
        rankings = [list(r) for r in rankings if r]
        if len(rankings) == 1:
            return rankings[0]
        fused: dict[tuple[str, int], float] = {}
        for ranking in rankings:
            for rank, (rel, idx, _score) in enumerate(ranking):
                fused[(rel, idx)] = fused.get((rel, idx), 0.0) + 1.0 / (RRF_K + rank + 1)
        return sorted(((rel, idx, s) for (rel, idx), s in fused.items()), key=lambda r: -r[2])

    # ------------------------------------------------------- similarité notes
    def note_vector(self, terms: Counter) -> dict[str, float]:
        n = max(len(self.notes), 1)
        vec = {t: (1 + math.log(tf)) * math.log(1 + n / (1 + self._note_df.get(t, 0))) for t, tf in terms.items() if tf > 0}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    def similar_notes(self, text: str, *, exclude: set[str] | None = None, k: int = 10) -> list[tuple[str, float]]:
        """Notes les plus proches d'un texte : cosinus TF-IDF (et embeddings
        si disponibles, en prenant le maximum des deux scores)."""
        self.refresh()
        exclude = exclude or set()
        with self._lock:
            q_vec = self.note_vector(Counter(tokenize(text)))
            scores: dict[str, float] = {}
            for rel, doc in self.notes.items():
                if rel in exclude:
                    continue
                d_vec = self.note_vector(doc.terms)
                s = sum(w * d_vec.get(t, 0.0) for t, w in q_vec.items())
                if s > 0:
                    scores[rel] = s
            if self.embedder is not None and self._vectors:
                import numpy as np

                q = self.embedder.encode([text[:4000]])[0]
                for rel, mat in self._vectors.items():
                    if rel in exclude:
                        continue
                    mean = np.asarray(mat).mean(axis=0)
                    norm = float(np.linalg.norm(mean)) or 1.0
                    s = float(mean @ q) / norm
                    scores[rel] = max(scores.get(rel, 0.0), s)
            return sorted(scores.items(), key=lambda kv: -kv[1])[:k]

    def titles(self) -> dict[str, str]:
        with self._lock:
            return {rel: doc.title for rel, doc in self.notes.items()}

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"notes": len(self.notes), "chunks": self._n_chunks}
