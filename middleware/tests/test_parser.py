import textwrap
import tempfile
import os
import pytest

from middleware.ingestion.parser import CodeParser


@pytest.fixture
def parser():
    return CodeParser()


def make_tempfile(content: str, suffix: str) -> str:
    f = tempfile.NamedTemporaryFile(suffix=suffix, mode="w", delete=False)
    f.write(textwrap.dedent(content))
    f.close()
    return f.name


# ─── Python tests ─────────────────────────────────────────────────────────────

def test_python_top_level_function(parser):
    path = make_tempfile("""
        def greet(name):
            return f"hello {name}"
    """, ".py")
    nodes = parser.parse_file(path)
    os.unlink(path)

    names = [n.name for n in nodes]
    assert "greet" in names
    fn = next(n for n in nodes if n.name == "greet")
    assert fn.kind == "function"
    assert fn.signature == "def greet(name)"


def test_python_decorated_function(parser):
    path = make_tempfile("""
        @app.route("/")
        def index():
            pass

        @staticmethod
        def helper(x):
            return x
    """, ".py")
    nodes = parser.parse_file(path)
    os.unlink(path)

    names = [n.name for n in nodes]
    assert "index" in names, "decorated function should be captured"
    assert "helper" in names, "decorated function should be captured"


def test_python_class_and_methods_are_separate_nodes(parser):
    path = make_tempfile("""
        class FeedbackLogger:
            def init_db(self):
                pass

            def log_feedback(self, query_id, signal):
                pass

            def get_confidence(self, node_id):
                return 1.0
    """, ".py")
    nodes = parser.parse_file(path)
    os.unlink(path)

    kinds = {n.name: n.kind for n in nodes}
    assert "FeedbackLogger" in kinds
    assert kinds["FeedbackLogger"] == "class"
    assert "init_db" in kinds, "class methods should be individual nodes"
    assert "log_feedback" in kinds
    assert "get_confidence" in kinds
    assert kinds["init_db"] == "function"


def test_python_class_node_lists_method_names(parser):
    path = make_tempfile("""
        class MyClass:
            def alpha(self): pass
            def beta(self): pass
    """, ".py")
    nodes = parser.parse_file(path)
    os.unlink(path)

    class_node = next(n for n in nodes if n.kind == "class")
    assert "alpha" in class_node.methods
    assert "beta" in class_node.methods


def test_python_function_docstring(parser):
    path = make_tempfile('''
        def my_func():
            """This is the docstring."""
            pass
    ''', ".py")
    nodes = parser.parse_file(path)
    os.unlink(path)

    fn = next(n for n in nodes if n.name == "my_func")
    assert "docstring" in fn.docstring.lower() or "This is" in fn.docstring


def test_python_module_imports(parser):
    path = make_tempfile("""
        import os
        from pathlib import Path

        def foo(): pass
    """, ".py")
    nodes = parser.parse_file(path)
    os.unlink(path)

    module_nodes = [n for n in nodes if n.kind == "module"]
    assert len(module_nodes) == 1
    joined = " ".join(module_nodes[0].imports)
    assert "os" in joined
    assert "Path" in joined


def test_python_unsupported_extension_returns_empty(parser):
    path = make_tempfile("hello world", ".txt")
    nodes = parser.parse_file(path)
    os.unlink(path)
    assert nodes == []


# ─── JavaScript tests ──────────────────────────────────────────────────────────

def test_js_function_declaration(parser):
    path = make_tempfile("""
        function greet(name) {
            return "hello " + name;
        }
    """, ".js")
    nodes = parser.parse_file(path)
    os.unlink(path)

    names = [n.name for n in nodes]
    assert "greet" in names
    fn = next(n for n in nodes if n.name == "greet")
    assert fn.kind == "function"


def test_js_arrow_function(parser):
    path = make_tempfile("""
        const add = (a, b) => a + b;
        const noop = () => {};
    """, ".js")
    nodes = parser.parse_file(path)
    os.unlink(path)

    names = [n.name for n in nodes]
    assert "add" in names, "arrow function should be captured"
    assert "noop" in names, "arrow function should be captured"


def test_js_function_expression(parser):
    path = make_tempfile("""
        const multiply = function(a, b) {
            return a * b;
        };
    """, ".js")
    nodes = parser.parse_file(path)
    os.unlink(path)

    names = [n.name for n in nodes]
    assert "multiply" in names, "function expression should be captured"


