from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib import error, parse, request

from eval.models import LLMTrace, OpenAIModelConfig, ServerConfig


def _extract_json(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3:
            candidate = "\n".join(lines[1:-1]).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(candidate[start : end + 1])


class RebootRestClient:
    def __init__(self, config: ServerConfig):
        self._config = config

    def wait_for_health(self, timeout_seconds: int | None = None) -> None:
        timeout_seconds = timeout_seconds or self._config.startup_timeout_seconds
        deadline = time.monotonic() + timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                payload = self._request("GET", self._config.health_path)
                if payload.get("status") == "ok":
                    return
            except Exception as exc:  # pragma: no cover
                last_error = exc
            time.sleep(1.0)
        raise RuntimeError(
            f"REBOOT server did not become healthy within {timeout_seconds}s."
        ) from last_error

    def start_ingest(
        self,
        repo_path: str,
        *,
        incremental: bool = False,
        verbose: bool = False,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            self._config.ingest_path,
            {
                "repo_path": repo_path,
                "incremental": incremental,
                "verbose": verbose,
            },
        )

    def get_ingest_status(self, job_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            self._config.ingest_status_path_template.format(job_id=job_id),
        )

    def cancel_ingest(self, job_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            self._config.ingest_cancel_path_template.format(job_id=job_id),
        )

    def query(self, query: str, file_context: str | None = None) -> dict[str, Any]:
        return self._request(
            "POST",
            self._config.query_path,
            {"query": query, "file_context": file_context},
        )

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = parse.urljoin(self._config.base_url.rstrip("/") + "/", path.lstrip("/"))
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(url=url, method=method, data=data, headers=headers)
        try:
            with request.urlopen(req, timeout=self._config.request_timeout_seconds) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {url} failed with {exc.code}: {body}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"{method} {url} failed: {exc.reason}") from exc


class OpenAIJsonClient:
    def __init__(self, config: OpenAIModelConfig):
        from openai import OpenAI

        api_key = os.getenv(config.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"Missing API key for eval LLM client. Set ${config.api_key_env}."
            )
        base_url = os.getenv(config.base_url_env)
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._config = config
        self._client = OpenAI(**kwargs)

    def complete_json(self, system_prompt: str, user_prompt: str) -> LLMTrace:
        response = self._client.chat.completions.create(
            model=self._config.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self._config.temperature,
            max_completion_tokens=self._config.max_tokens,
            timeout=self._config.timeout_seconds,
        )
        raw_text = response.choices[0].message.content or ""
        parsed = _extract_json(raw_text)
        return LLMTrace(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            raw_response=raw_text,
            parsed_json=parsed,
        )
