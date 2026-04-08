"""
Tests for QueryClassifier — covers the heuristic fast path and the
SearchConfigSelector weight recipes for all four query types.
No LLM calls are made in these tests.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from middleware.components.query_classifier import QueryClassifier, _DEBUG_SIGNALS
from middleware.components.search_config import SearchConfigSelector, RECIPES
from middleware.models import QueryType


# ─── Heuristic fast-path tests ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_heuristic_detects_traceback():
    classifier = QueryClassifier()
    result = await classifier.classify("Traceback (most recent call last): File main.py line 10")
    assert result == QueryType.debugging


@pytest.mark.asyncio
async def test_heuristic_detects_error_colon():
    classifier = QueryClassifier()
    result = await classifier.classify("I'm getting TypeError: cannot unpack non-sequence int")
    assert result == QueryType.debugging


@pytest.mark.asyncio
async def test_heuristic_detects_valueerror():
    classifier = QueryClassifier()
    result = await classifier.classify("valueerror raised when calling update_confidence")
    assert result == QueryType.debugging


@pytest.mark.asyncio
async def test_heuristic_detects_why_is():
    classifier = QueryClassifier()
    result = await classifier.classify("why is the confidence score not updating")
    assert result == QueryType.debugging


@pytest.mark.asyncio
async def test_heuristic_detects_why_doesnt():
    classifier = QueryClassifier()
    result = await classifier.classify("why doesn't reboot_search return any results")
    assert result == QueryType.debugging


@pytest.mark.asyncio
async def test_heuristic_detects_not_working():
    classifier = QueryClassifier()
    result = await classifier.classify("the ingestion pipeline is not working after the last change")
    assert result == QueryType.debugging


@pytest.mark.asyncio
async def test_heuristic_detects_failed():
    classifier = QueryClassifier()
    result = await classifier.classify("ingest job failed with no error message")
    assert result == QueryType.debugging


@pytest.mark.asyncio
async def test_heuristic_is_case_insensitive():
    classifier = QueryClassifier()
    result = await classifier.classify("TRACEBACK from the server logs")
    assert result == QueryType.debugging


@pytest.mark.asyncio
async def test_heuristic_does_not_trigger_on_normal_query():
    # Should fall through to LLM — mock the LLM to return "factual"
    classifier = QueryClassifier()
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "factual"

    with patch.object(classifier._openai.chat.completions, "create", new=AsyncMock(return_value=mock_response)):
        result = await classifier.classify("where is the FeedbackLogger initialized")

    assert result == QueryType.factual


@pytest.mark.asyncio
async def test_heuristic_does_not_trigger_on_architectural_query():
    classifier = QueryClassifier()
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "architectural"

    with patch.object(classifier._openai.chat.completions, "create", new=AsyncMock(return_value=mock_response)):
        result = await classifier.classify("how is the middleware organized across modules")

    assert result == QueryType.architectural


@pytest.mark.asyncio
async def test_heuristic_does_not_trigger_on_explanatory_query():
    classifier = QueryClassifier()
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "explanatory"

    with patch.object(classifier._openai.chat.completions, "create", new=AsyncMock(return_value=mock_response)):
        result = await classifier.classify("what does the confidence ranker do")

    assert result == QueryType.explanatory


@pytest.mark.asyncio
async def test_llm_fallback_on_exception_returns_factual():
    classifier = QueryClassifier()

    with patch.object(classifier._openai.chat.completions, "create", new=AsyncMock(side_effect=Exception("api error"))):
        result = await classifier.classify("what is the return type of search_graph")

    assert result == QueryType.factual


@pytest.mark.asyncio
async def test_llm_returns_debugging_type():
    classifier = QueryClassifier()
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "debugging"

    with patch.object(classifier._openai.chat.completions, "create", new=AsyncMock(return_value=mock_response)):
        result = await classifier.classify("the search results seem wrong")

    assert result == QueryType.debugging


# ─── _DEBUG_SIGNALS completeness ──────────────────────────────────────────────

def test_debug_signals_covers_common_python_exceptions():
    expected = {"typeerror", "valueerror", "keyerror", "attributeerror",
                "indexerror", "importerror", "nameerror", "runtimeerror"}
    assert expected.issubset(set(_DEBUG_SIGNALS))


def test_debug_signals_covers_natural_language_patterns():
    expected = {"why is", "why does", "why isn't", "why doesn't",
                "not working", "failed", "broken", "crash"}
    assert expected.issubset(set(_DEBUG_SIGNALS))


# ─── SearchConfig weight recipes ──────────────────────────────────────────────

def test_all_four_query_types_have_recipes():
    for qt in QueryType:
        assert qt in RECIPES, f"missing recipe for {qt}"


def test_debugging_recipe_is_recency_dominant():
    config = RECIPES[QueryType.debugging]
    assert config.recency_weight > config.semantic_weight
    assert config.recency_weight > config.structural_weight


def test_architectural_recipe_is_structural_dominant():
    config = RECIPES[QueryType.architectural]
    assert config.structural_weight > config.semantic_weight
    assert config.structural_weight > config.recency_weight


def test_explanatory_recipe_is_semantic_dominant():
    config = RECIPES[QueryType.explanatory]
    assert config.semantic_weight > config.recency_weight
    assert config.semantic_weight > config.structural_weight


def test_procedural_recipe_is_structural_dominant():
    config = RECIPES[QueryType.procedural]
    assert config.structural_weight > config.semantic_weight
    assert config.structural_weight > config.recency_weight


def test_factual_recipe_is_recency_dominant():
    config = RECIPES[QueryType.factual]
    assert config.recency_weight > config.semantic_weight
    assert config.recency_weight > config.structural_weight


def test_all_recipes_weights_sum_to_one():
    for qt, config in RECIPES.items():
        total = round(config.semantic_weight + config.recency_weight + config.structural_weight, 10)
        assert total == 1.0, f"{qt} weights sum to {total}, expected 1.0"


def test_selector_returns_correct_recipe_for_each_type():
    selector = SearchConfigSelector()
    for qt in QueryType:
        config = selector.select(qt)
        assert config == RECIPES[qt]


def test_selector_falls_back_to_factual_for_unknown_type():
    selector = SearchConfigSelector()
    # Pass a string that isn't a valid QueryType to trigger the fallback
    result = selector.select("unknown_type")
    assert result == RECIPES[QueryType.factual]
