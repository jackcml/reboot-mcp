from __future__ import annotations

import json
from pathlib import Path

from eval.models import EvalManifest, GeneratedQuery, JudgeResult, PreparedRepo, RepoSpec, RepositorySnapshot
from eval.runner import EvalRunner


class StubEnvironment:
    def __init__(self):
        self.prepared_indexes: list[int] = []
        self.shutdown_calls = 0

    def prepare_for_repo(self, repo_index: int) -> None:
        self.prepared_indexes.append(repo_index)

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class StubRepoManager:
    def __init__(self, repo_path: Path):
        self._repo_path = repo_path

    def prepare_repo(self, spec: RepoSpec) -> PreparedRepo:
        return PreparedRepo(
            repo_id=spec.id,
            repo_path=str(self._repo_path),
            checkout=spec.checkout,
            head_commit="abc123",
            source=spec.clone_url or str(self._repo_path),
        )

    def build_snapshot(self, repo: PreparedRepo) -> RepositorySnapshot:
        return RepositorySnapshot(
            repo_root=repo.repo_path,
            head_commit=repo.head_commit,
            tracked_files_total=2,
            file_tree_excerpt="pkg/module.py\nREADME.md",
            key_files={"README.md": "Sample project"},
        )


class StubRebootClient:
    def __init__(self, *, timeout_then_cancel: bool = False):
        self.timeout_then_cancel = timeout_then_cancel
        self.cancel_calls = 0
        self.query_calls: list[tuple[str, str | None]] = []

    def start_ingest(self, repo_path: str, *, incremental: bool = False, verbose: bool = False):
        return {"status": "started", "job_id": "job-1", "repo_path": repo_path}

    def get_ingest_status(self, job_id: str):
        if self.timeout_then_cancel:
            if self.cancel_calls:
                return {"job_id": job_id, "stage": "cancelled", "processed_nodes": 4}
            return {"job_id": job_id, "stage": "ingesting", "processed_nodes": 4}
        return {"job_id": job_id, "stage": "completed", "processed_nodes": 9}

    def cancel_ingest(self, job_id: str):
        self.cancel_calls += 1
        return {"status": "cancelled", "job_id": job_id}

    def query(self, query: str, file_context: str | None = None):
        self.query_calls.append((query, file_context))
        return {
            "query_id": "query-1",
            "query_type": "factual",
            "results": [
                {
                    "node_id": "n1",
                    "name": "pkg.module.fix_bug",
                    "content": "Relevant function summary",
                    "score": 0.95,
                    "confidence": 1.0,
                }
            ],
        }


class StubQueryAgent:
    def __init__(self):
        self.calls = 0

    def generate_query(self, repo, case, snapshot):
        self.calls += 1
        return GeneratedQuery(
            query="Where is the failing behavior implemented?",
            rationale="Start from the likely affected code path.",
            file_context="pkg/module.py",
            confidence=0.72,
        )


class StubJudge:
    def judge(self, repo, case, query, query_response, solution_patch):
        return JudgeResult(
            verdict="strong",
            score=0.88,
            likely_useful=True,
            reasoning="The retrieval surfaces the function touched by the gold patch.",
            key_hits=["pkg.module.fix_bug"],
            missing_context=[],
        )


def _write_manifest(tmp_path: Path, *, query_override: str | None = None) -> Path:
    manifest_path = tmp_path / "manifest.json"
    manifest = {
        "name": "test-manifest",
        "workspace": {
            "clone_root": str(tmp_path / "repos"),
            "artifact_root": str(tmp_path / "artifacts")
        },
        "environment": {
            "docker": {"enabled": False, "reset_between_repos": True, "shutdown_on_exit": False},
            "server_process": {"auto_start": False}
        },
        "repos": [
            {
                "id": "sample",
                "clone_url": "https://example.com/repo.git",
                "cases": [
                    {
                        "id": "case-1",
                        "problem_statement": "Fix the crash in the sample module.",
                        "solution_patch": "diff --git a/pkg/module.py b/pkg/module.py\n+return True\n"
                    }
                ]
            }
        ]
    }
    if query_override:
        manifest["repos"][0]["cases"][0]["query_override"] = query_override
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def test_eval_runner_happy_path_writes_artifacts(tmp_path: Path):
    manifest_path = _write_manifest(tmp_path)
    manifest = EvalManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
    environment = StubEnvironment()
    reboot_client = StubRebootClient()
    query_agent = StubQueryAgent()
    runner = EvalRunner(
        manifest,
        manifest_path,
        run_id="run-001",
        environment=environment,
        reboot_client=reboot_client,
        repo_manager=StubRepoManager(tmp_path / "checkout"),
        query_agent=query_agent,
        judge=StubJudge(),
        sleep_fn=lambda _: None,
    )

    summary = runner.run()

    assert summary.total_repos == 1
    assert summary.total_cases == 1
    assert summary.successful_cases == 1
    assert summary.average_judge_score == 0.88
    assert environment.prepared_indexes == [0]
    assert environment.shutdown_calls == 1
    assert query_agent.calls == 1
    assert reboot_client.query_calls == [("Where is the failing behavior implemented?", "pkg/module.py")]
    summary_path = tmp_path / "artifacts" / "run-001" / "summary.json"
    case_path = tmp_path / "artifacts" / "run-001" / "cases" / "sample" / "case-1.json"
    assert summary_path.exists()
    assert case_path.exists()


def test_eval_runner_cancels_timed_out_ingest_and_uses_query_override(tmp_path: Path):
    manifest_path = _write_manifest(tmp_path, query_override="Find the broken function.")
    manifest = EvalManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
    manifest.server.ingest_timeout_seconds = 0
    environment = StubEnvironment()
    reboot_client = StubRebootClient(timeout_then_cancel=True)
    query_agent = StubQueryAgent()
    runner = EvalRunner(
        manifest,
        manifest_path,
        run_id="run-002",
        environment=environment,
        reboot_client=reboot_client,
        repo_manager=StubRepoManager(tmp_path / "checkout"),
        query_agent=query_agent,
        judge=StubJudge(),
        sleep_fn=lambda _: None,
    )

    summary = runner.run()

    repo_record = summary.repos[0]
    assert repo_record.ingest.timed_out is True
    assert repo_record.ingest.cancel_response == {"status": "cancelled", "job_id": "job-1"}
    assert repo_record.ingest.final_status["stage"] == "cancelled"
    assert reboot_client.cancel_calls == 1
    assert query_agent.calls == 0
    assert reboot_client.query_calls == [("Find the broken function.", None)]
