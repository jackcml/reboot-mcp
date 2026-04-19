from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eval.clients import OpenAIJsonClient, RebootRestClient
from eval.environment import EvalEnvironment, PROJECT_ROOT
from eval.models import (
    CaseRunRecord,
    EvalCase,
    EvalManifest,
    GeneratedQuery,
    IngestRunResult,
    JudgeResult,
    RepoRunRecord,
    RepoSpec,
    RepositorySnapshot,
    RunSummary,
    load_manifest,
)
from eval.repo import RepositoryManager

QUERY_AGENT_SYSTEM_PROMPT = """You generate the first retrieval query for a coding agent.
The goal is to ask REBOOT for the most useful context to start solving the issue.
Use the repository snapshot to ground the query, but do not answer the issue and do not use the gold patch.

The agent's tool definition is as follows:
<tool>
**Before answering any question about the codebase**, call `reboot_search` with the developer's query and, if available, the current file path as `file_context`. Use the returned context to inform your answer — do not rely solely on files you have already read.
Always prefer `reboot_search` over manually grepping or reading files when the user asks a question about how the codebase works, where something is defined, or why something was built a certain way. Manual file reads are still appropriate for targeted edits after you already know which file to change.
</tool>
The query should be only one simple question to begin exploration of issue-related code context.
Do not ask for specific file contents, instead, ask a simple question related to the concept in the issue, like "Where are WCS transformations handled?".

Return strict JSON with keys:
- query: string
- rationale: string
- file_context: string or null
- confidence: number between 0 and 1
Do not include any other text."""

JUDGE_SYSTEM_PROMPT = """You judge retrieval quality for a code-context ranking system.
Focus on whether the retrieved results would help an agent solve the issue, not on whether the agent would definitely finish the task.
Use the gold patch only as ground truth for what files, functions, and concepts mattered.
Return strict JSON with keys:
- verdict: one of strong, partial, weak, irrelevant
- score: number between 0 and 1
- likely_useful: boolean
- reasoning: string
- key_hits: array of short strings
- missing_context: array of short strings
Do not include any other text."""

QUERY_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "query": {"type": "string"},
        "rationale": {"type": "string"},
        "file_context": {"type": ["string", "null"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": ["query", "rationale", "file_context", "confidence"],
}

JUDGE_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["strong", "partial", "weak", "irrelevant"]},
        "score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "likely_useful": {"type": "boolean"},
        "reasoning": {"type": "string"},
        "key_hits": {"type": "array", "items": {"type": "string"}},
        "missing_context": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "verdict",
        "score",
        "likely_useful",
        "reasoning",
        "key_hits",
        "missing_context",
    ],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ArtifactStore:
    def __init__(self, root: Path, run_id: str):
        self.run_id = run_id
        self.run_dir = root / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._events_path = self.run_dir / "events.jsonl"

    def write_json(self, relative_path: str, payload: Any) -> None:
        target = self.run_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def append_event(self, event_type: str, payload: dict[str, Any]) -> None:
        event = {"ts": utc_now(), "type": event_type, **payload}
        with self._events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event))
            handle.write("\n")


class LLMQueryAgent:
    def __init__(self, client: OpenAIJsonClient):
        self._client = client

    def generate_query(
        self,
        repo: RepoSpec,
        case: EvalCase,
        snapshot: RepositorySnapshot,
    ) -> GeneratedQuery:
        user_prompt = "\n".join(
            [
                f"Repository id: {repo.id}",
                f"Repository root: {snapshot.repo_root}",
                f"HEAD commit: {snapshot.head_commit}",
                "",
                "Problem statement:",
                case.problem_statement,
                "",
                "Tracked file tree excerpt:",
                snapshot.file_tree_excerpt or "<no files>",
                "",
                "Key file excerpts:",
                json.dumps(snapshot.key_files, indent=2),
                "",
                "Generate the best initial retrieval query for REBOOT.",
            ]
        )
        trace = self._client.complete_json(
            QUERY_AGENT_SYSTEM_PROMPT,
            user_prompt,
            schema_name="generated_query",
            schema=QUERY_JSON_SCHEMA,
        )
        payload = trace.parsed_json
        return GeneratedQuery(
            query=str(payload["query"]).strip(),
            rationale=str(payload.get("rationale", "")).strip(),
            file_context=payload.get("file_context"),
            confidence=float(payload["confidence"]) if payload.get("confidence") is not None else None,
            trace=trace,
        )


