"""Tests for context gathering, truncation, and budget enforcement."""

from __future__ import annotations

from pathlib import Path

from council.context import (
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
        # Create content with identifiable head and tail.
        content = "HEAD_MARKER " + ("x" * 10000) + " TAIL_MARKER"
        result, truncated = _truncate_content(content, 2000)
        assert truncated is True
        assert "HEAD_MARKER" in result
        assert "TAIL_MARKER" in result


class TestBudgetEnforcement:
    def test_under_budget_no_changes(self):
        sections = [
            ("## Small", "small", 50),
            ("## Also small", "also small", 60),
        ]
        sources: list[ContextSource] = []
        _enforce_budget(sections, sources, max_bytes=10000)
        assert len(sections) == 2

    def test_drops_lowest_priority_first(self):
        sections = [
            ("## Tree\n" + "t" * 500, "tree", 10),   # lowest priority
            ("## Env\n" + "e" * 500, "env", 5),       # even lower
            ("## Diff\n" + "d" * 500, "diff", 90),    # high priority
        ]
        sources: list[ContextSource] = []
        # Budget allows only ~600 bytes total.
        _enforce_budget(sections, sources, max_bytes=600)
        # Should have dropped low-priority items, kept diff.
        labels = [s[0][:10] for s in sections]
        assert any("Diff" in l for l in labels)

    def test_preserves_diffs_above_threshold(self):
        sections = [
            ("## Diff\n" + "d" * 2000, "diff", 90),
        ]
        sources: list[ContextSource] = []
        # Budget is very small but diffs should be preserved.
        _enforce_budget(sections, sources, max_bytes=500)
        assert len(sections) == 1  # Diff stays, may be truncated


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
