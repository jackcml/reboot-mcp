from __future__ import annotations

import pytest

from eval.clients import JsonExtractionError, _extract_json


def test_extract_json_accepts_first_object_when_response_has_extra_json():
    payload = _extract_json('{"action":"search","query":"needle"}\n{"action":"read_file"}')

    assert payload == {"action": "search", "query": "needle"}


def test_extract_json_accepts_object_with_trailing_text():
    payload = _extract_json('{"action":"finish","results":[]} trailing explanation')

    assert payload == {"action": "finish", "results": []}


def test_extract_json_raises_clear_error_with_raw_response_when_no_object():
    raw_response = "not json"

    with pytest.raises(JsonExtractionError) as exc_info:
        _extract_json(raw_response)

    assert "did not contain a JSON object" in str(exc_info.value)
    assert exc_info.value.raw_response == raw_response
