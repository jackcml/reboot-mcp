from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from eval.clients import OpenAIJsonClient
from eval.models import EvalCase, ExplorerConfig, GeneratedQuery, PreparedRepo, RepoSpec, RepositorySnapshot


EXPLORER_SYSTEM_PROMPT = """You are a read-only codebase explorer for an eval baseline.
Your job is to fetch the most useful code context for solving the issue, not to solve it.
Use the available actions to inspect the repository and then finish with ranked context snippets.

Return strict JSON matching the schema on every turn.
Allowed actions:
- list_files: inspect tracked file paths.
- search: search repository text for a query.
- read_file: read a bounded excerpt from a repository file.
- finish: return ranked context results.

Finish results should be concise and should identify files, symbols, snippets, and why each item matters."""


EXPLORER_ACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": ["list_files", "search", "read_file", "finish"]},
        "query": {"type": ["string", "null"]},
        "path": {"type": ["string", "null"]},
        "start_line": {"type": ["integer", "null"], "minimum": 1},
        "line_count": {"type": ["integer", "null"], "minimum": 1},
        "rationale": {"type": "string"},
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "name": {"type": "string"},
                    "content": {"type": "string"},
                    "score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "rationale": {"type": "string"},
                },
                "required": ["path", "name", "content", "score", "rationale"],
            },
        },
    },
    "required": ["action", "query", "path", "start_line", "line_count", "rationale", "results"],
}


