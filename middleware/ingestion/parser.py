import os
from dataclasses import dataclass, field
from pathlib import Path

from graphiti_core import Graphiti
from tree_sitter_languages import get_language, get_parser

from middleware.graph.client import ENTITY_TYPES, add_code_episode

SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
}

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".eggs", "dist", "build"}


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


class CodeParser:
    def _get_parser(self, language: str):
        parser = get_parser(language)
        return parser, get_language(language)

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
                    docstring = expr.text.decode("utf-8").strip("\"'")

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
                        docstring = expr.text.decode("utf-8").strip("\"'")

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
        parser, _ = self._get_parser(language)
        tree = parser.parse(source_bytes)

        if language == "python":
            return self._extract_python_nodes(tree, source_bytes, file_path)
        elif language in ("javascript", "typescript"):
            return self._extract_js_nodes(tree, source_bytes, file_path)
        return []

    def parse_repository(self, repo_path: str) -> list[CodeNode]:
        all_nodes: list[CodeNode] = []
        for dirpath, dirnames, filenames in os.walk(repo_path):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fname in filenames:
                full_path = os.path.join(dirpath, fname)
                all_nodes.extend(self.parse_file(full_path))
        return all_nodes


async def ingest_to_graph(
    repo_path: str,
    group_id: str | None = None,
) -> int:
    parser = CodeParser()
    code_nodes = parser.parse_repository(repo_path)
    count = 0
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

        await add_code_episode(
            name=f"{cn.kind}:{cn.name}",
            body="\n".join(body_parts),
            source_description=f"{cn.language} {cn.kind} from {cn.file_path}",
            group_id=group_id,
        )
        count += 1
    return count
