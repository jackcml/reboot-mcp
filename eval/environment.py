from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from eval.clients import RebootRestClient
from eval.models import EnvironmentConfig

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class DockerComposeController:
    def __init__(self, config, artifact_dir: Path, *, preserve_volumes: bool = False):
        self._config = config
        self._artifact_dir = artifact_dir
        self._preserve_volumes = preserve_volumes

    def up(self) -> None:
        if not self._config.enabled:
            return
        self._run_compose(["up", "-d", *self._config.services])

    def reset(self) -> None:
        if not self._config.enabled:
            return
        self._run_compose(["down", *self._down_flags()])
        self.up()

    def shutdown(self) -> None:
        if not self._config.enabled or not self._config.shutdown_on_exit:
            return
        self._run_compose(["down", *self._down_flags()])

    def _down_flags(self) -> list[str]:
        flags = ["--remove-orphans"]
        if not self._preserve_volumes:
            flags.insert(0, "--volumes")
        return flags

    def _run_compose(self, args: list[str]) -> None:
        compose_file = self._resolve_project_path(self._config.compose_file)
        cwd = self._resolve_project_path(self._config.project_dir)
        env = os.environ.copy()
        env["COMPOSE_PROJECT_NAME"] = self._config.compose_project_name
        completed = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), *args],
            cwd=cwd,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "docker compose command failed.\n"
                f"command: docker compose -f {compose_file} {' '.join(args)}\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}"
            )

    @staticmethod
    def _resolve_project_path(raw_path: str) -> Path:
        path = Path(raw_path)
        if path.is_absolute():
            return path
        return PROJECT_ROOT / path


class ServerProcessController:
    def __init__(self, config, server_client: RebootRestClient, artifact_dir: Path):
        self._config = config
        self._server_client = server_client
        self._artifact_dir = artifact_dir
        self._process: subprocess.Popen[str] | None = None
        self._log_handle = None

    def start(self) -> None:
        if not self._config.auto_start:
            self._server_client.wait_for_health()
            return
        if self._process and self._process.poll() is None:
            return
        parsed = urlparse(self._server_client._config.base_url)
        command = self._config.command or self._default_command(parsed)
        cwd = self._resolve_project_path(self._config.cwd)
        env = os.environ.copy()
        env.update(self._config.env)
        env["SERVER_PORT"] = str(parsed.port or 8010)
        log_path = self._artifact_dir / "reboot-server.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = log_path.open("a", encoding="utf-8")
        self._process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self._server_client.wait_for_health()

    def stop(self) -> None:
        if not self._config.auto_start:
            return
        if self._process is None or self._process.poll() is not None:
            self._close_log()
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=self._config.shutdown_grace_seconds)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)
        finally:
            self._close_log()
        self._process = None

    def _default_command(self, parsed) -> list[str]:
        port = str(parsed.port or 8010)
        return [
            sys.executable,
            "-m",
            "uvicorn",
            "middleware.main:app",
            "--host",
            parsed.hostname or "127.0.0.1",
            "--port",
            port,
        ]

    @staticmethod
    def _resolve_project_path(raw_path: str) -> Path:
        path = Path(raw_path)
        if path.is_absolute():
            return path
        return PROJECT_ROOT / path

    def _close_log(self) -> None:
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None


class EvalEnvironment:
    def __init__(
        self,
        config: EnvironmentConfig,
        server_client: RebootRestClient,
        artifact_dir: Path,
        *,
        preserve_graph: bool = False,
    ):
        self._docker = DockerComposeController(
            config.docker, artifact_dir, preserve_volumes=preserve_graph
        )
        self._server = ServerProcessController(
            config.server_process, server_client, artifact_dir
        )
        self._reset_between_repos = (
            config.docker.reset_between_repos and not preserve_graph
        )
        self._started = False

    def ensure_started(self) -> None:
        if self._started:
            return
        self._docker.up()
        self._server.start()
        self._started = True

    def prepare_for_repo(self, repo_index: int) -> None:
        if repo_index == 0:
            self.ensure_started()
            return
        if not self._reset_between_repos:
            return
        self._server.stop()
        self._docker.reset()
        self._server.start()

    def shutdown(self) -> None:
        self._server.stop()
        self._docker.shutdown()
