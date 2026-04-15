from __future__ import annotations

import os
import subprocess
from pathlib import Path

from eval.environment import PROJECT_ROOT
from eval.models import PreparedRepo, RepoSpec, RepositorySnapshot, SnapshotConfig


class RepositoryManager:
    def __init__(self, clone_root: Path, manifest_dir: Path, snapshot_config: SnapshotConfig):
        self._clone_root = clone_root
        self._manifest_dir = manifest_dir
        self._snapshot_config = snapshot_config
        self._clone_root.mkdir(parents=True, exist_ok=True)

    def prepare_repo(self, spec: RepoSpec) -> PreparedRepo:
        destination = self._clone_root / spec.id
        source = spec.clone_url or str(self._resolve_manifest_path(spec.local_path))
        if destination.exists() and (destination / ".git").exists():
            self._git(["fetch", "--all", "--tags", "--prune"], cwd=destination)
        else:
            if destination.exists():
                raise RuntimeError(
                    f"Repo destination {destination} exists but is not a git checkout."
                )
            self._git(["clone", source, str(destination)])
        if spec.checkout:
            self._git(["checkout", "--detach", spec.checkout], cwd=destination)
        else:
            self._git(["checkout", "--detach", "HEAD"], cwd=destination)
        head_commit = self._git(["rev-parse", "HEAD"], cwd=destination).strip()
        return PreparedRepo(
            repo_id=spec.id,
            repo_path=str(destination),
            checkout=spec.checkout,
            head_commit=head_commit,
            source=source,
        )

    def build_snapshot(self, repo: PreparedRepo) -> RepositorySnapshot:
        repo_path = Path(repo.repo_path)
        tracked_files = self._tracked_files(repo_path)
        file_lines = tracked_files[: self._snapshot_config.max_files]
        file_tree_excerpt = self._truncate(
            "\n".join(file_lines), self._snapshot_config.max_tree_chars
        )
        key_files: dict[str, str] = {}
        for relative_path in self._snapshot_config.key_files:
            candidate = repo_path / relative_path
            if candidate.exists() and candidate.is_file():
                key_files[relative_path] = self._truncate(
                    candidate.read_text(encoding="utf-8", errors="replace"),
                    self._snapshot_config.max_file_chars,
                )
        return RepositorySnapshot(
            repo_root=str(repo_path),
            head_commit=repo.head_commit,
            tracked_files_total=len(tracked_files),
            file_tree_excerpt=file_tree_excerpt,
            key_files=key_files,
        )

    def _tracked_files(self, repo_path: Path) -> list[str]:
        try:
            output = self._git(["ls-files"], cwd=repo_path)
            return [line for line in output.splitlines() if line]
        except RuntimeError:
            files: list[str] = []
            for dirpath, dirnames, filenames in os.walk(repo_path):
                dirnames[:] = [d for d in dirnames if d not in {".git", ".venv", "venv", "__pycache__"}]
                for filename in filenames:
                    full_path = Path(dirpath) / filename
                    files.append(str(full_path.relative_to(repo_path)))
            files.sort()
            return files

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return f"{text[: limit - 32]}\n... [{len(text) - limit + 32} chars truncated]"

    def _resolve_manifest_path(self, raw_path: str | None) -> Path:
        if raw_path is None:
            raise RuntimeError("Expected a local_path value to resolve.")
        path = Path(raw_path)
        if path.is_absolute():
            return path
        return self._manifest_dir / path

    @staticmethod
    def _git(args: list[str], cwd: Path | None = None) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd or PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} failed in {cwd or PROJECT_ROOT}\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}"
            )
        return completed.stdout
