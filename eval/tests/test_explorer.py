from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from eval.clients import JsonExtractionError
from eval.explorer import ExplorerContextProvider
from eval.models import (
    EvalCase,
    ExplorerConfig,
    LLMTrace,
    PreparedRepo,
    RepoSpec,
    RepositorySnapshot,
)


class StubExplorerClient:
    def __init__(self, payloads):
        self._payloads = list(payloads)

    def complete_json(self, system_prompt, user_prompt, *, schema_name, schema):
        payload = self._payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return LLMTrace(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            raw_response="{}",
            parsed_json=payload,
        )


def _provider(client=None, **config):
    return ExplorerContextProvider(
        client or StubExplorerClient([]),
        ExplorerConfig(**config),
    )


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "module.py").write_text(
        "def fix_bug():\n    return 'needle'\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True)
    return repo


def test_explorer_read_file_is_bounded_and_numbered(tmp_path: Path):
    repo = _make_repo(tmp_path)
    provider = _provider(max_read_lines=1)

    output = provider._run_action(
        repo,
        {
            "action": "read_file",
            "path": "pkg/module.py",
            "start_line": 2,
            "line_count": 20,
        },
    )

    assert output == "2:     return 'needle'"


def test_explorer_rejects_paths_outside_repo(tmp_path: Path):
    repo = _make_repo(tmp_path)
    provider = _provider()

    with pytest.raises(RuntimeError, match="escapes repository"):
        provider._run_action(
            repo,
            {
                "action": "read_file",
                "path": "../outside.py",
                "start_line": 1,
                "line_count": 1,
            },
        )


def test_explorer_search_returns_bounded_matches(tmp_path: Path):
    repo = _make_repo(tmp_path)
    provider = _provider(max_search_results=1)

    output = provider._run_action(
        repo,
        {
            "action": "search",
            "query": "needle",
            "path": None,
            "start_line": None,
            "line_count": None,
        },
    )

    assert "pkg/module.py:2:" in output
    assert len(output.splitlines()) == 1


def test_explorer_fetch_context_normalizes_finish_results(tmp_path: Path):
    repo_path = _make_repo(tmp_path)
    client = StubExplorerClient(
        [
            {
                "action": "finish",
                "query": None,
                "path": None,
                "start_line": None,
                "line_count": None,
                "rationale": "Found the relevant function.",
                "results": [
                    {
                        "path": "pkg/module.py",
                        "name": "fix_bug",
                        "content": "def fix_bug(): ...",
                        "score": 1.5,
                        "rationale": "This function handles the issue.",
                    }
                ],
            }
        ]
    )
    provider = _provider(client, max_results=1)
    repo = RepoSpec(id="sample", local_path=str(repo_path), cases=[])
    case = EvalCase(
        id="case-1",
        problem_statement="Fix the bug.",
        solution_patch="diff --git a/pkg/module.py b/pkg/module.py\n+return True\n",
    )
    prepared = PreparedRepo(
        repo_id="sample",
        repo_path=str(repo_path),
        head_commit="abc123",
        source=str(repo_path),
    )
    snapshot = RepositorySnapshot(
        repo_root=str(repo_path),
        head_commit="abc123",
        tracked_files_total=1,
        file_tree_excerpt="pkg/module.py",
        key_files={},
    )

    generated_query, response = provider.fetch_context(repo, case, prepared, snapshot)

    assert generated_query.query == "Fix the bug."
    assert response["provider"] == "explorer"
    assert response["results"][0]["node_id"] == "explorer:pkg/module.py"
    assert response["results"][0]["confidence"] == 1.0


def test_explorer_fetch_context_preserves_raw_response_on_json_parse_error(tmp_path: Path):
    repo_path = _make_repo(tmp_path)
    client = StubExplorerClient(
        [
            JsonExtractionError(
                "Could not parse JSON object from LLM response",
                raw_response='{"action":"search"}\nnot-json',
            )
        ]
    )
    provider = _provider(client)
    repo = RepoSpec(id="sample", local_path=str(repo_path), cases=[])
    case = EvalCase(
        id="case-1",
        problem_statement="Fix the bug.",
        solution_patch="diff --git a/pkg/module.py b/pkg/module.py\n+return True\n",
    )
    prepared = PreparedRepo(
        repo_id="sample",
        repo_path=str(repo_path),
        head_commit="abc123",
        source=str(repo_path),
    )
    snapshot = RepositorySnapshot(
        repo_root=str(repo_path),
        head_commit="abc123",
        tracked_files_total=1,
        file_tree_excerpt="pkg/module.py",
        key_files={},
    )

    generated_query, response = provider.fetch_context(repo, case, prepared, snapshot)

    assert generated_query.confidence == 0.0
    assert response["results"] == []
    assert response["trace"]["error_type"] == "json_extraction_error"
    assert response["trace"]["raw_response"] == '{"action":"search"}\nnot-json'
