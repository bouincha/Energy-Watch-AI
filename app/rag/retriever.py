"""Retrieval-Augmented Generation over the energy-efficiency knowledge base.

Chunking: documents are split by Markdown section (## heading), and long sections are split
again into overlapping windows. Each chunk keeps its document title and section heading so
answers can cite their sources.

Retrieval: Okapi BM25 (pure Python, no external service) with query expansion through a
small domain synonym map. The retriever interface (``search``) can be swapped for a vector
store without touching the agents.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have", "how", "i", "in", "is", "it",
    "its", "of", "on", "or", "that", "the", "this", "to", "was", "were", "what", "when", "where", "which", "why",
    "with", "my", "our", "we", "you", "your", "do", "does", "can", "should", "could", "would", "will", "into",
    "than", "then", "there", "these", "those", "they", "their", "them", "about", "over", "per", "all", "any",
    "each", "also", "more", "most", "very",
}

SYNONYMS = {
    "ac": ["hvac", "cooling"], "aircon": ["hvac"], "air-conditioning": ["hvac"], "chiller": ["hvac", "cooling"],
    "leak": ["leaks", "compressed"], "compressor": ["compressed", "air"], "pue": ["cooling", "data", "center"],
    "datacenter": ["data", "center"], "bill": ["tariff", "cost"], "expensive": ["cost", "tariff", "peak"],
    "night": ["schedule", "occupancy", "outside"], "weekend": ["schedule", "outside", "weekends"],
    "lights": ["lighting"], "lamp": ["lighting", "led"], "solar": ["photovoltaic", "self-consumption"],
    "co2": ["carbon", "emissions"], "carbon": ["co2", "emissions"], "hospital": ["healthcare", "clinic"],
    "clinic": ["healthcare", "hospital"], "drift": ["pue", "progressive"], "spike": ["fault", "sudden"],
}


@dataclass(frozen=True)
class Chunk:
    id: str
    doc: str
    title: str
    section: str
    text: str

    @property
    def citation(self) -> str:
        return f"{self.title} > {self.section}"


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)?", text.lower())
    return [t for t in tokens if t not in STOPWORDS and len(t) > 1]


def chunk_markdown(doc: str, text: str, max_words: int = 140, overlap: int = 30) -> list[Chunk]:
    title_match = re.search(r"^# (.+)$", text, re.M)
    title = title_match.group(1).strip() if title_match else doc
    chunks: list[Chunk] = []
    for n, part in enumerate(re.split(r"^## ", text, flags=re.M)[1:]):
        heading, _, body = part.partition("\n")
        words = body.split()
        start = 0
        k = 0
        while start < len(words):
            window = " ".join(words[start:start + max_words])
            chunks.append(Chunk(f"{doc}#{n}.{k}", doc, title, heading.strip(), window))
            if start + max_words >= len(words):
                break
            start += max_words - overlap
            k += 1
    return chunks


class BM25Retriever:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = chunks
        self.k1, self.b = k1, b
        # Section headings and titles are weighted by repeating them.
        self.docs = [tokenize(f"{c.title} {c.section} {c.section} {c.text}") for c in chunks]
        self.tf = [Counter(d) for d in self.docs]
        self.avgdl = sum(len(d) for d in self.docs) / max(len(self.docs), 1)
        df: Counter[str] = Counter()
        for d in self.docs:
            df.update(set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def _expand(self, query: str) -> list[str]:
        terms = tokenize(query)
        expanded = list(terms)
        for t in terms:
            expanded.extend(SYNONYMS.get(t, []))
        return expanded

    def search(self, query: str, k: int = 4, min_score: float = 0.5) -> list[dict]:
        terms = self._expand(query)
        scored = []
        for i, tf in enumerate(self.tf):
            dl = len(self.docs[i])
            score = 0.0
            for t in terms:
                f = tf.get(t)
                if not f:
                    continue
                norm = self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                score += self.idf.get(t, 0) * f * (self.k1 + 1) / (f + norm)
            if score >= min_score:
                scored.append((score, i))
        scored.sort(reverse=True)
        out = []
        for score, i in scored[:k]:
            c = self.chunks[i]
            out.append({"id": c.id, "doc": c.doc, "title": c.title, "section": c.section,
                        "citation": c.citation, "score": round(score, 2), "text": c.text})
        return out


@lru_cache
def get_retriever() -> BM25Retriever:
    chunks: list[Chunk] = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        chunks.extend(chunk_markdown(path.stem, path.read_text(encoding="utf-8")))
    return BM25Retriever(chunks)
