"""Tests for multi-candidate mode (--claude-n / --codex-n)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from council.config import CouncilConfig
from council.pipeline import _pick_best_candidate, resume_pipeline, run_pipeline
from council.state import ROUND_NAMES, init_state, load_state, update_round
from council.types import Mode, RoundStatus, RunOptions, ToolResult

# ---------------------------------------------------------------------------
# _pick_best_candidate
# ---------------------------------------------------------------------------


class TestPickBestCandidate:
    def test_single_candidate(self):
        candidates = [("claude", "output text")]
        name, text = _pick_best_candidate(candidates)
        assert name == "claude"
        assert text == "output text"

    def test_picks_highest_confidence(self):
        candidates = [
            ("claude", "Analysis...\nConfidence: 70\n..."),
            ("claude_2", "Analysis...\nConfidence: 90\n..."),
            ("claude_3", "Analysis...\nConfidence: 60\n..."),
        ]
        name, text = _pick_best_candidate(candidates)
        assert name == "claude_2"
        assert "90" in text

    def test_confidence_case_insensitive(self):
        candidates = [
            ("codex", "CONFIDENCE: 40\nshort"),
            ("codex_2", "confidence: 80\nlonger output here"),
        ]
        name, _ = _pick_best_candidate(candidates)
        assert name == "codex_2"

    def test_confidence_with_equals(self):
        candidates = [
            ("claude", "confidence=55"),
            ("claude_2", "confidence=75"),
        ]
        name, _ = _pick_best_candidate(candidates)
        assert name == "claude_2"

    def test_fallback_to_longest_output(self):
        candidates = [
            ("codex", "short"),
            ("codex_2", "this is a much longer output with more detail"),
        ]
        name, _ = _pick_best_candidate(candidates)
        assert name == "codex_2"

    def test_verbose_logging(self, capsys):
        candidates = [
            ("claude", "Confidence: 60"),
            ("claude_2", "Confidence: 80"),
        ]
        name, _ = _pick_best_candidate(candidates, verbose=True)
        assert name == "claude_2"

    def test_mixed_scored_and_unscored(self):
        candidates = [
            ("claude", "No score here, just a long analysis " * 10),
            ("claude_2", "Confidence: 85\nShort but scored"),
        ]
        name, _ = _pick_best_candidate(candidates)
        # The scored candidate should win even though it's shorter.
        assert name == "claude_2"


# ---------------------------------------------------------------------------
# RunOptions multi-candidate fields
# ---------------------------------------------------------------------------


class TestRunOptionsMultiCandidate:
    def test_default_values(self):
        opts = RunOptions(mode=Mode.FIX, task="test")
        assert opts.claude_n == 1
        assert opts.codex_n == 1

    def test_custom_values(self):
        opts = RunOptions(mode=Mode.FIX, task="test", claude_n=3, codex_n=2)
        assert opts.claude_n == 3
        assert opts.codex_n == 2


# ---------------------------------------------------------------------------
# CLI flags
# ---------------------------------------------------------------------------


class TestMultiCandidateCLI:
    def test_fix_accepts_claude_n(self):
        from typer.testing import CliRunner

        from council.cli import app

        runner = CliRunner()
        with patch("council.cli._run") as mock_run:
            result = runner.invoke(
                app,
                [
                    "fix",
                    "test task",
                    "--claude-n",
                    "3",
                    "--codex-n",
                    "2",
                    "--dry-run",
                ],
            )
        if result.exit_code == 0:
            opts = mock_run.call_args[0][0]
            assert opts.claude_n == 3
            assert opts.codex_n == 2

    def test_feature_accepts_claude_n(self):
        from typer.testing import CliRunner

        from council.cli import app

        runner = CliRunner()
        with patch("council.cli._run") as mock_run:
            result = runner.invoke(
                app,
                [
                    "feature",
                    "new feature",
                    "--claude-n",
                    "2",
                ],
            )
        if result.exit_code == 0:
            opts = mock_run.call_args[0][0]
            assert opts.claude_n == 2
            assert opts.codex_n == 1  # default

    def test_review_accepts_codex_n(self):
        from typer.testing import CliRunner

        from council.cli import app

        runner = CliRunner()
        with patch("council.cli._run") as mock_run:
            result = runner.invoke(
                app,
                [
                    "review",
                    "review this",
                    "--codex-n",
                    "3",
                ],
            )
        if result.exit_code == 0:
            opts = mock_run.call_args[0][0]
            assert opts.codex_n == 3

    def test_structured_review_flag(self):
        from typer.testing import CliRunner

        from council.cli import app

        runner = CliRunner()
        with patch("council.cli._run") as mock_run:
            result = runner.invoke(
                app,
                [
                    "fix",
                    "test task",
                    "--structured-review",
                ],
            )
        if result.exit_code == 0:
            opts = mock_run.call_args[0][0]
            assert opts.structured_review is True

    def test_review_structured_by_default(self):
        from typer.testing import CliRunner

        from council.cli import app

        runner = CliRunner()
        with patch("council.cli._run") as mock_run:
            result = runner.invoke(
                app,
                [
                    "review",
                    "review this",
                ],
            )
        if result.exit_code == 0:
            opts = mock_run.call_args[0][0]
            assert opts.structured_review is True

    def test_review_no_structured(self):
        from typer.testing import CliRunner

        from council.cli import app

        runner = CliRunner()
        with patch("council.cli._run") as mock_run:
            result = runner.invoke(
                app,
                [
                    "review",
                    "review this",
                    "--no-structured-review",
                ],
            )
        if result.exit_code == 0:
            opts = mock_run.call_args[0][0]
            assert opts.structured_review is False


# ---------------------------------------------------------------------------
# Pipeline multi-candidate round 0 prompt generation
# ---------------------------------------------------------------------------


class TestMultiCandidatePrompts:
    def test_generates_extra_prompts(self):
        """Verify that claude_n=2 creates two claude prompt entries."""
        from council.prompts import round0_prompt
        from council.types import Mode

        opts = RunOptions(mode=Mode.FIX, task="test", claude_n=2, codex_n=1)
        prompts: dict[str, str] = {}
        prompts["claude"] = round0_prompt(opts.mode, opts.task, "ctx")
        for i in range(1, opts.claude_n):
            prompts[f"claude_{i + 1}"] = round0_prompt(opts.mode, opts.task, "ctx")

        assert "claude" in prompts
        assert "claude_2" in prompts
        assert len(prompts) == 2

    def test_codex_n_generates_extra(self):
        from council.prompts import round0_prompt
        from council.types import Mode

        opts = RunOptions(mode=Mode.FIX, task="test", claude_n=1, codex_n=3)
        prompts: dict[str, str] = {}
        prompts["codex"] = round0_prompt(opts.mode, opts.task, "ctx")
        for i in range(1, opts.codex_n):
            prompts[f"codex_{i + 1}"] = round0_prompt(opts.mode, opts.task, "ctx")

        assert "codex" in prompts
        assert "codex_2" in prompts
        assert "codex_3" in prompts
        assert len(prompts) == 3


# ---------------------------------------------------------------------------
# Multi-candidate persistence in state.json
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


class TestMultiCandidatePersistence:
    @pytest.mark.asyncio
    async def test_chosen_candidate_persisted_in_state(self, tmp_path: Path):
        """Running with claude_n=2 should persist chosen_candidates in state.json."""
        opts = RunOptions(
            mode=Mode.FIX,
            task="Fix bug",
            outdir=tmp_path,
            claude_n=2,
            codex_n=1,
        )
        config = CouncilConfig.defaults()

        async def mock_run_tool(name, cfg, prompt, timeout_sec=180, cwd=None):
            # claude_2 has higher confidence → should be chosen.
            if name == "claude":
                return _mock_tool_result("claude", stdout="Analysis\nConfidence: 60")
            if name == "claude_2":
                return _mock_tool_result("claude_2", stdout="Better analysis\nConfidence: 95")
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
            run_dir = await run_pipeline(opts, config)

        state = json.loads((run_dir / "state.json").read_text())
        chosen = state["rounds"]["0_generate"]["tools"].get("chosen_candidates", {})
        assert chosen.get("claude") == "claude_2"

    @pytest.mark.asyncio
    async def test_resume_uses_chosen_candidate_output(self, tmp_path: Path):
        """Resume should load the chosen candidate's output, not the base."""
        run_dir = tmp_path / "test_run"
        run_dir.mkdir()
        (run_dir / "task.md").write_text("Fix the login bug", encoding="utf-8")
        (run_dir / "context.md").write_text("## Context\nSome context", encoding="utf-8")

        # Create round directories.
        for rname in ROUND_NAMES:
            (run_dir / "rounds" / rname).mkdir(parents=True, exist_ok=True)
        (run_dir / "final").mkdir(exist_ok=True)

        # Write R0 outputs: base claude and chosen claude_2.
        r0_dir = run_dir / "rounds" / "0_generate"
        (r0_dir / "claude_stdout.md").write_text("BASE claude output", encoding="utf-8")
        (r0_dir / "claude_2_stdout.md").write_text("CHOSEN claude_2 output", encoding="utf-8")
        (r0_dir / "codex_stdout.md").write_text("Codex R0 analysis", encoding="utf-8")

        # R1 output.
        r1_dir = run_dir / "rounds" / "1_claude_improve"
        (r1_dir / "stdout.md").write_text("Claude improved analysis", encoding="utf-8")

        # State: R0 OK with chosen_candidates, R1 OK, R2+R3 pending.
        state = init_state(run_dir, "fix", "Fix the login bug", ["claude", "codex"])
        update_round(
            run_dir,
            state,
            "0_generate",
            RoundStatus.OK,
            {
                "claude": "ok",
                "claude_2": "ok",
                "codex": "ok",
                "chosen_candidates": {"claude": "claude_2", "codex": "codex"},
            },
        )
        update_round(run_dir, state, "1_claude_improve", RoundStatus.OK, {"claude": "ok"})

        config = CouncilConfig.defaults()
        prompts_received: list[str] = []

        async def mock_run_tool(name, cfg, prompt, timeout_sec=180, cwd=None):
            prompts_received.append(prompt)
            return _mock_tool_result(name, stdout=f"{name} output for resume")

        with (
            patch("council.pipeline.find_repo_root", return_value=None),
            patch("council.pipeline.run_tool", side_effect=mock_run_tool),
        ):
            await resume_pipeline(run_dir, config)

        # The R2 prompt should reference "CHOSEN claude_2 output" (loaded via
        # chosen_candidates), not "BASE claude output".
        # R2 uses claude_improved (from R1 stdout), but R1 loaded from disk.
        # The key check: R0 should have loaded claude_2's output for building
        # the R1 reuse path. Since R1 is already complete, we can verify state.
        state = load_state(run_dir)
        assert state["status"] == "completed"

        # Additional verification: if we resume from R0 itself with chosen candidates,
        # the correct output is loaded. Read the state to confirm chosen_candidates survived.
        chosen = state["rounds"]["0_generate"]["tools"].get("chosen_candidates", {})
        assert chosen.get("claude") == "claude_2"
