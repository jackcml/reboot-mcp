import pytest
from middleware.components.retrieval_metrics import (
    precision_at_k,
    mean_reciprocal_rank,
    evaluate_query,
)
from middleware.models import SearchResultItem


def make_result(node_id: str, score: float = 0.9) -> SearchResultItem:
    return SearchResultItem(
        node_id=node_id,
        name=f"node_{node_id}",
        content=f"content for {node_id}",
        score=score,
    )


def test_precision_at_k_all_relevant():
    results = [make_result("a"), make_result("b"), make_result("c")]
    ground_truth = ["a", "b"]

    assert precision_at_k(results, ground_truth, k=2) == 1.0
    assert precision_at_k(results, ground_truth, k=3) == pytest.approx(2/3)


def test_precision_at_k_partial():
    results = [make_result("a"), make_result("b"), make_result("c")]
    ground_truth = ["b", "c"]

    assert precision_at_k(results, ground_truth, k=1) == 0.0
    assert precision_at_k(results, ground_truth, k=2) == 0.5
    assert precision_at_k(results, ground_truth, k=3) == pytest.approx(2/3)


def test_precision_at_k_none_relevant():
    results = [make_result("a"), make_result("b"), make_result("c")]
    ground_truth = ["x", "y"]

    assert precision_at_k(results, ground_truth, k=1) == 0.0
    assert precision_at_k(results, ground_truth, k=3) == 0.0


def test_precision_at_k_empty_ground_truth():
    results = [make_result("a"), make_result("b")]
    ground_truth: list[str] = []

    assert precision_at_k(results, ground_truth, k=2) == 0.0


def test_precision_at_k_empty_results():
    results: list[SearchResultItem] = []
    ground_truth = ["a", "b"]

    assert precision_at_k(results, ground_truth, k=1) == 0.0


def test_precision_at_k_k_larger_than_results():
    results = [make_result("a")]
    ground_truth = ["a", "b"]

    assert precision_at_k(results, ground_truth, k=5) == 0.2


def test_mrr_first_relevant_at_rank_1():
    results = [make_result("a"), make_result("b"), make_result("c")]
    ground_truth = ["a"]

    assert mean_reciprocal_rank(results, ground_truth) == 1.0


def test_mrr_first_relevant_at_rank_2():
    results = [make_result("x"), make_result("a"), make_result("c")]
    ground_truth = ["a"]

    assert mean_reciprocal_rank(results, ground_truth) == 0.5


def test_mrr_first_relevant_at_rank_3():
    results = [make_result("x"), make_result("y"), make_result("a")]
    ground_truth = ["a"]

    assert mean_reciprocal_rank(results, ground_truth) == pytest.approx(1/3)


def test_mrr_no_relevant():
    results = [make_result("a"), make_result("b"), make_result("c")]
    ground_truth = ["x", "y"]

    assert mean_reciprocal_rank(results, ground_truth) == 0.0


def test_mrr_multiple_relevant_uses_first():
    results = [make_result("x"), make_result("a"), make_result("b")]
    ground_truth = ["b", "a"]

    assert mean_reciprocal_rank(results, ground_truth) == pytest.approx(1/2)


def test_mrr_empty_ground_truth():
    results = [make_result("a"), make_result("b")]
    ground_truth: list[str] = []

    assert mean_reciprocal_rank(results, ground_truth) == 0.0


def test_evaluate_query_returns_all_metrics():
    results = [make_result("a"), make_result("b"), make_result("c"), make_result("d")]
    ground_truth = ["b", "c"]

    metrics = evaluate_query(results, ground_truth)

    assert "precision@1" in metrics
    assert "precision@3" in metrics
    assert "precision@5" in metrics
    assert "precision@10" in metrics
    assert "mrr" in metrics
    assert metrics["precision@1"] == 0.0
    assert metrics["precision@3"] == pytest.approx(2/3)
    assert metrics["mrr"] == pytest.approx(1/2)


def test_evaluate_query_custom_k_values():
    results = [make_result("a"), make_result("b"), make_result("c")]
    ground_truth = ["a"]

    metrics = evaluate_query(results, ground_truth, k_values=[1, 2])

    assert "precision@1" in metrics
    assert "precision@2" in metrics
    assert "precision@5" not in metrics
    assert metrics["precision@1"] == 1.0
    assert metrics["precision@2"] == 0.5
