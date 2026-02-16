"""Tests for pipeline resume and retry-failed functionality."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from council.config import CouncilConfig
from council.pipeline import resume_pipeline, run_pipeline
from council.state import ROUND_NAMES, init_state, load_state, update_round
from council.types import Mode, RoundStatus, RunOptions, ToolResult


def _mock_tool_result(name: str, stdout: str = "mock output", exit_code: int = 0) -> ToolResult:
    return ToolResult(
        tool_name=name,
        command=[name],
        exit_code=exit_code,
        stdout=stdout,
        stderr="",
        duration_sec=1.0,
    )


def _setup_interrupted_run(tmp_path: Path) -> Path:
    """Create a run directory that looks like it was interrupted after Round 1.

    Round 0 and Round 1 succeeded; Rounds 2 and 3 are still pending.
    """
    run_dir = tmp_path / "test_run"
    run_dir.mkdir()
    (run_dir / "task.md").write_text("Fix the login bug", encoding="utf-8")
    (run_dir / "context.md").write_text("## Context\nSome context here", encoding="utf-8")

    # Create round directories and artifacts.
    for rname in ROUND_NAMES:
        (run_dir / "rounds" / rname).mkdir(parents=True, exist_ok=True)
    (run_dir / "final").mkdir(exist_ok=True)

    # Round 0 outputs.
    r0_dir = run_dir / "rounds" / "0_generate"
    (r0_dir / "claude_stdout.md").write_text("Claude R0 analysis", encoding="utf-8")
    (r0_dir / "codex_stdout.md").write_text("Codex R0 analysis", encoding="utf-8")

    # Round 1 output.
    r1_dir = run_dir / "rounds" / "1_claude_improve"
    (r1_dir / "stdout.md").write_text("Claude improved analysis", encoding="utf-8")

    # Write state.json: R0 OK, R1 OK, R2/R3 pending.
    state = init_state(run_dir, "fix", "Fix the login bug", ["claude", "codex"])
    update_round(run_dir, state, "0_generate", RoundStatus.OK, {"claude": "ok", "codex": "ok"})
    update_round(run_dir, state, "1_claude_improve", RoundStatus.OK, {"claude": "ok"})

    return run_dir


def _setup_failed_run(tmp_path: Path) -> Path:
    """Create a run directory where Round 2 (codex critique) failed.

    Rounds 0, 1 OK; Round 2 failed; Round 3 pending.
    """
    run_dir = _setup_interrupted_run(tmp_path)

    state = load_state(run_dir)
    update_round(run_dir, state, "2_codex_critique", RoundStatus.FAILED, {"codex": "failed"})

    return run_dir


class TestResumeInterruptedRun:
    @pytest.mark.asyncio
    async def test_resumes_from_round_2(self, tmp_path: Path):
        """Resume should skip R0 and R1, execute R2 and R3."""
        run_dir = _setup_interrupted_run(tmp_path)
        config = CouncilConfig.defaults()

        call_log: list[str] = []

        async def mock_run_tool(name, cfg, prompt, timeout_sec=180, cwd=None):
            call_log.append(f"{name}")
            return _mock_tool_result(
                name,
                stdout=f"{name} output for resume",
            )

        with (
            patch("council.pipeline.find_repo_root", return_value=None),
            patch("council.pipeline.run_tool", side_effect=mock_run_tool),
        ):
            result = await resume_pipeline(run_dir, config)

        assert result == run_dir

        # Only R2 (codex) and R3 (claude) should have been called.
        assert call_log == ["codex", "claude"]

        # Final output should exist.
        assert (run_dir / "final" / "final.md").exists()

        # State should be completed.
        state = load_state(run_dir)
        assert state["status"] == "completed"

    @pytest.mark.asyncio
    async def test_resume_all_complete_is_noop(self, tmp_path: Path):
        """Resuming a fully completed run should be a no-op."""
        run_dir = _setup_interrupted_run(tmp_path)
        state = load_state(run_dir)
        update_round(run_dir, state, "2_codex_critique", RoundStatus.OK, {"codex": "ok"})
        update_round(run_dir, state, "3_claude_finalize", RoundStatus.OK, {"claude": "ok"})

        config = CouncilConfig.defaults()

        with patch("council.pipeline.find_repo_root", return_value=None):
            result = await resume_pipeline(run_dir, config)

        assert result == run_dir


class TestRetryFailed:
    @pytest.mark.asyncio
    async def test_retry_failed_reruns_only_failed_round(self, tmp_path: Path):
        """--retry-failed should only re-execute rounds marked as failed."""
        run_dir = _setup_failed_run(tmp_path)

        # Also mark R3 as failed.
        state = load_state(run_dir)
        update_round(run_dir, state, "3_claude_finalize", RoundStatus.FAILED, {"claude": "failed"})

        config = CouncilConfig.defaults()
        call_log: list[str] = []

        async def mock_run_tool(name, cfg, prompt, timeout_sec=180, cwd=None):
            call_log.append(name)
            return _mock_tool_result(name, stdout=f"{name} retry output")

        with (
            patch("council.pipeline.find_repo_root", return_value=None),
            patch("council.pipeline.run_tool", side_effect=mock_run_tool),
        ):
            result = await resume_pipeline(
                run_dir, config, retry_failed=True,
            )

        assert result == run_dir

        # Both failed rounds should be retried.
        assert "codex" in call_log
        assert "claude" in call_log

        state = load_state(run_dir)
        assert state["status"] == "completed"

    @pytest.mark.asyncio
    async def test_retry_failed_preserves_ok_rounds(self, tmp_path: Path):
        """Rounds that previously succeeded should not be re-executed."""
        run_dir = _setup_failed_run(tmp_path)
        config = CouncilConfig.defaults()

        call_log: list[str] = []

        async def mock_run_tool(name, cfg, prompt, timeout_sec=180, cwd=None):
            call_log.append(f"round_call:{name}")
            return _mock_tool_result(name, stdout=f"{name} output")

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
            result = await resume_pipeline(
                run_dir, config, retry_failed=True,
            )

        # R0 and R1 should NOT have been called (they were OK).
        # R2 failed -> retried, R3 pending -> executed.
        # Only codex (R2) and claude (R3) should appear.
        assert call_log == ["round_call:codex", "round_call:claude"]


class TestRunPipelineCreatesState:
    @pytest.mark.asyncio
    async def test_full_run_creates_state_json(self, tmp_path: Path):
        """A fresh pipeline run should create state.json."""
        opts = RunOptions(
            mode=Mode.FIX,
            task="Fix the bug",
            outdir=tmp_path,
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

        assert (run_dir / "state.json").exists()
        state = json.loads((run_dir / "state.json").read_text())
        assert state["status"] == "completed"
        assert state["finished_at"] is not None

        # All rounds should be OK.
        for name in ROUND_NAMES:
            assert state["rounds"][name]["status"] in ("ok", "skipped")


class TestResumeCLI:
    """Tests for the `council resume` CLI command."""

    def test_resume_missing_dir(self):
        from typer.testing import CliRunner
        from council.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["resume", "/nonexistent/path"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_resume_missing_state_json(self, tmp_path: Path):
        from typer.testing import CliRunner
        from council.cli import app

        # Create a directory with no state.json.
        run_dir = tmp_path / "empty_run"
        run_dir.mkdir()

        runner = CliRunner()
        result = runner.invoke(app, ["resume", str(run_dir)])
        assert result.exit_code == 1
        assert "state.json" in result.output
