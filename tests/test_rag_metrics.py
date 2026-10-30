"""Retrieval metrics against hand-computed expected values."""

import math

import pytest

from ai_quality_lab.rag.retrieval_metrics import (
    aggregate,
    compute_metrics,
    hit_rate_at_k,
    mrr_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)


def approx(value):
    return pytest.approx(value, abs=1e-9)


def test_all_relevant_retrieved_at_the_top():
    m = compute_metrics(["A", "B", "X", "Y"], {"A", "B"}, k=4, level="document")
    assert (m.hit_rate, m.recall, m.precision, m.mrr, m.ndcg) == (1.0, 1.0, 0.5, 1.0, 1.0)


def test_no_relevant_retrieved():
    m = compute_metrics(["X", "Y", "Z"], {"A"}, k=3, level="document")
    assert (m.hit_rate, m.recall, m.precision, m.mrr, m.ndcg) == (0.0, 0.0, 0.0, 0.0, 0.0)


def test_some_relevant_retrieved():
    # relevant {A, B, C}; top-3 = X, A, Y -> 1 of 3 relevant found
    assert recall_at_k(["X", "A", "Y", "B"], {"A", "B", "C"}, 3) == approx(1 / 3)
    assert precision_at_k(["X", "A", "Y", "B"], {"A", "B", "C"}, 3) == approx(1 / 3)
    assert recall_at_k(["X", "A", "Y", "B"], {"A", "B", "C"}, 4) == approx(2 / 3)


def test_relevant_retrieved_but_ranked_poorly():
    ranked = ["X", "Y", "Z", "A"]
    assert hit_rate_at_k(ranked, {"A"}, 4) == 1.0
    assert mrr_at_k(ranked, {"A"}, 4) == approx(0.25)
    assert ndcg_at_k(ranked, {"A"}, 4) == approx(1 / math.log2(5))
    assert hit_rate_at_k(ranked, {"A"}, 3) == 0.0  # outside K


def test_irrelevant_results_occupy_top_positions():
    good = compute_metrics(["A", "B", "X", "Y"], {"A", "B"}, k=4, level="chunk")
    bad = compute_metrics(["X", "Y", "A", "B"], {"A", "B"}, k=4, level="chunk")
    assert good.recall == bad.recall == 1.0  # recall alone hides the ranking problem
    assert bad.mrr == approx(1 / 3) and bad.ndcg < good.ndcg
    ideal = 1 + 1 / math.log2(3)
    assert bad.ndcg == approx((1 / math.log2(4) + 1 / math.log2(5)) / ideal)


def test_questions_without_relevant_ids_are_not_applicable():
    m = compute_metrics(["X", "Y"], set(), k=2, level="document")
    assert not m.applicable
    assert (m.hit_rate, m.recall, m.precision, m.mrr, m.ndcg) == (None,) * 5


def test_duplicates_in_ranking_count_once():
    assert precision_at_k(["A", "A", "X"], {"A"}, 2) == approx(0.5)  # unique: A, X


def test_fewer_results_than_k():
    assert precision_at_k(["A"], {"A", "B"}, 4) == 1.0
    assert recall_at_k(["A"], {"A", "B"}, 4) == approx(0.5)
    assert precision_at_k([], {"A"}, 4) == 0.0


def test_invalid_k_rejected():
    with pytest.raises(ValueError):
        recall_at_k(["A"], {"A"}, 0)


def test_aggregate_excludes_not_applicable_questions():
    results = [
        compute_metrics(["A"], {"A"}, 1, "document"),
        compute_metrics(["X"], {"A"}, 1, "document"),
        compute_metrics(["X"], set(), 1, "document"),
    ]
    summary = aggregate(results)
    assert (summary["questions"], summary["applicable"], summary["not_applicable"]) == (3, 2, 1)
    assert summary["hit_rate"] == 0.5 and summary["recall"] == 0.5
    assert aggregate([compute_metrics(["X"], set(), 1, "chunk")])["recall"] is None


def test_to_dict_rounds_and_labels_level():
    data = compute_metrics(["X", "A"], {"A"}, 2, "chunk").to_dict()
    assert data["level"] == "chunk" and data["mrr"] == 0.5 and data["applicable"] is True
