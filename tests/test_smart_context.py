"""Tests for smart context targeting: traceback parsing and scope extraction."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from council.smart_context import (
    FileRef,
    extract_file_refs,
    extract_scope,
    resolve_ref,
)

# ---------------------------------------------------------------------------
# extract_file_refs — traceback / log parsing
# ---------------------------------------------------------------------------

class TestExtractFileRefsPython:
    def test_python_traceback(self):
        text = '''Traceback (most recent call last):
  File "src/auth/login.py", line 42, in authenticate
    token = generate_token(user)
  File "src/auth/tokens.py", line 15, in generate_token
    return jwt.encode(payload, SECRET)
jwt.exceptions.InvalidKeyError: key is empty'''
        refs = extract_file_refs(text)
        paths = [(r.path, r.line) for r in refs]
        assert ("src/auth/login.py", 42) in paths
        assert ("src/auth/tokens.py", 15) in paths

    def test_python_traceback_no_line(self):
        text = 'File "src/config.py", in load_config'
        refs = extract_file_refs(text)
        assert any(r.path == "src/config.py" for r in refs)

    def test_python_single_frame(self):
        text = '  File "app.py", line 7, in main'
        refs = extract_file_refs(text)
        assert refs[0].path == "app.py"
        assert refs[0].line == 7


class TestExtractFileRefsNode:
    def test_node_stack(self):
        text = '''TypeError: Cannot read property 'id' of undefined
    at Object.<anonymous> (/home/user/app/server.js:42:5)
    at Module._compile (internal/modules/cjs/loader.js:999:30)'''
        refs = extract_file_refs(text)
        paths = [r.path for r in refs]
        assert any("server.js" in p for p in paths)

    def test_node_simple(self):
        text = "at /project/src/handler.ts:15:3"
        refs = extract_file_refs(text)
        assert any(r.path.endswith("handler.ts") and r.line == 15 for r in refs)


class TestExtractFileRefsGo:
    def test_go_panic(self):
        text = '''goroutine 1 [running]:
main.main()
	/home/user/project/main.go:42 +0x1a2
runtime.main()
	/usr/local/go/src/runtime/proc.go:250 +0x1c0'''
        refs = extract_file_refs(text)
        paths = [(r.path, r.line) for r in refs]
        assert any("main.go" in p and ln == 42 for p, ln in paths)


class TestExtractFileRefsRust:
    def test_rust_panic(self):
        text = "thread 'main' panicked at 'index out of bounds', src/main.rs:15:5"
        refs = extract_file_refs(text)
        assert any(r.path == "src/main.rs" and r.line == 15 for r in refs)


class TestExtractFileRefsJava:
    def test_java_stack(self):
        text = '''Exception in thread "main"
    at com.example.App.main(App.java:10)
    at com.example.Service.run(Service.java:45)'''
        refs = extract_file_refs(text)
        paths = [(r.path, r.line) for r in refs]
        assert ("App.java", 10) in paths
        assert ("Service.java", 45) in paths


class TestExtractFileRefsRuby:
    def test_ruby_stack(self):
        text = "from /home/user/app.rb:12:in `method'"
        refs = extract_file_refs(text)
        assert any(r.path.endswith("app.rb") and r.line == 12 for r in refs)


class TestExtractFileRefsGeneric:
    def test_generic_file_line(self):
        text = "Error in src/utils/helpers.py:88: undefined variable"
        refs = extract_file_refs(text)
        assert any(r.path == "src/utils/helpers.py" and r.line == 88 for r in refs)

    def test_generic_file_line_col(self):
        text = "src/main.ts:10:5 - error TS2304"
        refs = extract_file_refs(text)
        assert any(r.path == "src/main.ts" and r.line == 10 for r in refs)

    def test_deduplication(self):
        text = '''  File "app.py", line 10, in main
  File "app.py", line 10, in main'''
        refs = extract_file_refs(text)
        assert len([r for r in refs if r.path == "app.py" and r.line == 10]) == 1

    def test_no_refs(self):
        text = "Just a plain error message with no file references."
        refs = extract_file_refs(text)
        assert refs == []

    def test_mixed_languages(self):
        text = '''Error at src/foo.py:10
Also see src/bar.go:20
And check src/baz.ts:30:5'''
        refs = extract_file_refs(text)
        assert len(refs) >= 3


# ---------------------------------------------------------------------------
# resolve_ref
# ---------------------------------------------------------------------------

class TestResolveRef:
    def test_resolves_absolute_path(self, tmp_path: Path):
        f = tmp_path / "test.py"
        f.write_text("x = 1")
        ref = FileRef(path=str(f), line=1)
        assert resolve_ref(ref, None) == f

    def test_resolves_relative_to_repo_root(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        f = src / "app.py"
        f.write_text("print(1)")
        ref = FileRef(path="src/app.py", line=1)
        assert resolve_ref(ref, tmp_path) == f

    def test_returns_none_for_missing(self, tmp_path: Path):
        ref = FileRef(path="nonexistent.py", line=1)
        assert resolve_ref(ref, tmp_path) is None


# ---------------------------------------------------------------------------
# extract_scope — enclosing function/class extraction
# ---------------------------------------------------------------------------

class TestExtractScopePython:
    def test_extracts_enclosing_function(self):
        source = '''\
import os

def helper():
    pass

def authenticate(user):
    token = generate_token(user)
    if not token:
        raise ValueError("no token")
    return token

def other():
    pass
'''
        # Line 7 is inside authenticate()
        snippet, start, end = extract_scope(source, 7)
        assert "def authenticate" in snippet
        assert "generate_token" in snippet
        # Should NOT include def other()
        assert "def other" not in snippet

    def test_extracts_class_method(self):
        source = '''\
class UserService:
    def __init__(self, db):
        self.db = db

    def get_user(self, user_id):
        result = self.db.query(user_id)
        if result is None:
            raise KeyError(f"User {user_id} not found")
        return result

    def delete_user(self, user_id):
        self.db.delete(user_id)
'''
        # Line 7 is inside get_user()
        snippet, start, end = extract_scope(source, 7)
        assert "def get_user" in snippet
        assert "result" in snippet

    def test_includes_decorator(self):
        source = '''\
from functools import wraps

@wraps
@login_required
def protected_view(request):
    return render(request, "secret.html")

def public_view(request):
    return render(request, "public.html")
'''
        # Line 6 is inside protected_view()
        snippet, start, end = extract_scope(source, 6)
        assert "@login_required" in snippet
        assert "def protected_view" in snippet

    def test_falls_back_to_window_if_no_scope(self):
        source = "x = 1\ny = 2\nz = 3\nw = 4\na = 5\n"
        # No function/class, should still return something around line 3.
        snippet, start, end = extract_scope(source, 3)
        assert "z = 3" in snippet


class TestExtractScopeJS:
    def test_js_function(self):
        source = '''\
const helper = () => {};

function processOrder(order) {
    const total = calculateTotal(order.items);
    if (total > 1000) {
        applyDiscount(order);
    }
    return total;
}

function otherFunc() {
    return null;
}
'''
        # Line 5 is inside processOrder()
        snippet, start, end = extract_scope(source, 5)
        assert "function processOrder" in snippet
        assert "calculateTotal" in snippet


class TestExtractScopeGo:
    def test_go_func(self):
        source = '''\
package main

import "fmt"

func helper() {}

func main() {
	fmt.Println("hello")
	result := compute(42)
	fmt.Println(result)
}

func compute(x int) int {
	return x * 2
}
'''
        # Line 9 is inside main()
        snippet, start, end = extract_scope(source, 9)
        assert "func main()" in snippet
        assert "compute(42)" in snippet


class TestExtractScopeRust:
    def test_rust_fn(self):
        source = '''\
use std::io;

fn helper() {}

pub fn process(input: &str) -> Result<String, io::Error> {
    let trimmed = input.trim();
    if trimmed.is_empty() {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "empty"));
    }
    Ok(trimmed.to_string())
}

fn other() {}
'''
        # Line 7 is inside process()
        snippet, start, end = extract_scope(source, 7)
        assert "pub fn process" in snippet
        assert "trimmed" in snippet


class TestExtractScopeEdgeCases:
    def test_line_beyond_file(self):
        source = "x = 1\ny = 2\n"
        snippet, start, end = extract_scope(source, 999)
        assert snippet  # Should return something

    def test_line_zero(self):
        source = "x = 1\ny = 2\n"
        snippet, start, end = extract_scope(source, 0)
        assert snippet

    def test_empty_source(self):
        snippet, start, end = extract_scope("", 1)
        assert snippet == ""


# ---------------------------------------------------------------------------
# Integration: smart context in gather_context
# ---------------------------------------------------------------------------

class TestSmartContextIntegration:
    def test_auto_includes_from_traceback(self, tmp_path: Path):
        """Smart context should auto-include files referenced in task traceback."""
        # Create the file that the traceback references.
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        auth_file = src_dir / "auth.py"
        auth_file.write_text(
            "def login(user):\n"
            "    token = get_token(user)\n"
            "    return token\n"
            "\n"
            "def logout(user):\n"
            "    pass\n",
            encoding="utf-8",
        )

        from council.context import gather_context
        from council.types import ContextMode, Mode, RunOptions

        task = (
            'Fix this error:\n'
            'Traceback (most recent call last):\n'
            '  File "src/auth.py", line 2, in login\n'
            '    token = get_token(user)\n'
            'NameError: name \'get_token\' is not defined'
        )

        opts = RunOptions(
            mode=Mode.FIX,
            task=task,
            context_mode=ContextMode.AUTO,
            smart_context=True,
        )

        with (
            patch("council.context._run_git", return_value=None),
            patch("council.smart_context.resolve_ref") as mock_resolve,
        ):
            mock_resolve.return_value = auth_file
            ctx = gather_context(opts, repo_root=tmp_path)

        # The auth.py file should be included in context.
        file_sources = [s for s in ctx.sources if s.source_type == "file" and s.path]
        assert any("auth.py" in (s.path or "") for s in file_sources)

        # The scope snippet should reference the login function, not the whole file.
        assert "def login" in ctx.text
        # Marker showing it's a scope extract with line range.
        assert "around line 2" in ctx.text

    def test_skips_when_disabled(self, tmp_path: Path):
        """Without --smart-context, traceback files should NOT be auto-included."""
        from council.context import gather_context
        from council.types import ContextMode, Mode, RunOptions

        task = '  File "src/missing.py", line 5, in broken\n    foo()'

        opts = RunOptions(
            mode=Mode.FIX,
            task=task,
            context_mode=ContextMode.AUTO,
            smart_context=False,
        )

        with patch("council.context._run_git", return_value=None):
            ctx = gather_context(opts, repo_root=tmp_path)

        # No file sources should reference missing.py.
        file_sources = [s for s in ctx.sources if s.source_type == "file"]
        assert not any("missing.py" in (s.path or "") for s in file_sources)

    def test_deduplicates_with_explicit_includes(self, tmp_path: Path):
        """If a file is already --include'd, smart context shouldn't add it again."""
        f = tmp_path / "app.py"
        f.write_text("def main():\n    pass\n", encoding="utf-8")

        from council.context import gather_context
        from council.types import ContextMode, Mode, RunOptions

        task = 'Error in app.py:1'

        opts = RunOptions(
            mode=Mode.FIX,
            task=task,
            context_mode=ContextMode.AUTO,
            smart_context=True,
            include_paths=[str(f)],
        )

        with patch("council.context._run_git", return_value=None):
            ctx = gather_context(opts, repo_root=tmp_path)

        # app.py should appear only once.
        file_paths = [s.path for s in ctx.sources if s.source_type == "file" and s.path and "app.py" in s.path]
        assert len(file_paths) == 1

    def test_excludes_sensitive_files(self, tmp_path: Path):
        """Smart context should respect exclude patterns."""
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET=abc")

        from council.context import gather_context
        from council.types import ContextMode, Mode, RunOptions

        task = '  File ".env", line 1, in <module>'

        opts = RunOptions(
            mode=Mode.FIX,
            task=task,
            context_mode=ContextMode.AUTO,
            smart_context=True,
        )

        with (
            patch("council.context._run_git", return_value=None),
            patch("council.smart_context.resolve_ref", return_value=env_file),
        ):
            ctx = gather_context(opts, repo_root=tmp_path)

        # .env should be excluded.
        excluded = [s for s in ctx.sources if s.excluded and s.path and ".env" in s.path]
        assert len(excluded) >= 1
