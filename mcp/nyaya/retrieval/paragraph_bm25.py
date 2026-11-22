"""Pure-Python BM25Okapi over extracted judgment paragraphs (<80 LOC, zero deps)."""

from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[./-][a-z0-9]+)*")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class ParagraphBM25:
    """Ephemeral in-memory BM25Okapi ranker for single-judgment ``search_within``."""

    def __init__(self, paragraphs: list[str], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.paragraphs = paragraphs
        self.k1 = k1
        self.b = b
        self._docs: list[list[str]] = [_tokenize(p) for p in paragraphs]
        self._tfs: list[Counter[str]] = [Counter(d) for d in self._docs]
        self._n = len(self._docs)
        self._avgdl = (sum(len(d) for d in self._docs) / self._n) if self._n else 0.0
        df: Counter[str] = Counter()
        for tf in self._tfs:
            for term in tf:
                df[term] += 1
        self._idf: dict[str, float] = {
            term: math.log(1.0 + (self._n - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def score_all(self, query: str) -> list[float]:
        q_terms = _tokenize(query)
        if not q_terms or self._n == 0 or self._avgdl == 0.0:
            return [0.0] * self._n
        q_lower = query.strip().lower()
        scores: list[float] = []
        for idx, tf in enumerate(self._tfs):
            dl = len(self._docs[idx])
            score = 0.0
            for term in q_terms:
                freq = tf.get(term, 0)
                if freq == 0:
                    continue
                idf = self._idf.get(term, 0.0)
                denom = freq + self.k1 * (1.0 - self.b + self.b * (dl / self._avgdl))
                score += idf * (freq * (self.k1 + 1.0)) / denom
            if q_lower and q_lower in self.paragraphs[idx].lower():
                score += 2.0
            scores.append(score)
        return scores

    def rank(
        self,
        query: str,
        *,
        top_k: int = 5,
        context_window: int = 1,
    ) -> list[tuple[int, float]]:
        """Return 0-based ``(para_idx, score)`` hits expanded by ``context_window``."""
        scores = self.score_all(query)
        ranked = sorted(
            ((idx, sc) for idx, sc in enumerate(scores) if sc > 0.0),
            key=lambda item: item[1],
            reverse=True,
        )[: max(1, top_k)]
        if not ranked:
            return []
        selected: dict[int, float] = {}
        for idx, sc in ranked:
            start = max(0, idx - context_window)
            end = min(self._n - 1, idx + context_window)
            for pos in range(start, end + 1):
                if pos not in selected:
                    selected[pos] = sc if pos == idx else 0.0
        return sorted(selected.items(), key=lambda item: item[0])
