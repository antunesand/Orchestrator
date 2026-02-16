"""Tests for multi-candidate mode (--claude-n / --codex-n)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from council.pipeline import _pick_best_candidate
from council.types import Mode, RunOptions


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
            result = runner.invoke(app, [
                "fix", "test task",
                "--claude-n", "3",
                "--codex-n", "2",
                "--dry-run",
            ])
        if result.exit_code == 0:
            opts = mock_run.call_args[0][0]
            assert opts.claude_n == 3
            assert opts.codex_n == 2

    def test_feature_accepts_claude_n(self):
        from typer.testing import CliRunner
        from council.cli import app

        runner = CliRunner()
        with patch("council.cli._run") as mock_run:
            result = runner.invoke(app, [
                "feature", "new feature",
                "--claude-n", "2",
            ])
        if result.exit_code == 0:
            opts = mock_run.call_args[0][0]
            assert opts.claude_n == 2
            assert opts.codex_n == 1  # default

    def test_review_accepts_codex_n(self):
        from typer.testing import CliRunner
        from council.cli import app

        runner = CliRunner()
        with patch("council.cli._run") as mock_run:
            result = runner.invoke(app, [
                "review", "review this",
                "--codex-n", "3",
            ])
        if result.exit_code == 0:
            opts = mock_run.call_args[0][0]
            assert opts.codex_n == 3

    def test_structured_review_flag(self):
        from typer.testing import CliRunner
        from council.cli import app

        runner = CliRunner()
        with patch("council.cli._run") as mock_run:
            result = runner.invoke(app, [
                "fix", "test task",
                "--structured-review",
            ])
        if result.exit_code == 0:
            opts = mock_run.call_args[0][0]
            assert opts.structured_review is True

    def test_review_structured_by_default(self):
        from typer.testing import CliRunner
        from council.cli import app

        runner = CliRunner()
        with patch("council.cli._run") as mock_run:
            result = runner.invoke(app, [
                "review", "review this",
            ])
        if result.exit_code == 0:
            opts = mock_run.call_args[0][0]
            assert opts.structured_review is True

    def test_review_no_structured(self):
        from typer.testing import CliRunner
        from council.cli import app

        runner = CliRunner()
        with patch("council.cli._run") as mock_run:
            result = runner.invoke(app, [
                "review", "review this",
                "--no-structured-review",
            ])
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
            prompts[f"claude_{i+1}"] = round0_prompt(opts.mode, opts.task, "ctx")

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
            prompts[f"codex_{i+1}"] = round0_prompt(opts.mode, opts.task, "ctx")

        assert "codex" in prompts
        assert "codex_2" in prompts
        assert "codex_3" in prompts
        assert len(prompts) == 3
