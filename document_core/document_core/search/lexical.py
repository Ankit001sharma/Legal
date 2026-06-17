"""Lightweight lexical scoring for child chunk retrieval (embeddings later)."""

from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def score_query(query: str, document_text: str) -> float:
    """BM25-like lexical overlap score in [0, 1]."""
    q_tokens = tokenize(query)
    if not q_tokens:
        return 0.0
    d_tokens = tokenize(document_text)
    if not d_tokens:
        return 0.0

    q_counts = Counter(q_tokens)
    d_counts = Counter(d_tokens)
    d_len = len(d_tokens)
    avg_dl = max(d_len, 1)
    k1, b = 1.2, 0.75
    score = 0.0
    for term, qf in q_counts.items():
        if term not in d_counts:
            continue
        tf = d_counts[term]
        idf = math.log(1 + (1) / (1 + tf))
        denom = tf + k1 * (1 - b + b * (d_len / avg_dl))
        score += idf * ((tf * (k1 + 1)) / denom) * qf

    # Normalize to rough 0-1 range
    return min(1.0, score / (len(q_counts) * 3.0))
