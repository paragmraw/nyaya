"""Pure-Python BM25Okapi over extracted judgment paragraphs (zero deps)."""

from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[./-][a-z0-9]+)*")
_COUNSEL_NAME_RE = re.compile(r"\b(?:Mr|Ms|Mrs|Dr|Shri|Smt)\.?\s+[A-Z]|\bSr\.?\s*Adv\.?|\bAOR\b")
_FOR_PARTY_RE = re.compile(
    r"(?i)\bFor\s+the\s+(?:Petitioner|Respondent|Appellant|Intervenor|Applicant)s?\s*:"
)
_COUNSEL_SUBMISSION_RE = re.compile(
    r"(?i)(?:"
    r"\bmade\s+the\s+following\s+submissions\b"
    r"|\bsubmissions\s+(?:of|on\s+behalf\s+of)\b"
    r"|\blearned\s+(?:senior\s+)?counsel\b[^.\n]{0,120}\b(?:submitted|argued|contended|urged|opposed|canvassed)\b"
    r"|\blearned\s+(?:attorney\s+general|solicitor\s+general|additional\s+solicitor\s+general|advocate\s+general|amicus\s+curiae)\b[^.\n]{0,120}\b(?:submitted|argued|contended|urged)\b"
    r")"
)
_HOLDING_CUE_RE = re.compile(
    r"(?i)\b(?:we\s+hold|we\s+conclude|we\s+formulate\s+our\s+conclusions|our\s+conclusions|"
    r"in\s+view\s+of\s+the\s+above\s+discussion|it\s+is\s+held\s+that|held\s*\(\s*per\b)"
)


def _is_counsel_list(para: str) -> bool:
    """Return True if a paragraph looks like a counsel appearance block."""
    if _FOR_PARTY_RE.search(para):
        return True
    return len(_COUNSEL_NAME_RE.findall(para)) >= 4


def _is_counsel_noise(para: str) -> bool:
    """Return True if a paragraph is counsel appearance/submission text without holding cues."""
    if _HOLDING_CUE_RE.search(para):
        return False
    return _is_counsel_list(para) or bool(_COUNSEL_SUBMISSION_RE.search(para))


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
            para = self.paragraphs[idx]
            if q_lower and q_lower in para.lower():
                score += 2.0
            if score > 0.0:
                if _HOLDING_CUE_RE.search(para):
                    score *= 1.25
                elif _is_counsel_list(para) or _COUNSEL_SUBMISSION_RE.search(para):
                    score *= 0.15
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
                if pos != idx and _is_counsel_noise(self.paragraphs[pos]):
                    continue
                if pos == idx:
                    selected[pos] = sc
                elif pos not in selected:
                    selected[pos] = 0.0
        return sorted(selected.items(), key=lambda item: item[0])