class LLMContextJudge:
    def __init__(self, client: OpenAIJsonClient):
        self._client = client

    def judge(
        self,
        repo: RepoSpec,
        case: EvalCase,
        query: GeneratedQuery,
        query_response: dict[str, Any],
        solution_patch: str,
    ) -> JudgeResult:
        user_prompt = "\n".join(
            [
                f"Repository id: {repo.id}",
                "",
                "Problem statement:",
                case.problem_statement,
                "",
                "Generated retrieval query:",
                json.dumps(
                    {
                        "query": query.query,
                        "rationale": query.rationale,
                        "file_context": query.file_context,
                        "confidence": query.confidence,
                    },
                    indent=2,
                ),
                "",
                "Retrieved ranking response:",
                json.dumps(query_response, indent=2),
                "",
                "Gold solution patch:",
                solution_patch,
                "",
                "Judge how well the retrieved ranking captures the context needed to solve the issue.",
            ]
        )
        trace = self._client.complete_json(
            JUDGE_SYSTEM_PROMPT,
            user_prompt,
            schema_name="judge_result",
            schema=JUDGE_JSON_SCHEMA,
        )
        payload = trace.parsed_json
        return JudgeResult(
            verdict=str(payload["verdict"]).strip(),
            score=float(payload["score"]),
            likely_useful=bool(payload["likely_useful"]),
            reasoning=str(payload["reasoning"]).strip(),
            key_hits=[str(item).strip() for item in payload.get("key_hits", [])],
            missing_context=[str(item).strip() for item in payload.get("missing_context", [])],
            trace=trace,
        )


