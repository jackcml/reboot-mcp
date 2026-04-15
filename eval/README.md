# REBOOT Eval Harness

This harness evaluates how much useful context REBOOT surfaces for issue-solving tasks before we measure full agent execution quality.

## What it does

For each repo in a manifest, the harness:

1. Starts an isolated eval environment.
2. Materializes the repo into a harness-owned checkout.
3. Calls `POST /ingest`, polls `/ingest-status/{job_id}`, and optionally cancels on timeout.
4. For each issue:
   - gives an LLM a lightweight snapshot of the repo plus the problem statement
   - asks it for the first retrieval query an agent would send to REBOOT
   - sends that query to `POST /query`
   - asks an LLM judge whether the returned ranking would have helped solve the issue, using the gold patch as ground truth
5. Writes structured artifacts for later analysis.

## Layout

- `eval/docker-compose.eval.yml`
  A dedicated Neo4j stack on ports `17474/17687` so eval resets do not touch the default dev volume.
- `eval/models.py`
  Manifest, runtime, and artifact schemas.
- `eval/environment.py`
  Docker compose and REBOOT server lifecycle management.
- `eval/repo.py`
  Repo checkout and repository snapshot generation.
- `eval/runner.py`
  Main orchestration, LLM query generation, judging, and CLI entrypoint.

## Running

From the repo root:

```powershell
python -m eval --config eval/examples/sample_manifest.json
```

The harness expects a working OpenAI-compatible key in `$OPENAI_API_KEY`. By default it also uses `$OPENAI_BASE_URL`.

Artifacts are written under `eval/artifacts/<run_id>/`:

- `manifest.json`
- `summary.json`
- `events.jsonl`
- `repos/<repo_id>.json`
- `cases/<repo_id>/<case_id>.json`
- `reboot-server.log`

## Manifest format

Use JSON for now. Relative paths are resolved as follows:

- `solution_patch_path` and `local_path` are resolved relative to the manifest file.
- workspace and environment paths are resolved relative to the project root.

Top-level fields:

- `name`
- `description`
- `environment`
- `server`
- `llm`
- `workspace`
- `snapshot`
- `repos`

Repo fields:

- `id`
- exactly one of `clone_url` or `local_path`
- optional `checkout`
- `cases`

Case fields:

- `id`
- `problem_statement`
- one of `solution_patch` or `solution_patch_path`
- optional `query_override`
- optional `file_context`
- optional `metadata`

## Important assumption

Search results are not repo-scoped today. Because of that, the harness resets the isolated eval Neo4j stack between repos by default so rankings are not contaminated by previous ingests.

## Future extension

The harness is intentionally split into environment control, repo materialization, query generation, query execution, and judging. That makes it straightforward to replace the current LLM query generator with a headless OpenCode controller later without rewriting the rest of the pipeline.