class ExplorerContextProvider:
    def __init__(self, client: OpenAIJsonClient, config: ExplorerConfig):
        self._client = client
        self._config = config

    def fetch_context(
        self,
        repo: RepoSpec,
        case: EvalCase,
        prepared_repo: PreparedRepo,
        snapshot: RepositorySnapshot,
    ) -> tuple[GeneratedQuery, dict[str, Any]]:
        repo_root = Path(prepared_repo.repo_path).resolve()
        focus = case.query_override or case.problem_statement
        history: list[dict[str, Any]] = []
        traces: list[dict[str, Any]] = []
        final_payload: dict[str, Any] | None = None

        for _ in range(self._config.max_steps):
            trace = self._client.complete_json(
                EXPLORER_SYSTEM_PROMPT,
                self._build_prompt(repo, case, snapshot, focus, history),
                schema_name="explorer_action",
                schema=EXPLORER_ACTION_SCHEMA,
            )
            traces.append(trace.model_dump(mode="json"))
            payload = trace.parsed_json
            action = str(payload["action"])
            if action == "finish":
                final_payload = payload
                break
            try:
                observation = self._run_action(repo_root, payload)
            except RuntimeError as exc:
                observation = str(exc)
            history.append(
                {
                    "action": action,
                    "request": {
                        "query": payload.get("query"),
                        "path": payload.get("path"),
                        "start_line": payload.get("start_line"),
                        "line_count": payload.get("line_count"),
                        "rationale": payload.get("rationale"),
                    },
                    "observation": self._truncate(observation, self._config.max_observation_chars),
                }
            )

        if final_payload is None:
            final_payload = {
                "rationale": "Explorer reached the step limit before finishing.",
                "results": [],
            }

        generated_query = GeneratedQuery(
            query=focus,
            rationale=str(final_payload.get("rationale") or "Explorer gathered direct context."),
            file_context=case.file_context,
            confidence=1.0 if final_payload.get("results") else 0.0,
        )
        response = {
            "provider": "explorer",
            "query_id": f"explorer:{repo.id}:{case.id}",
            "query_type": "explorer_context",
            "results": self._normalize_results(final_payload.get("results", [])),
            "trace": {
                "steps": history,
                "llm_traces": traces,
                "step_limit": self._config.max_steps,
            },
        }
        return generated_query, response

    def _build_prompt(
        self,
        repo: RepoSpec,
        case: EvalCase,
        snapshot: RepositorySnapshot,
        focus: str,
        history: list[dict[str, Any]],
    ) -> str:
        return "\n".join(
            [
                f"Repository id: {repo.id}",
                f"Repository root: {snapshot.repo_root}",
                f"HEAD commit: {snapshot.head_commit}",
                f"Optional file context: {case.file_context or '<none>'}",
                "",
                "Problem statement:",
                case.problem_statement,
                "",
                "Explorer focus:",
                focus,
                "",
                "Tracked file tree excerpt:",
                snapshot.file_tree_excerpt or "<no files>",
                "",
                "Key file excerpts:",
                json.dumps(snapshot.key_files, indent=2),
                "",
                "Prior actions and observations:",
                json.dumps(history, indent=2),
                "",
                f"Return finish only when you have enough context. Return at most {self._config.max_results} results.",
            ]
        )

    def _run_action(self, repo_root: Path, payload: dict[str, Any]) -> str:
        action = payload["action"]
        if action == "list_files":
            return "\n".join(self._tracked_files(repo_root)[: self._config.max_search_results])
        if action == "search":
            query = str(payload.get("query") or "").strip()
            if not query:
                return "search requires a non-empty query"
            return self._search(repo_root, query)
        if action == "read_file":
            path = str(payload.get("path") or "").strip()
            if not path:
                return "read_file requires a path"
            start_line = int(payload.get("start_line") or 1)
            line_count = min(int(payload.get("line_count") or self._config.max_read_lines), self._config.max_read_lines)
            return self._read_file(repo_root, path, start_line, line_count)
        return f"unsupported action: {action}"

    def _tracked_files(self, repo_root: Path) -> list[str]:
        completed = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0:
            return [line for line in completed.stdout.splitlines() if line]
        files: list[str] = []
        for dirpath, dirnames, filenames in os.walk(repo_root):
            dirnames[:] = [d for d in dirnames if d not in {".git", ".venv", "venv", "__pycache__"}]
            for filename in filenames:
                files.append(str((Path(dirpath) / filename).relative_to(repo_root)))
        files.sort()
        return files

    def _search(self, repo_root: Path, query: str) -> str:
        try:
            completed = subprocess.run(
                ["rg", "--line-number", "--fixed-strings", "--max-count", "5", query],
                cwd=repo_root,
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return self._python_search(repo_root, query)
        if completed.returncode == 0:
            return "\n".join(completed.stdout.splitlines()[: self._config.max_search_results])
        if completed.returncode == 1:
            return "<no matches>"
        return completed.stderr.strip() or "<search failed>"

    def _python_search(self, repo_root: Path, query: str) -> str:
        matches: list[str] = []
        for relative_path in self._tracked_files(repo_root):
            path = repo_root / relative_path
            if not path.is_file():
                continue
            for line_no, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(),
                start=1,
            ):
                if query in line:
                    matches.append(f"{relative_path}:{line_no}:{line}")
                    if len(matches) >= self._config.max_search_results:
                        return "\n".join(matches)
        return "\n".join(matches) if matches else "<no matches>"

    def _read_file(self, repo_root: Path, raw_path: str, start_line: int, line_count: int) -> str:
        path = self._resolve_repo_path(repo_root, raw_path)
        if not path.is_file():
            return f"{raw_path} is not a file"
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start_index = max(start_line - 1, 0)
        end_index = min(start_index + line_count, len(lines))
        numbered = [
            f"{line_no}: {lines[line_no - 1]}"
            for line_no in range(start_index + 1, end_index + 1)
        ]
        return "\n".join(numbered) if numbered else "<empty excerpt>"

    @staticmethod
    def _resolve_repo_path(repo_root: Path, raw_path: str) -> Path:
        candidate = (repo_root / raw_path).resolve()
        try:
            candidate.relative_to(repo_root)
        except ValueError as exc:
            raise RuntimeError(f"Explorer path escapes repository: {raw_path}") from exc
        return candidate

    def _normalize_results(self, raw_results: Any) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if not isinstance(raw_results, list):
            return results
        for index, item in enumerate(raw_results[: self._config.max_results], start=1):
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            name = str(item.get("name") or path or f"result-{index}").strip()
            content = str(item.get("content") or "").strip()
            score = float(item.get("score") or 0.0)
            results.append(
                {
                    "node_id": f"explorer:{path or index}",
                    "name": name,
                    "file_path": path or None,
                    "content": content,
                    "score": max(0.0, min(score, 1.0)),
                    "confidence": max(0.0, min(score, 1.0)),
                    "explanation": str(item.get("rationale") or "").strip(),
                }
            )
        return results

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return f"{text[: limit - 32]}\n... [{len(text) - limit + 32} chars truncated]"
