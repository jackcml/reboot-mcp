from datasets import load_dataset
from pathlib import Path
import json

REPOS = [
    "astropy/astropy"
]

rows = load_dataset("SWE-bench/SWE-bench_Lite", split="test")
selected = [r for r in rows if r["repo"] in REPOS]
found = {r["repo"] for r in selected}
missing = [i for i in REPOS if i not in found]
if missing:
    raise SystemExit(f"Missing repos: {missing}")

out_dir = Path("eval/generated/swebench-lite-small")
patch_dir = out_dir / "patches"
patch_dir.mkdir(parents=True, exist_ok=True)

repos_data = []
for repo in REPOS:
    cases_data = []
    cases = [r for r in selected if r["repo"] == repo]
    for case in cases:
        patch_file = patch_dir / f"{case['instance_id']}.diff"
        patch_file.write_text(case["patch"], encoding="utf-8")

        cases_data.append({
            "id": case["instance_id"],
            "title": case["instance_id"],
            "problem_statement": case["problem_statement"],
            "solution_patch_path": f"patches/{case['instance_id']}.diff",
            "metadata": {
                "swebench_repo": case["repo"],
                "created_at": case["created_at"],
                "version": case["version"],
            },
        })


    any_case = cases[0]
    repos_data.append({
        "id": any_case["repo"],
        "clone_url": f"https://github.com/{any_case['repo']}.git",
        "checkout": any_case["base_commit"],
        "cases": cases_data,
    })

manifest = {
    "name": "swebench-lite-small",
    "description": "Small SWE-Bench-Lite subset for REBOOT retrieval eval.",
    "environment": {
        "docker": {
            "enabled": True,
            "compose_file": "eval/docker-compose.eval.yml",
            "compose_project_name": "reboot-eval",
            "project_dir": ".",
            "services": ["neo4j"],
            "reset_between_repos": True,
            "shutdown_on_exit": True,
        },
        "server_process": {
            "auto_start": True,
            "cwd": ".",
            "env": {
                "NEO4J_URI": "bolt://127.0.0.1:17687",
                "NEO4J_USER": "neo4j",
                "NEO4J_PASSWORD": "reboot_dev",
                "SERVER_PORT": "8010",
            },
        },
    },
    "server": {
        "base_url": "http://127.0.0.1:8010",
        "ingest_timeout_seconds": 600,
        "ingest_poll_interval_seconds": 5.0,
        "cancel_ingest_on_timeout": True,
    },
    "llm": {
        "query_generator": {"model": "gpt-5-mini", "max_tokens": 250},
        "judge": {"model": "gpt-5-mini", "max_tokens": 1200},
    },
    "workspace": {
        "clone_root": "eval/workspaces/repos",
        "artifact_root": "eval/artifacts",
    },
    "snapshot": {
        "max_files": 400,
        "max_tree_chars": 12000,
        "max_file_chars": 4000,
    },
    "repos": repos_data,
}

(out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(out_dir / "manifest.json")
