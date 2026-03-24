import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import tree_sitter_javascript as ts_js
import tree_sitter_python as ts_py
from graphiti_core.nodes import EpisodeType
from graphiti_core.utils.bulk_utils import RawEpisode
from tree_sitter import Language, Parser

from middleware.graph.client import (
    ENTITY_TYPES,
    add_code_episode,
    add_code_episodes_bulk,
    get_graphiti_client,
    is_graph_empty,
)

SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
}

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".eggs", "dist", "build"}

INGEST_STATE_FILE = Path(__file__).parent / "ingest_state.json"
INGEST_JOBS: dict[str, dict] = {}


@dataclass
class CodeNode:
    kind: str  # "function", "class", "module"
    name: str
    language: str
    file_path: str
    start_line: int
    end_line: int
    source: str
    signature: str = ""
    docstring: str = ""
    methods: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)


LANGUAGES: dict[str, Language] = {
    "python": Language(ts_py.language()),
    "javascript": Language(ts_js.language()),
    "typescript": Language(ts_js.language()),
}


def _load_ingest_state() -> dict:
    if INGEST_STATE_FILE.exists():
        try:
            return json.loads(INGEST_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_ingest_state(state: dict) -> None:
    INGEST_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _get_repo_state(repo_path: str) -> dict:
    state = _load_ingest_state()
    return state.get(repo_path, {"files": {}, "last_ingest": None})


def _set_repo_state(repo_path: str, files_state: dict) -> None:
    state = _load_ingest_state()
    state[repo_path] = {"files": files_state, "last_ingest": datetime.now(timezone.utc).isoformat()}
    _save_ingest_state(state)


def _init_job(job_id: str, repo_path: str, incremental: bool) -> None:
    INGEST_JOBS[job_id] = {
        "job_id": job_id,
        "repo_path": repo_path,
        "incremental": incremental,
        "stage": "started",
        "total_files": 0,
        "processed_files": 0,
        "total_nodes": 0,
        "processed_nodes": 0,
        "episodes_added": 0,
        "start_time": datetime.now(timezone.utc).isoformat(),
        "end_time": None,
        "last_update": datetime.now(timezone.utc).isoformat(),
        "error": None,
        "message": "Ingest job started",
    }


def _update_job(job_id: str, **kwargs) -> None:
    job = INGEST_JOBS.get(job_id)
    if not job:
        return
    for k, v in kwargs.items():
        job[k] = v
    job["last_update"] = datetime.now(timezone.utc).isoformat()


def get_ingest_job_status(job_id: str) -> Optional[dict]:
    return INGEST_JOBS.get(job_id)


class CodeParser:
    def _get_parser(self, language: str) -> Parser:
        lang = LANGUAGES[language]
        parser = Parser(lang)
        return parser

    def _extract_python_nodes(self, tree, source_bytes: bytes, file_path: str) -> list[CodeNode]:
        nodes: list[CodeNode] = []
        root = tree.root_node

        for child in root.children:
            if child.type == "function_definition":
                nodes.append(self._parse_python_function(child, source_bytes, file_path))
            elif child.type == "class_definition":
                nodes.append(self._parse_python_class(child, source_bytes, file_path))
            elif child.type in ("import_statement", "import_from_statement"):
                pass  # collected at module level

        imports = [
            child.text.decode("utf-8")
            for child in root.children
            if child.type in ("import_statement", "import_from_statement")
        ]
        if imports:
            nodes.append(
                CodeNode(
                    kind="module",
                    name=file_path,
                    language="python",
                    file_path=file_path,
                    start_line=1,
                    end_line=root.end_point[0] + 1,
                    source="",
                    imports=imports,
                )
            )

        return nodes

    def _parse_python_function(self, node, source_bytes: bytes, file_path: str) -> CodeNode:
        name_node = node.child_by_field_name("name")
        name = name_node.text.decode("utf-8") if name_node else "<unknown>"
        params_node = node.child_by_field_name("parameters")
        params = params_node.text.decode("utf-8") if params_node else "()"
        body = node.child_by_field_name("body")
        docstring = ""
        if body and body.children:
            first = body.children[0]
            if first.type == "expression_statement" and first.children:
                expr = first.children[0]
                if expr.type == "string":
                    docstring = expr.text.decode("utf-8").strip('"\'')

        return CodeNode(
            kind="function",
            name=name,
            language="python",
            file_path=file_path,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            source=node.text.decode("utf-8"),
            signature=f"def {name}{params}",
            docstring=docstring,
        )

    def _parse_python_class(self, node, source_bytes: bytes, file_path: str) -> CodeNode:
        name_node = node.child_by_field_name("name")
        name = name_node.text.decode("utf-8") if name_node else "<unknown>"
        body = node.child_by_field_name("body")
        methods: list[str] = []
        docstring = ""
        if body:
            for child in body.children:
                if child.type == "function_definition":
                    m_name = child.child_by_field_name("name")
                    if m_name:
                        methods.append(m_name.text.decode("utf-8"))
            if body.children:
                first = body.children[0]
                if first.type == "expression_statement" and first.children:
                    expr = first.children[0]
                    if expr.type == "string":
                        docstring = expr.text.decode("utf-8").strip('"\'')

        return CodeNode(
            kind="class",
            name=name,
            language="python",
            file_path=file_path,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            source=node.text.decode("utf-8"),
            methods=methods,
            docstring=docstring,
        )

    def _extract_js_nodes(self, tree, source_bytes: bytes, file_path: str) -> list[CodeNode]:
        nodes: list[CodeNode] = []
        root = tree.root_node

        for child in root.children:
            if child.type == "function_declaration":
                nodes.append(self._parse_js_function(child, source_bytes, file_path))
            elif child.type == "class_declaration":
                nodes.append(self._parse_js_class(child, source_bytes, file_path))
            elif child.type == "export_statement":
                for sub in child.children:
                    if sub.type == "function_declaration":
                        nodes.append(self._parse_js_function(sub, source_bytes, file_path))
                    elif sub.type == "class_declaration":
                        nodes.append(self._parse_js_class(sub, source_bytes, file_path))

        return nodes

    def _parse_js_function(self, node, source_bytes: bytes, file_path: str) -> CodeNode:
        name_node = node.child_by_field_name("name")
        name = name_node.text.decode("utf-8") if name_node else "<unknown>"
        params_node = node.child_by_field_name("parameters")
        params = params_node.text.decode("utf-8") if params_node else "()"

        return CodeNode(
            kind="function",
            name=name,
            language="javascript",
            file_path=file_path,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            source=node.text.decode("utf-8"),
            signature=f"function {name}{params}",
        )

    def _parse_js_class(self, node, source_bytes: bytes, file_path: str) -> CodeNode:
        name_node = node.child_by_field_name("name")
        name = name_node.text.decode("utf-8") if name_node else "<unknown>"
        body = node.child_by_field_name("body")
        methods: list[str] = []
        if body:
            for child in body.children:
                if child.type == "method_definition":
                    m_name = child.child_by_field_name("name")
                    if m_name:
                        methods.append(m_name.text.decode("utf-8"))

        return CodeNode(
            kind="class",
            name=name,
            language="javascript",
            file_path=file_path,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            source=node.text.decode("utf-8"),
            methods=methods,
        )

    def parse_file(self, file_path: str) -> list[CodeNode]:
        ext = Path(file_path).suffix
        language = SUPPORTED_EXTENSIONS.get(ext)
        if language is None:
            return []

        source_bytes = Path(file_path).read_bytes()
        parser = self._get_parser(language)
        tree = parser.parse(source_bytes)

        if language == "python":
            return self._extract_python_nodes(tree, source_bytes, file_path)
        elif language in ("javascript", "typescript"):
            return self._extract_js_nodes(tree, source_bytes, file_path)
        return []

    def parse_repository(
        self,
        repo_path: str,
        incremental: bool = False,
        previous_file_state: Optional[dict] = None,
        job_id: Optional[str] = None,
    ) -> tuple[list[CodeNode], dict[str, float], list[str]]:
        all_nodes: list[CodeNode] = []
        current_file_state: dict[str, float] = {}
        removed_files: list[str] = []

        if previous_file_state is None:
            previous_file_state = {}

        for dirpath, dirnames, filenames in os.walk(repo_path):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fname in filenames:
                full_path = os.path.join(dirpath, fname)
                ext = Path(full_path).suffix
                if ext not in SUPPORTED_EXTENSIONS:
                    continue

                try:
                    mtime = os.path.getmtime(full_path)
                except OSError:
                    continue

                current_file_state[full_path] = mtime
                if incremental and previous_file_state.get(full_path) == mtime:
                    continue

                if job_id:
                    _update_job(job_id, processed_files=len(current_file_state), total_files=len(current_file_state))

                all_nodes.extend(self.parse_file(full_path))

        if incremental:
            removed_files = [f for f in previous_file_state if f not in current_file_state]

        return all_nodes, current_file_state, removed_files


async def ingest_to_graph(
    repo_path: str,
    group_id: str | None = None,
    incremental: bool = False,
    use_bulk_first: bool = True,
    job_id: Optional[str] = None,
) -> dict:
    if job_id:
        _init_job(job_id, repo_path, incremental)

    start_time = datetime.now(timezone.utc)
    try:
        repo_state = _get_repo_state(repo_path)
        previous_file_state = repo_state.get("files", {})

        if job_id:
            _update_job(job_id, stage="scanning", message="Scanning repository for changed files")

        parser = CodeParser()
        code_nodes, current_file_state, removed_files = parser.parse_repository(
            repo_path,
            incremental=incremental,
            previous_file_state=previous_file_state if incremental else None,
            job_id=job_id,
        )

        num_files = len(current_file_state)
        num_removed = len(removed_files)

        if job_id:
            _update_job(
                job_id,
                stage="ingesting",
                total_files=num_files,
                processed_files=0,
                total_nodes=len(code_nodes),
                processed_nodes=0,
                message=f"Ingesting {len(code_nodes)} nodes (removed {num_removed} files)",
            )

        episodes_added = 0
        graph_empty = await is_graph_empty()

        if use_bulk_first and not incremental and graph_empty:
            if job_id:
                _update_job(job_id, message="Using bulk ingestion path")

            episodes_added = await add_code_episodes_bulk(code_nodes, group_id=group_id)

            if job_id:
                _update_job(job_id, processed_nodes=episodes_added)
        else:
            if job_id:
                _update_job(job_id, message="Using incremental ingestion path")

            batch_size = 16
            tasks = []
            for cn in code_nodes:
                body_parts = [f"kind: {cn.kind}", f"name: {cn.name}", f"file: {cn.file_path}"]
                if cn.signature:
                    body_parts.append(f"signature: {cn.signature}")
                if cn.docstring:
                    body_parts.append(f"docstring: {cn.docstring}")
                if cn.methods:
                    body_parts.append(f"methods: {', '.join(cn.methods)}")
                if cn.imports:
                    body_parts.append(f"imports: {'; '.join(cn.imports)}")
                body_parts.append(f"lines: {cn.start_line}-{cn.end_line}")
                if cn.source:
                    body_parts.append(f"source:\n{cn.source}")

                task = add_code_episode(
                    name=f"{cn.kind}:{cn.name}",
                    body="\n".join(body_parts),
                    source_description=f"{cn.language} {cn.kind} from {cn.file_path}",
                    group_id=group_id,
                )
                tasks.append(task)

                if len(tasks) >= batch_size:
                    await asyncio.gather(*tasks)
                    episodes_added += len(tasks)
                    if job_id:
                        _update_job(job_id, processed_nodes=episodes_added)
                    tasks = []

            if tasks:
                await asyncio.gather(*tasks)
                episodes_added += len(tasks)
                if job_id:
                    _update_job(job_id, processed_nodes=episodes_added)

        _set_repo_state(repo_path, current_file_state)

        end_time = datetime.now(timezone.utc)
        if job_id:
            _update_job(
                job_id,
                stage="completed",
                episodes_added=episodes_added,
                end_time=end_time.isoformat(),
                message=f"Ingest finished in {(end_time - start_time).total_seconds():.2f}s",
            )

        return {
            "status": "ok",
            "episodes_added": episodes_added,
            "files_scanned": num_files,
            "files_removed": num_removed,
            "duration_seconds": (end_time - start_time).total_seconds(),
        }
    except Exception as exc:
        end_time = datetime.now(timezone.utc)
        if job_id:
            _update_job(
                job_id,
                stage="failed",
                error=str(exc),
                end_time=end_time.isoformat(),
                message=f"Ingest failed: {str(exc)}",
            )
        raise
