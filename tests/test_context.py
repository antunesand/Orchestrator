"""Tests for context gathering, truncation, and budget enforcement."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from council.context import (
    Section,
    _collect_changed_files,
    _enforce_budget,
    _is_binary,
    _matches_exclude,
    _truncate_content,
    gather_context,
)
from council.types import ContextMode, ContextSource, DiffScope, RunOptions, Mode


class TestExcludePatterns:
    def test_env_file(self):
        assert _matches_exclude(".env") is True

    def test_pem_file(self):
        assert _matches_exclude("server.pem") is True

    def test_key_file(self):
        assert _matches_exclude("private.key") is True

    def test_id_rsa(self):
        assert _matches_exclude("id_rsa") is True
        assert _matches_exclude("id_rsa.pub") is True

    def test_credentials(self):
        assert _matches_exclude("credentials.json") is True

    def test_node_modules(self):
        assert _matches_exclude("node_modules") is True

    def test_normal_file(self):
        assert _matches_exclude("main.py") is False
        assert _matches_exclude("README.md") is False

    # --- Issue 1: nested path exclusion ---
    def test_nested_node_modules(self):
        assert _matches_exclude("node_modules/react/index.js") is True

    def test_nested_git(self):
        assert _matches_exclude(".git/config") is True
        assert _matches_exclude(".git/objects/pack/abc") is True

    def test_nested_env(self):
        assert _matches_exclude("config/.env") is True

    def test_deeply_nested_secrets(self):
        assert _matches_exclude("deploy/secrets.yaml") is True
        assert _matches_exclude("a/b/c/credentials.json") is True

    def test_windows_path_separators(self):
        assert _matches_exclude("node_modules\\react\\index.js") is True
        assert _matches_exclude(".git\\config") is True

    def test_safe_nested_paths(self):
        assert _matches_exclude("src/utils/helpers.py") is False
        assert _matches_exclude("lib/auth/handler.js") is False


class TestTruncation:
    def test_no_truncation_needed(self):
        content = "short content"
        result, truncated = _truncate_content(content, 1000)
        assert result == content
        assert truncated is False

    def test_truncation_applied(self):
        content = "x" * 10000
        result, truncated = _truncate_content(content, 1000)
        assert truncated is True
        assert "TRUNCATED" in result
        assert len(result.encode()) < len(content.encode())

    def test_truncation_has_head_and_tail(self):
        content = "HEAD_MARKER " + ("x" * 10000) + " TAIL_MARKER"
        result, truncated = _truncate_content(content, 2000)
        assert truncated is True
        assert "HEAD_MARKER" in result
        assert "TAIL_MARKER" in result


class TestBudgetEnforcement:
    def test_under_budget_no_changes(self):
        src1 = ContextSource(source_type="git_status", original_size=10, included_size=10)
        src2 = ContextSource(source_type="file", path="f.py", original_size=10, included_size=10)
        sections: list[Section] = [
            ("## Small", "small", 50, src1),
            ("## Also small", "also small", 60, src2),
        ]
        _enforce_budget(sections, max_bytes=10000)
        assert len(sections) == 2
        assert src1.excluded is False
        assert src2.excluded is False

    def test_drops_lowest_priority_first(self):
        src_tree = ContextSource(source_type="tree", original_size=500, included_size=500)
        src_env = ContextSource(source_type="env", original_size=500, included_size=500)
        src_diff = ContextSource(source_type="diff_staged", original_size=500, included_size=500)
        sections: list[Section] = [
            ("## Tree\n" + "t" * 500, "tree", 10, src_tree),
            ("## Env\n" + "e" * 500, "env", 5, src_env),
            ("## Diff\n" + "d" * 500, "diff", 90, src_diff),
        ]
        _enforce_budget(sections, max_bytes=600)
        # Diff should remain.
        labels = [s[0][:10] for s in sections]
        assert any("Diff" in l for l in labels)
        # Dropped sources should be marked.
        assert src_tree.excluded is True
        assert src_tree.included_size == 0
        assert src_tree.reason == "dropped due to context budget"
        assert src_env.excluded is True
        assert src_env.included_size == 0

    def test_preserves_diffs_above_threshold(self):
        src = ContextSource(source_type="diff_staged", original_size=2000, included_size=2000)
        sections: list[Section] = [
            ("## Diff\n" + "d" * 2000, "diff", 90, src),
        ]
        _enforce_budget(sections, max_bytes=500)
        assert len(sections) == 1  # Diff stays, may be truncated

    # --- Issue 2: source metadata updated on drop ---
    def test_dropped_source_metadata_updated(self):
        src_file = ContextSource(source_type="file", path="big.py", original_size=5000, included_size=5000)
        src_diff = ContextSource(source_type="diff_staged", original_size=100, included_size=100)
        sections: list[Section] = [
            ("## File: big.py\n" + "x" * 5000, "content", 30, src_file),
            ("## Diff\n" + "d" * 100, "diff", 90, src_diff),
        ]
        _enforce_budget(sections, max_bytes=200)
        # File should have been dropped.
        assert src_file.excluded is True
        assert src_file.included_size == 0
        assert "budget" in src_file.reason
        # Diff should remain.
        assert src_diff.excluded is False


class TestGatherContextNone:
    def test_none_mode_returns_empty(self):
        opts = RunOptions(
            mode=Mode.FIX,
            task="test",
            context_mode=ContextMode.NONE,
            diff_scope=DiffScope.NONE,
        )
        ctx = gather_context(opts, repo_root=None)
        assert ctx.text == ""
        assert ctx.sources == []


class TestBinaryDetection:
    def test_binary_extension(self, tmp_path: Path):
        f = tmp_path / "image.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n")
        assert _is_binary(f) is True

    def test_text_file(self, tmp_path: Path):
        f = tmp_path / "code.py"
        f.write_text("print('hello')", encoding="utf-8")
        assert _is_binary(f) is False

    def test_null_bytes(self, tmp_path: Path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"some\x00binary\x00data")
        assert _is_binary(f) is True


# --- Issue 5: collect_changed_files ---
class TestCollectChangedFiles:
    def test_unions_staged_unstaged_untracked(self):
        """Verify changed files are a union of all three sources."""
        def mock_run_git(args, cwd=None):
            cmd = " ".join(args)
            if "diff --name-only --staged" in cmd:
                return "staged_file.py\n"
            if "diff --name-only" in cmd:
                return "unstaged_file.py\n"
            if "status --porcelain" in cmd:
                return "?? new_untracked.py\nM  staged_file.py\n"
            return None

        with patch("council.context._run_git", side_effect=mock_run_git):
            files = _collect_changed_files(Path("/fake"))

        assert "staged_file.py" in files
        assert "unstaged_file.py" in files
        assert "new_untracked.py" in files

    def test_handles_no_commits(self):
        """All git diff commands fail (no HEAD), but status works."""
        def mock_run_git(args, cwd=None):
            cmd = " ".join(args)
            if "diff" in cmd:
                return None  # No commits, diff fails.
            if "status --porcelain" in cmd:
                return "?? file_a.py\n?? file_b.py\n"
            return None

        with patch("council.context._run_git", side_effect=mock_run_git):
            files = _collect_changed_files(Path("/fake"))

        assert "file_a.py" in files
        assert "file_b.py" in files

    def test_deduplicates(self):
        """Same file in multiple sources should appear once."""
        def mock_run_git(args, cwd=None):
            cmd = " ".join(args)
            if "diff --name-only --staged" in cmd:
                return "both.py\n"
            if "diff --name-only" in cmd:
                return "both.py\n"
            if "status --porcelain" in cmd:
                return ""
            return None

        with patch("council.context._run_git", side_effect=mock_run_git):
            files = _collect_changed_files(Path("/fake"))

        assert files.count("both.py") == 1