def test_js_exported_arrow_function(parser):
    path = make_tempfile("""
        export const handler = async (req, res) => {
            res.json({});
        };
    """, ".js")
    nodes = parser.parse_file(path)
    os.unlink(path)

    names = [n.name for n in nodes]
    assert "handler" in names, "exported arrow function should be captured"


def test_js_exported_function_declaration(parser):
    path = make_tempfile("""
        export function fetchData(url) {
            return fetch(url);
        }
    """, ".js")
    nodes = parser.parse_file(path)
    os.unlink(path)

    names = [n.name for n in nodes]
    assert "fetchData" in names


def test_js_class(parser):
    path = make_tempfile("""
        class Animal {
            speak() { return "..."; }
            move() { return "..."; }
        }
    """, ".js")
    nodes = parser.parse_file(path)
    os.unlink(path)

    class_node = next((n for n in nodes if n.kind == "class"), None)
    assert class_node is not None
    assert class_node.name == "Animal"
    assert "speak" in class_node.methods
    assert "move" in class_node.methods


# ─── Debug / visibility tests ─────────────────────────────────────────────────

def test_debug_python_file(parser, capsys):
    path = make_tempfile("""
        import os

        @app.route("/")
        def index():
            pass

        class MyService:
            def start(self): pass
            def stop(self): pass

        def standalone(): pass
    """, ".py")
    nodes = parser.parse_file(path)
    os.unlink(path)

    print(f"\n{'─'*60}")
    print(f"{'KIND':<12} {'NAME':<30} {'LINES':<12} {'SIGNATURE'}")
    print(f"{'─'*60}")
    for n in nodes:
        lines = f"{n.start_line}-{n.end_line}"
        print(f"{n.kind:<12} {n.name:<30} {lines:<12} {n.signature or '—'}")
        if n.kind == "class" and n.methods:
            print(f"{'':12} {'methods: ' + ', '.join(n.methods)}")
        if n.docstring:
            print(f"{'':12} docstring: {n.docstring[:60]}")
    print(f"{'─'*60}")
    print(f"Total: {len(nodes)} nodes")

    with capsys.disabled():
        pass  # keeps output visible even when pytest captures


def test_debug_js_file(parser, capsys):
    path = make_tempfile("""
        const arrowFn = (x) => x + 1;

        const asyncHandler = async (req, res) => {
            res.json({});
        };

        export const namedExport = (a, b) => a + b;

        export function regularExport(x) {
            return x;
        }

        function localFn() {}

        class MyClass {
            render() { return null; }
            componentDidMount() {}
        }
    """, ".js")
    nodes = parser.parse_file(path)
    os.unlink(path)

    print(f"\n{'─'*60}")
    print(f"{'KIND':<12} {'NAME':<30} {'LINES':<12} {'SIGNATURE'}")
    print(f"{'─'*60}")
    for n in nodes:
        lines = f"{n.start_line}-{n.end_line}"
        print(f"{n.kind:<12} {n.name:<30} {lines:<12} {n.signature or '—'}")
        if n.kind == "class" and n.methods:
            print(f"{'':12} methods: {', '.join(n.methods)}")
    print(f"{'─'*60}")
    print(f"Total: {len(nodes)} nodes")

    with capsys.disabled():
        pass


def test_debug_middleware_repo(parser, capsys):
    nodes, file_state, _ = parser.parse_repository("middleware")

    from collections import defaultdict
    by_file: dict = defaultdict(list)
    for n in nodes:
        key = n.file_path.split("middleware/")[-1]
        by_file[key].append(n)

    print(f"\n{'─'*60}")
    print(f"Parsed {len(file_state)} files, {len(nodes)} total nodes")
    print(f"{'─'*60}")
    for filepath, fnodes in sorted(by_file.items()):
        print(f"\n  {filepath}")
        for n in fnodes:
            print(f"    {n.kind:<10} {n.name}")
    print(f"{'─'*60}")

    with capsys.disabled():
        pass


# ─── Repository-level test ────────────────────────────────────────────────────

def test_parse_repository_finds_middleware_nodes(parser):
    # Parse the middleware directory itself — no ingestion needed
    nodes, file_state, removed = parser.parse_repository("middleware")

    assert len(nodes) > 0, "should find nodes in middleware/"
    assert len(file_state) > 0, "should track file mtimes"
    assert removed == [], "fresh parse should have no removed files"

    kinds = {n.kind for n in nodes}
    assert "function" in kinds
    assert "class" in kinds

    names = [n.name for n in nodes]
    # These are known to exist in the middleware codebase
    assert "FeedbackLogger" in names
    assert "QueryClassifier" in names
    assert "CodeParser" in names
