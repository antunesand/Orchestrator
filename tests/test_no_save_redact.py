"""Tests for --no-save and --redact-paths features."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from council.artifacts import cleanup_intermediates, redact_abs_paths, write_minimal_manifest
from council.config import CouncilConfig
from council.pipeline import run_pipeline
from council.types import Mode, RunOptions, ToolResult


# ---------------------------------------------------------------------------
# redact_abs_paths unit tests
# ---------------------------------------------------------------------------

class TestRedactAbsPaths:
    def test_redacts_home_path(self):
        text = "See /home/user/project/src/foo.py for details."
        result = redact_abs_paths(text)
        assert "/home/user" not in result
        assert "<REDACTED>/foo.py" in result

    def test_redacts_users_path(self):
        text = "File at /Users/alice/dev/bar.ts"
        result = redact_abs_paths(text)
        assert "/Users/alice" not in result
        assert "<REDACTED>/bar.ts" in result

    def test_redacts_var_path(self):
        text = "Log: /var/log/app/error.log"
        result = redact_abs_paths(text)
        assert "/var/log" not in result
        assert "<REDACTED>/error.log" in result

    def test_redacts_tmp_path(self):
        text = "Temp file /tmp/council_abc123/prompt.md"
        result = redact_abs_paths(text)
        assert "/tmp/council" not in result
        assert "<REDACTED>/prompt.md" in result

    def test_preserves_relative_paths(self):
        text = "Edit src/council/pipeline.py"
        result = redact_abs_paths(text)
        assert result == text

    def test_preserves_urls(self):
        text = "See https://example.com/home/page for docs."
        result = redact_abs_paths(text)
        assert "https://example.com/home/page" in result

    def test_multiple_paths(self):
        text = (
            "Files: /home/user/a.py and /Users/bob/b.ts\n"
            "Also /var/lib/data/c.json"
        )
        result = redact_abs_paths(text)
        assert "/home/user" not in result
        assert "/Users/bob" not in result
        assert "/var/lib" not in result
        assert "<REDACTED>/a.py" in result
        assert "<REDACTED>/b.ts" in result
        assert "<REDACTED>/c.json" in result

    def test_empty_string(self):
        assert redact_abs_paths("") == ""

    def test_no_paths(self):
        text = "Just regular text with no paths at all."
        assert redact_abs_paths(text) == text

    def test_workspace_path(self):
        text = "Running in /workspace/myproject/src/main.py"
        result = redact_abs_paths(text)
        assert "<REDACTED>/main.py" in result


# ---------------------------------------------------------------------------
# cleanup_intermediates unit tests
# ---------------------------------------------------------------------------

class TestCleanupIntermediates:
    def test_removes_task_and_context(self, tmp_path: Path):
        (tmp_path / "task.md").write_text("task")
        (tmp_path / "context.md").write_text("ctx")
        (tmp_path / "context_sources.json").write_text("[]")
        (tmp_path / "state.json").write_text("{}")

        cleanup_intermediates(tmp_path)

        assert not (tmp_path / "task.md").exists()
        assert not (tmp_path / "context.md").exists()
        assert not (tmp_path / "context_sources.json").exists()
        assert not (tmp_path / "state.json").exists()

    def test_removes_rounds_directory(self, tmp_path: Path):
        rounds = tmp_path / "rounds" / "0_generate"
        rounds.mkdir(parents=True)
        (rounds / "prompt_claude.md").write_text("prompt")

        cleanup_intermediates(tmp_path)

        assert not (tmp_path / "rounds").exists()

    def test_preserves_final_dir(self, tmp_path: Path):
        final = tmp_path / "final"
        final.mkdir()
        (final / "final.md").write_text("output")

        cleanup_intermediates(tmp_path)

        assert (final / "final.md").exists()

    def test_tolerates_missing_files(self, tmp_path: Path):
        # Should not raise even when files don't exist.
        cleanup_intermediates(tmp_path)


# ---------------------------------------------------------------------------
# write_minimal_manifest unit tests
# ---------------------------------------------------------------------------

class TestWriteMinimalManifest:
    def test_writes_slim_manifest(self, tmp_path: Path):
        from datetime import UTC, datetime

        opts = RunOptions(mode=Mode.FIX, task="Fix bug", outdir=tmp_path)
        start = datetime(2026, 2, 16, 12, 0, 0, tzinfo=UTC)
        end = datetime(2026, 2, 16, 12, 5, 0, tzinfo=UTC)

        write_minimal_manifest(tmp_path, opts, start, end)

        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert manifest["no_save"] is True
        assert manifest["task_preview"] == "(not saved)"
        assert manifest["mode"] == "fix"
        assert "context" not in manifest
        assert "rounds" not in manifest


# ---------------------------------------------------------------------------
# Pipeline integration: --no-save
# ---------------------------------------------------------------------------

def _mock_tool_result(name: str, stdout: str = "mock output", exit_code: int = 0) -> ToolResult:
    return ToolResult(
        tool_name=name,
        command=[name],
        exit_code=exit_code,
        stdout=stdout,
        stderr="",
        duration_sec=1.0,
    )


class TestNoSavePipeline:
    @pytest.mark.asyncio
    async def test_no_save_only_keeps_final_and_manifest(self, tmp_path: Path):
        """--no-save should only write final/ contents and manifest.json."""
        opts = RunOptions(
            mode=Mode.FIX,
            task="Fix the bug",
            outdir=tmp_path,
            no_save=True,
        )
        config = CouncilConfig.defaults()

        async def mock_run_tool(name, cfg, prompt, timeout_sec=180, cwd=None):
            return _mock_tool_result(name, stdout=f"{name} analysis")

        async def mock_run_parallel(configs, prompts, timeout_sec=180, cwd=None):
            results = {}
            for name in prompts:
                if name in configs:
                    results[name] = await mock_run_tool(name, configs[name], prompts[name])
            return results

        with (
            patch("council.pipeline.find_repo_root", return_value=None),
            patch("council.pipeline.run_tools_parallel", side_effect=mock_run_parallel),
            patch("council.pipeline.run_tool", side_effect=mock_run_tool),
        ):
            run_dir = await run_pipeline(opts, config)

        # Final output should exist.
        assert (run_dir / "final" / "final.md").exists()
        assert (run_dir / "manifest.json").exists()

        # Intermediate artifacts should NOT exist.
        assert not (run_dir / "task.md").exists()
        assert not (run_dir / "context.md").exists()
        assert not (run_dir / "context_sources.json").exists()
        assert not (run_dir / "state.json").exists()
        assert not (run_dir / "rounds").exists()

        # Manifest should be minimal.
        manifest = json.loads((run_dir / "manifest.json").read_text())
        assert manifest["no_save"] is True
        assert "rounds" not in manifest

    @pytest.mark.asyncio
    async def test_no_save_does_not_create_round_dirs(self, tmp_path: Path):
        """--no-save should not create rounds/ subdirectories at all."""
        opts = RunOptions(
            mode=Mode.FIX,
            task="Fix it",
            outdir=tmp_path,
            no_save=True,
            dry_run=True,
        )
        config = CouncilConfig.defaults()

        with patch("council.pipeline.find_repo_root", return_value=None):
            run_dir = await run_pipeline(opts, config)

        # final/ should still exist.
        assert (run_dir / "final").is_dir()
        # rounds/ should not.
        assert not (run_dir / "rounds").exists()


# ---------------------------------------------------------------------------
# Pipeline integration: --redact-paths
# ---------------------------------------------------------------------------

class TestRedactPathsPipeline:
    @pytest.mark.asyncio
    async def test_redact_paths_in_final_output(self, tmp_path: Path):
        """--redact-paths should strip absolute paths from saved final.md."""
        opts = RunOptions(
            mode=Mode.FIX,
            task="Fix the bug in /home/user/project/app.py",
            outdir=tmp_path,
            redact_paths=True,
        )
        config = CouncilConfig.defaults()

        async def mock_run_tool(name, cfg, prompt, timeout_sec=180, cwd=None):
            return _mock_tool_result(
                name,
                stdout=(
                    "### Summary\n"
                    "Fixed the bug in /home/user/project/app.py\n"
                    "Also checked /var/log/app.log\n"
                ),
            )

        async def mock_run_parallel(configs, prompts, timeout_sec=180, cwd=None):
            results = {}
            for name in prompts:
                if name in configs:
                    results[name] = await mock_run_tool(name, configs[name], prompts[name])
            return results

        with (
            patch("council.pipeline.find_repo_root", return_value=None),
            patch("council.pipeline.run_tools_parallel", side_effect=mock_run_parallel),
            patch("council.pipeline.run_tool", side_effect=mock_run_tool),
        ):
            run_dir = await run_pipeline(opts, config)

        final_content = (run_dir / "final" / "final.md").read_text()
        assert "/home/user/project" not in final_content
        assert "<REDACTED>/app.py" in final_content
        assert "/var/log" not in final_content
        assert "<REDACTED>/app.log" in final_content

    @pytest.mark.asyncio
    async def test_redact_paths_in_manifest(self, tmp_path: Path):
        """--redact-paths should redact file lists in manifest.json."""
        opts = RunOptions(
            mode=Mode.FIX,
            task="Fix bug in /home/user/src/main.py",
            outdir=tmp_path,
            redact_paths=True,
        )
        config = CouncilConfig.defaults()

        async def mock_run_tool(name, cfg, prompt, timeout_sec=180, cwd=None):
            return _mock_tool_result(name, stdout="output")

        async def mock_run_parallel(configs, prompts, timeout_sec=180, cwd=None):
            results = {}
            for name in prompts:
                if name in configs:
                    results[name] = await mock_run_tool(name, configs[name], prompts[name])
            return results

        with (
            patch("council.pipeline.find_repo_root", return_value=None),
            patch("council.pipeline.run_tools_parallel", side_effect=mock_run_parallel),
            patch("council.pipeline.run_tool", side_effect=mock_run_tool),
        ):
            run_dir = await run_pipeline(opts, config)

        manifest = json.loads((run_dir / "manifest.json").read_text())
        # task_preview should have paths redacted.
        assert "/home/user" not in manifest["task_preview"]


# ---------------------------------------------------------------------------
# Combined: --no-save --redact-paths
# ---------------------------------------------------------------------------

class TestCombinedNoSaveRedact:
    @pytest.mark.asyncio
    async def test_both_flags_together(self, tmp_path: Path):
        """Both flags should work simultaneously."""
        opts = RunOptions(
            mode=Mode.FEATURE,
            task="Add feature involving /home/user/project/src/app.py",
            outdir=tmp_path,
            no_save=True,
            redact_paths=True,
        )
        config = CouncilConfig.defaults()

        async def mock_run_tool(name, cfg, prompt, timeout_sec=180, cwd=None):
            return _mock_tool_result(
                name,
                stdout="Changed /home/user/project/src/app.py successfully",
            )

        async def mock_run_parallel(configs, prompts, timeout_sec=180, cwd=None):
            results = {}
            for name in prompts:
                if name in configs:
                    results[name] = await mock_run_tool(name, configs[name], prompts[name])
            return results

        with (
            patch("council.pipeline.find_repo_root", return_value=None),
            patch("council.pipeline.run_tools_parallel", side_effect=mock_run_parallel),
            patch("council.pipeline.run_tool", side_effect=mock_run_tool),
        ):
            run_dir = await run_pipeline(opts, config)

        # Only final output + manifest.
        assert (run_dir / "final" / "final.md").exists()
        assert (run_dir / "manifest.json").exists()
        assert not (run_dir / "rounds").exists()
        assert not (run_dir / "task.md").exists()

        # Final output should have paths redacted.
        final = (run_dir / "final" / "final.md").read_text()
        assert "/home/user" not in final
        assert "<REDACTED>/app.py" in final
