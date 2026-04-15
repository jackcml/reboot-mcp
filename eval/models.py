from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OpenAIModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = "gpt-5-mini"
    api_key_env: str = "OPENAI_API_KEY"
    base_url_env: str = "OPENAI_BASE_URL"
    temperature: float = 0.0
    max_tokens: int = 800
    timeout_seconds: int = 120


class EvalLLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_generator: OpenAIModelConfig = Field(
        default_factory=lambda: OpenAIModelConfig(max_tokens=250)
    )
    judge: OpenAIModelConfig = Field(
        default_factory=lambda: OpenAIModelConfig(max_tokens=1200)
    )


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = "http://127.0.0.1:8010"
    health_path: str = "/health"
    ingest_path: str = "/ingest"
    ingest_status_path_template: str = "/ingest-status/{job_id}"
    ingest_cancel_path_template: str = "/ingest-cancel/{job_id}"
    query_path: str = "/query"
    startup_timeout_seconds: int = 60
    request_timeout_seconds: int = 60
    ingest_timeout_seconds: int = 600
    ingest_poll_interval_seconds: float = 5.0
    cancel_ingest_on_timeout: bool = True


class DockerEnvironmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    compose_file: str = "eval/docker-compose.eval.yml"
    compose_project_name: str = "reboot-eval"
    project_dir: str = "."
    services: list[str] = Field(default_factory=lambda: ["neo4j"])
    reset_between_repos: bool = True
    shutdown_on_exit: bool = True


class ServerProcessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_start: bool = True
    cwd: str = "."
    command: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(
        default_factory=lambda: {
            "NEO4J_URI": "bolt://127.0.0.1:17687",
            "NEO4J_USER": "neo4j",
            "NEO4J_PASSWORD": "reboot_dev",
            "SERVER_PORT": "8010",
        }
    )
    shutdown_grace_seconds: int = 15


class EnvironmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    docker: DockerEnvironmentConfig = Field(default_factory=DockerEnvironmentConfig)
    server_process: ServerProcessConfig = Field(default_factory=ServerProcessConfig)


class WorkspaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clone_root: str = "eval/workspaces/repos"
    artifact_root: str = "eval/artifacts"


class SnapshotConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_files: int = 400
    max_tree_chars: int = 12000
    max_file_chars: int = 4000
    key_files: list[str] = Field(
        default_factory=lambda: [
            "README.md",
            "README.rst",
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "package.json",
            "Cargo.toml",
            "go.mod",
        ]
    )


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str | None = None
    problem_statement: str
    solution_patch: str | None = None
    solution_patch_path: str | None = None
    query_override: str | None = None
    file_context: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_patch_source(self) -> "EvalCase":
        if not self.solution_patch and not self.solution_patch_path:
            raise ValueError("Each eval case must define solution_patch or solution_patch_path.")
        return self

    def load_solution_patch(self, manifest_dir: Path) -> str:
        if self.solution_patch is not None:
            return self.solution_patch
        assert self.solution_patch_path is not None
        patch_path = Path(self.solution_patch_path)
        if not patch_path.is_absolute():
            patch_path = manifest_dir / patch_path
        return patch_path.read_text(encoding="utf-8")


class RepoSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    clone_url: str | None = None
    local_path: str | None = None
    checkout: str | None = None
    cases: list[EvalCase]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_source(self) -> "RepoSpec":
        if bool(self.clone_url) == bool(self.local_path):
            raise ValueError("Each repo must define exactly one of clone_url or local_path.")
        return self


class EvalManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    llm: EvalLLMConfig = Field(default_factory=EvalLLMConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    snapshot: SnapshotConfig = Field(default_factory=SnapshotConfig)
    repos: list[RepoSpec]


class PreparedRepo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_id: str
    repo_path: str
    checkout: str | None = None
    head_commit: str
    source: str


class RepositorySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_root: str
    head_commit: str
    tracked_files_total: int
    file_tree_excerpt: str
    key_files: dict[str, str]


class LLMTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_prompt: str
    user_prompt: str
    raw_response: str
    parsed_json: dict[str, Any]


class GeneratedQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    rationale: str
    file_context: str | None = None
    confidence: float | None = None
    trace: LLMTrace | None = None


class JudgeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: str
    score: float
    likely_useful: bool
    reasoning: str
    key_hits: list[str] = Field(default_factory=list)
    missing_context: list[str] = Field(default_factory=list)
    trace: LLMTrace | None = None


class IngestRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    timed_out: bool = False
    start_response: dict[str, Any]
    status_history: list[dict[str, Any]] = Field(default_factory=list)
    final_status: dict[str, Any] = Field(default_factory=dict)
    cancel_response: dict[str, Any] | None = None


class CaseRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_id: str
    case_id: str
    title: str | None = None
    status: str
    started_at: str
    finished_at: str
    duration_seconds: float
    problem_statement: str
    solution_patch: str
    generated_query: GeneratedQuery | None = None
    query_response: dict[str, Any] | None = None
    judge_result: JudgeResult | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepoRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_id: str
    prepared_repo: PreparedRepo
    snapshot: RepositorySnapshot
    ingest: IngestRunResult
    cases: list[CaseRunRecord]


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    manifest_name: str
    started_at: str
    finished_at: str
    total_repos: int
    total_cases: int
    successful_cases: int
    average_judge_score: float | None = None
    repos: list[RepoRunRecord] = Field(default_factory=list)


def load_manifest(path: Path) -> EvalManifest:
    return EvalManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
