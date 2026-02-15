"""Tests for pipeline orchestration with mocked tool execution."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from council.config import CouncilConfig
from council.pipeline import run_pipeline
from council.types import Mode, RunOptions, ToolResult


def _mock_tool_result(name: str, stdout: str = "mock output", exit_code: int = 0) -> ToolResult:
    """Create a mock ToolResult."""
    return ToolResult(
        tool_name=name,
        command=[name],
        exit_code=exit_code,
        stdout=stdout,
        stderr="",
        duration_sec=1.0,
    )


class TestPipelineDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_creates_prompts_only(self, tmp_path: Path):
        """Dry run should write prompts and context but not call tools."""
        opts = RunOptions(
            mode=Mode.FIX,
            task="Fix the login bug",
            outdir=tmp_path,
            dry_run=True,
        )
        config = CouncilConfig.defaults()

        with patch("council.pipeline.find_repo_root", return_value=None):
            run_dir = await run_pipeline(opts, config)

        assert run_dir.exists()
        assert (run_dir / "task.md").exists()
        assert (run_dir / "context.md").exists()
        assert (run_dir / "manifest.json").exists()

        # Check that prompts were written.
        r0_dir = run_dir / "rounds" / "0_generate"
        assert (r0_dir / "prompt_claude.md").exists()
        assert (r0_dir / "prompt_codex.md").exists()

        # No stdout files (tools weren't called).
        assert not (r0_dir / "claude_stdout.md").exists()


class TestPipelineFullRun:
    @pytest.mark.asyncio
    async def test_full_pipeline_with_mocked_tools(self, tmp_path: Path):
        """Full pipeline with mocked subprocess calls."""
        opts = RunOptions(
            mode=Mode.FEATURE,
            task="Add dark mode support",
            outdir=tmp_path,
        )
        config = CouncilConfig.defaults()

        # Mock all tool calls.
        call_count = 0

        async def mock_run_tool(name, cfg, prompt, timeout_sec=180, cwd=None):
            nonlocal call_count
            call_count += 1

            if name == "claude":
                return _mock_tool_result(
                    "claude",
                    stdout=(
                        "### Summary\n- Added dark mode\n\n"
                        "### Patch\n```diff\n--- a/app.css\n+++ b/app.css\n"
                        "@@ -1 +1 @@\n-light\n+dark\n```\n"
                    ),
                )
            else:
                return _mock_tool_result(
                    "codex",
                    stdout=(
                        "### Summary\n- Implemented dark mode toggle\n\n"
                        "### Confidence Score\n90\n"
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

        # Verify directory structure.
        assert (run_dir / "task.md").exists()
        assert (run_dir / "context.md").exists()
        assert (run_dir / "manifest.json").exists()
        assert (run_dir / "final" / "final.md").exists()
        assert (run_dir / "final" / "summary.md").exists()

        # Verify manifest content.
        manifest = json.loads((run_dir / "manifest.json").read_text())
        assert manifest["mode"] == "feature"
        assert len(manifest["rounds"]) == 4  # All 4 rounds.

        # Should have called tools multiple times.
        assert call_count >= 4  # r0(2) + r1(1) + r2(1) + r3(1) = 5 minimum


class TestPipelinePartialFailure:
    @pytest.mark.asyncio
    async def test_codex_failure_continues_with_claude(self, tmp_path: Path):
        """Pipeline should continue when codex fails."""
        opts = RunOptions(
            mode=Mode.FIX,
            task="Fix bug",
            outdir=tmp_path,
        )
        config = CouncilConfig.defaults()

        async def mock_run_tool(name, cfg, prompt, timeout_sec=180, cwd=None):
            if name == "codex":
                return _mock_tool_result("codex", stdout="", exit_code=1)
            return _mock_tool_result("claude", stdout="Claude's analysis")

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

        assert (run_dir / "final" / "final.md").exists()
        assert (run_dir / "manifest.json").exists()
