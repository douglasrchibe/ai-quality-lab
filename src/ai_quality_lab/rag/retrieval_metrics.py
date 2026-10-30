"""Deterministic retrieval metrics computed from ground-truth relevant IDs (no LLM judge).

All metrics take a *ranked* list of retrieved IDs (best first) and a set of relevant IDs, and
consider the first ``k`` retrieved results:

* Hit Rate@K   = 1 if any relevant ID is in the top K, else 0.
* Recall@K     = |relevant in top K| / |relevant|.
* Precision@K  = |relevant in top K| / |results returned in the top K| (at most K). Using the
                 returned count keeps document-level precision fair when several chunks of one
                 document collapse into a single document result.
* MRR@K        = 1 / rank of the first relevant result in the top K (0 if none).
* NDCG@K       = DCG@K / ideal DCG@K with binary relevance and log2(rank + 1) discounting.

When a question has NO relevant IDs (the answer is not in the corpus), every metric is ``None``
("not applicable") rather than 0 or 1, and aggregates exclude it. Document-level and chunk-level
measurements are computed separately from the same retrieval.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

METRIC_NAMES = ("hit_rate", "recall", "precision", "mrr", "ndcg")


def _check(k: int) -> None:
    if k < 1:
        raise ValueError("k must be at least 1.")


def _top(ranked: Sequence[str], k: int) -> list[str]:
    return list(dict.fromkeys(ranked))[:k]  # de-duplicate, keep first occurrence


def hit_rate_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float | None:
    _check(k)
    relevant = set(relevant)
    if not relevant:
        return None
    return 1.0 if relevant & set(_top(ranked, k)) else 0.0


def recall_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float | None:
    _check(k)
    relevant = set(relevant)
    if not relevant:
        return None
    return len(relevant & set(_top(ranked, k))) / len(relevant)


def precision_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float | None:
    _check(k)
    relevant = set(relevant)
    top = _top(ranked, k)
    if not relevant:
        return None
    if not top:
        return 0.0
    return sum(1 for r in top if r in relevant) / len(top)


def mrr_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float | None:
    _check(k)
    relevant = set(relevant)
    if not relevant:
        return None
    for rank, result in enumerate(_top(ranked, k), start=1):
        if result in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float | None:
    _check(k)
    relevant = set(relevant)
    if not relevant:
        return None
    dcg = sum(1.0 / math.log2(rank + 1)
              for rank, result in enumerate(_top(ranked, k), start=1) if result in relevant)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(relevant), k) + 1))
    return dcg / ideal


@dataclass(frozen=True)
class RankingMetrics:
    level: str  # "document" or "chunk"
    k: int
    ranked: tuple[str, ...]
    relevant: tuple[str, ...]
    hit_rate: float | None
    recall: float | None
    precision: float | None
    mrr: float | None
    ndcg: float | None

    @property
    def applicable(self) -> bool:
        return bool(self.relevant)

    def to_dict(self) -> dict:
        return {
            "level": self.level, "k": self.k, "ranked": list(self.ranked),
            "relevant": list(self.relevant), "applicable": self.applicable,
            **{name: _round(getattr(self, name)) for name in METRIC_NAMES},
        }


def compute_metrics(ranked: Sequence[str], relevant: Iterable[str], k: int, level: str
                    ) -> RankingMetrics:
    relevant = tuple(sorted(set(relevant)))
    return RankingMetrics(
        level=level, k=k, ranked=tuple(_top(ranked, k)), relevant=relevant,
        hit_rate=hit_rate_at_k(ranked, relevant, k),
        recall=recall_at_k(ranked, relevant, k),
        precision=precision_at_k(ranked, relevant, k),
        mrr=mrr_at_k(ranked, relevant, k),
        ndcg=ndcg_at_k(ranked, relevant, k),
    )


def aggregate(results: Sequence[RankingMetrics]) -> dict:
    """Mean of each metric over applicable questions; counts make the denominator explicit."""
    applicable = [r for r in results if r.applicable]
    summary = {"questions": len(results), "applicable": len(applicable),
               "not_applicable": len(results) - len(applicable)}
    for name in METRIC_NAMES:
        values = [getattr(r, name) for r in applicable]
        summary[name] = _round(sum(values) / len(values)) if values else None
    return summary


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)
