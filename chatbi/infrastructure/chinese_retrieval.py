"""Bailian Chinese embeddings and deterministic BM25 candidate reranking."""
from __future__ import annotations

from collections import Counter
import math
import re
from typing import Iterable

from openai import OpenAI

from chatbi.application.progress import remaining_timeout
from chatbi.settings import Settings


class BailianEncoder:
    dimensions = 1024
    identity = "bailian-text-embedding-v3-1024"

    def __init__(self, settings: Settings) -> None:
        if settings.intent_api_key is None:
            raise ValueError("Bailian embedding key unavailable")
        self.client = OpenAI(api_key=settings.intent_api_key.get_secret_value(), base_url=settings.intent_base_url,
                             timeout=15, max_retries=0)
        self.usage = {"calls": 0, "reported_calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def encode(self, text: str) -> list[float]:
        return self.encode_many([text])[0]

    def encode_many(self, texts: Iterable[str]) -> list[list[float]]:
        items = list(texts)
        vectors: list[list[float]] = []
        for offset in range(0, len(items), 10):
            response = self.client.embeddings.create(model="text-embedding-v3", input=items[offset:offset + 10],
                dimensions=self.dimensions, timeout=remaining_timeout(15))
            batch = sorted(response.data, key=lambda row: row.index)
            self.usage["calls"] += 1
            if response.usage is not None:
                self.usage["reported_calls"] += 1
                self.usage["prompt_tokens"] += response.usage.prompt_tokens
            if len(batch) != len(items[offset:offset + 10]) or any(len(row.embedding) != self.dimensions for row in batch):
                raise ValueError("Embedding response dimensions mismatch")
            vectors.extend(row.embedding for row in batch)
        return vectors


def tokens(text: str) -> list[str]:
    text = text.lower()
    words = re.findall(r"[a-z_][a-z0-9_]*|[0-9]+", text)
    for part in re.findall(r"[\u3400-\u9fff]+", text):
        words.extend(part[i:i + 2] for i in range(len(part) - 1))
    return words


def rerank(question: str, result: dict, limit: int) -> tuple[list[dict], list[dict]]:
    metas = result.get("metadatas", [[]])[0]
    docs = result.get("documents", [[]])[0]
    distances = result.get("distances", [[]])[0]
    corpus = [Counter(tokens(doc or "")) for doc in docs]
    average = sum(sum(doc.values()) for doc in corpus) / max(len(corpus), 1) or 1
    query = set(tokens(question))
    ranked = []
    for i, meta in enumerate(metas):
        if not meta:
            continue
        score = 0.0
        doc = corpus[i] if i < len(corpus) else Counter()
        for word in query:
            count = doc[word]
            frequency = sum(word in item for item in corpus)
            idf = math.log(1 + (len(corpus) - frequency + .5) / (frequency + .5))
            score += idf * count * 2.2 / (count + 1.2 * (.25 + .75 * sum(doc.values()) / average))
        distance = float(distances[i]) if i < len(distances) else 1
        ranked.append((score + .25 * (1 - distance), meta, distance))
    ranked.sort(key=lambda row: row[0], reverse=True)
    return [row[1] for row in ranked[:limit]], [
        {"metadata": meta, "score": round(score, 4), "distance": round(distance, 4)} for score, meta, distance in ranked[:limit]]
