from middleware.models import SearchResultItem


def precision_at_k(
    results: list[SearchResultItem],
    ground_truth: list[str],
    k: int,
) -> float:
    if not ground_truth:
        return 0.0
    top_k = results[:k]
    relevant = sum(1 for item in top_k if item.node_id in ground_truth)
    return relevant / k


def mean_reciprocal_rank(
    results: list[SearchResultItem],
    ground_truth: list[str],
) -> float:
    if not ground_truth:
        return 0.0
    for rank, item in enumerate(results, start=1):
        if item.node_id in ground_truth:
            return 1.0 / rank
    return 0.0


def evaluate_query(
    results: list[SearchResultItem],
    ground_truth: list[str],
    k_values: list[int] = None,
) -> dict:
    if k_values is None:
        k_values = [1, 3, 5, 10]
    metrics = {
        f"precision@{k}": precision_at_k(results, ground_truth, k)
        for k in k_values
    }
    metrics["mrr"] = mean_reciprocal_rank(results, ground_truth)
    return metrics