class EvalRunner:
    def __init__(
        self,
        manifest: EvalManifest,
        manifest_path: Path,
        *,
        run_id: str | None = None,
        environment: EvalEnvironment | None = None,
        reboot_client: RebootRestClient | None = None,
        repo_manager: RepositoryManager | None = None,
        query_agent: LLMQueryAgent | None = None,
        judge: LLMContextJudge | None = None,
        sleep_fn=time.sleep,
        reuse_last_ingest: bool = False,
        keep_graph: bool = False,
    ):
        self._manifest = manifest
        self._manifest_path = manifest_path
        self._manifest_dir = manifest_path.parent
        self._run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._reuse_last_ingest = reuse_last_ingest
        artifact_root = self._resolve_project_path(manifest.workspace.artifact_root)
        clone_root = self._resolve_project_path(manifest.workspace.clone_root)
        self._artifacts = ArtifactStore(artifact_root, self._run_id)
        self._reboot_client = reboot_client or RebootRestClient(manifest.server)
        self._environment = environment or EvalEnvironment(
            manifest.environment,
            self._reboot_client,
            self._artifacts.run_dir,
            preserve_graph=reuse_last_ingest or keep_graph,
        )
        self._repo_manager = repo_manager or RepositoryManager(
            clone_root,
            self._manifest_dir,
            manifest.snapshot,
        )
        self._query_agent = query_agent or LLMQueryAgent(
            OpenAIJsonClient(manifest.llm.query_generator)
        )
        self._judge = judge or LLMContextJudge(OpenAIJsonClient(manifest.llm.judge))
        self._sleep_fn = sleep_fn

    def run(
        self,
        *,
        repo_filter: str | None = None,
        case_filter: str | None = None,
    ) -> RunSummary:
        started_at = utc_now()
        repos_out: list[RepoRunRecord] = []
        self._artifacts.write_json("manifest.json", self._manifest.model_dump(mode="json"))
        self._artifacts.append_event(
            "run_started",
            {
                "manifest_name": self._manifest.name,
                "repo_filter": repo_filter,
                "case_filter": case_filter,
            },
        )

        selected_repos = [
            repo for repo in self._manifest.repos if repo_filter is None or repo.id == repo_filter
        ]
        try:
            for repo_index, repo in enumerate(selected_repos):
                self._environment.prepare_for_repo(repo_index)
                repo_record = self._run_repo(repo, case_filter)
                repos_out.append(repo_record)
                self._artifacts.write_json(
                    f"repos/{repo.id}.json",
                    repo_record.model_dump(mode="json"),
                )
        finally:
            self._environment.shutdown()

        finished_at = utc_now()
        case_scores = [
            case.judge_result.score
            for repo in repos_out
            for case in repo.cases
            if case.judge_result is not None
        ]
        summary = RunSummary(
            run_id=self._run_id,
            manifest_name=self._manifest.name,
            started_at=started_at,
            finished_at=finished_at,
            total_repos=len(repos_out),
            total_cases=sum(len(repo.cases) for repo in repos_out),
            successful_cases=sum(1 for repo in repos_out for case in repo.cases if case.status == "ok"),
            average_judge_score=(sum(case_scores) / len(case_scores)) if case_scores else None,
            repos=repos_out,
        )
        self._artifacts.write_json("summary.json", summary.model_dump(mode="json"))
        self._artifacts.append_event(
            "run_finished",
            {
                "total_repos": summary.total_repos,
                "total_cases": summary.total_cases,
                "successful_cases": summary.successful_cases,
                "average_judge_score": summary.average_judge_score,
            },
        )
        return summary

    def _run_repo(self, repo: RepoSpec, case_filter: str | None) -> RepoRunRecord:
        self._artifacts.append_event("repo_started", {"repo_id": repo.id})
        prepared_repo = self._repo_manager.prepare_repo(repo)
        snapshot = self._repo_manager.build_snapshot(prepared_repo)
        if self._reuse_last_ingest:
            self._artifacts.append_event("ingest_reused", {"repo_id": repo.id})
            ingest = IngestRunResult(
                job_id="reused",
                timed_out=False,
                start_response={"stage": "reused"},
                status_history=[],
                final_status={"stage": "reused"},
            )
        else:
            ingest = self._run_ingest(prepared_repo.repo_path)

        cases_out: list[CaseRunRecord] = []
        selected_cases = [
            case for case in repo.cases if case_filter is None or case.id == case_filter
        ]
        for case in selected_cases:
            case_record = self._run_case(repo, case, snapshot)
            cases_out.append(case_record)
            self._artifacts.write_json(
                f"cases/{repo.id}/{case.id}.json",
                case_record.model_dump(mode="json"),
            )

        repo_record = RepoRunRecord(
            repo_id=repo.id,
            prepared_repo=prepared_repo,
            snapshot=snapshot,
            ingest=ingest,
            cases=cases_out,
        )
        self._artifacts.append_event(
            "repo_finished",
            {
                "repo_id": repo.id,
                "head_commit": prepared_repo.head_commit,
                "cases": len(cases_out),
                "ingest_timed_out": ingest.timed_out,
                "ingest_stage": ingest.final_status.get("stage"),
            },
        )
        return repo_record

    def _run_ingest(self, repo_path: str) -> IngestRunResult:
        start_response = self._reboot_client.start_ingest(repo_path, incremental=False, verbose=False)
        job_id = str(start_response["job_id"])
        deadline = time.monotonic() + self._manifest.server.ingest_timeout_seconds
        history: list[dict[str, Any]] = []
        final_status: dict[str, Any] | None = None

        while time.monotonic() < deadline:
            status = self._reboot_client.get_ingest_status(job_id)
            history.append({"observed_at": utc_now(), **status})
            if status.get("stage") in {"completed", "failed", "cancelled"}:
                final_status = status
                break
            self._sleep_fn(self._manifest.server.ingest_poll_interval_seconds)

        timed_out = final_status is None
        cancel_response = None
        if timed_out:
            final_status = history[-1] if history else {"job_id": job_id, "stage": "timed_out"}
            if self._manifest.server.cancel_ingest_on_timeout:
                cancel_response = self._reboot_client.cancel_ingest(job_id)
                post_cancel = self._reboot_client.get_ingest_status(job_id)
                final_status = {"observed_at": utc_now(), **post_cancel}

        return IngestRunResult(
            job_id=job_id,
            timed_out=timed_out,
            start_response=start_response,
            status_history=history,
            final_status=final_status,
            cancel_response=cancel_response,
        )

    def _run_case(
        self,
        repo: RepoSpec,
        case: EvalCase,
        snapshot: RepositorySnapshot,
    ) -> CaseRunRecord:
        started_at = utc_now()
        start_clock = time.monotonic()
        solution_patch = case.load_solution_patch(self._manifest_dir)
        self._artifacts.append_event(
            "case_started",
            {"repo_id": repo.id, "case_id": case.id},
        )

        generated_query = None
        query_response = None
        judge_result = None
        error = None
        status = "ok"
        try:
            if case.query_override:
                generated_query = GeneratedQuery(
                    query=case.query_override,
                    rationale="Used query_override from manifest.",
                    file_context=case.file_context,
                    confidence=1.0,
                )
            else:
                generated_query = self._query_agent.generate_query(repo, case, snapshot)
                if case.file_context and not generated_query.file_context:
                    generated_query = generated_query.model_copy(
                        update={"file_context": case.file_context}
                    )
            query_response = self._reboot_client.query(
                generated_query.query,
                generated_query.file_context,
            )
            judge_result = self._judge.judge(
                repo,
                case,
                generated_query,
                query_response,
                solution_patch,
            )
        except Exception as exc:
            status = "error"
            error = str(exc)

        finished_at = utc_now()
        record = CaseRunRecord(
            repo_id=repo.id,
            case_id=case.id,
            title=case.title,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=time.monotonic() - start_clock,
            problem_statement=case.problem_statement,
            solution_patch=solution_patch,
            generated_query=generated_query,
            query_response=query_response,
            judge_result=judge_result,
            error=error,
            metadata=case.metadata,
        )
        self._artifacts.append_event(
            "case_finished",
            {
                "repo_id": repo.id,
                "case_id": case.id,
                "status": status,
                "judge_score": judge_result.score if judge_result else None,
                "error": error,
            },
        )
        return record

    @staticmethod
    def _resolve_project_path(raw_path: str) -> Path:
        path = Path(raw_path)
        if path.is_absolute():
            return path
        return PROJECT_ROOT / path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the REBOOT eval harness.")
    parser.add_argument(
        "--config",
        required=True,
        help="Path to an eval manifest JSON file.",
    )
    parser.add_argument(
        "--repo",
        help="Optional repo id filter.",
    )
    parser.add_argument(
        "--case",
        help="Optional case id filter.",
    )
    parser.add_argument(
        "--run-id",
        help="Optional explicit run id. Defaults to a UTC timestamp.",
    )
    parser.add_argument(
        "--reuse-last-ingest",
        action="store_true",
        help=(
            "Skip ingestion and reuse the Neo4j volume from the previous run. "
            "The graph must already be populated (run with --keep-graph first); "
            "useful for iterating on query/judge agents."
        ),
    )
    parser.add_argument(
        "--keep-graph",
        action="store_true",
        help=(
            "Run ingestion normally but preserve the Neo4j volume on exit and "
            "skip inter-repo resets. Use this to bootstrap a graph that "
            "subsequent --reuse-last-ingest runs can attach to."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manifest_path = Path(args.config)
    if not manifest_path.is_absolute():
        manifest_path = (PROJECT_ROOT / manifest_path).resolve()
    manifest = load_manifest(manifest_path)
    runner = EvalRunner(
        manifest,
        manifest_path,
        run_id=args.run_id,
        reuse_last_ingest=args.reuse_last_ingest,
        keep_graph=args.keep_graph,
    )
    summary = runner.run(repo_filter=args.repo, case_filter=args.case)
    print(
        json.dumps(
            {
                "run_id": summary.run_id,
                "manifest_name": summary.manifest_name,
                "total_repos": summary.total_repos,
                "total_cases": summary.total_cases,
                "successful_cases": summary.successful_cases,
                "average_judge_score": summary.average_judge_score,
                "artifacts_dir": str(
                    (PROJECT_ROOT / manifest.workspace.artifact_root / summary.run_id).resolve()
                ),
            },
            indent=2,
        )
    )
    return 0
