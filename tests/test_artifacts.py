"""Tests for run directory structure and manifest fields."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from council.artifacts import create_run_dir, save_final, save_round0, save_task, write_manifest
from council.config import CouncilConfig
from council.types import (
    ContextSource,
    GatheredContext,
    Mode,
    RoundResult,
    RunOptions,
    ToolResult,
)


class TestRunDirectoryStructure:
    def test_creates_expected_dirs(self, basic_opts: RunOptions):
        run_dir = create_run_dir(basic_opts)
        assert run_dir.exists()
        assert (run_dir / "rounds" / "0_generate").is_dir()
        assert (run_dir / "rounds" / "1_claude_improve").is_dir()
        assert (run_dir / "rounds" / "2_codex_critique").is_dir()
        assert (run_dir / "rounds" / "3_claude_finalize").is_dir()
        assert (run_dir / "final").is_dir()

    def test_dirname_contains_timestamp_and_slug(self, basic_opts: RunOptions):
        run_dir = create_run_dir(basic_opts)
        name = run_dir.name
        # Should contain date pattern.
        assert "-" in name
        assert "_" in name
        # Should contain slug from task.
        assert "fix" in name.lower() or "broken" in name.lower() or "auth" in name.lower()


class TestSaveTask:
    def test_saves_task_file(self, basic_opts: RunOptions):
        run_dir = create_run_dir(basic_opts)
        save_task(run_dir, "My test task")
        content = (run_dir / "task.md").read_text()
        assert content == "My test task"


class TestSaveRound0:
    def test_saves_prompts_and_results(self, basic_opts: RunOptions):
        run_dir = create_run_dir(basic_opts)
        prompts = {
            "claude": "prompt for claude",
            "codex": "prompt for codex",
        }
        results = {
            "claude": ToolResult(
                tool_name="claude",
                command=["claude", "-p"],
                exit_code=0,
                stdout="claude output",
                stderr="",
                duration_sec=5.0,
            ),
            "codex": ToolResult(
                tool_name="codex",
                command=["codex"],
                exit_code=0,
                stdout="codex output",
                stderr="warning",
                duration_sec=3.0,
            ),
        }
        save_round0(run_dir, prompts, results)

        rdir = run_dir / "rounds" / "0_generate"
        assert (rdir / "prompt_claude.md").read_text() == "prompt for claude"
        assert (rdir / "prompt_codex.md").read_text() == "prompt for codex"
        assert (rdir / "claude_stdout.md").read_text() == "claude output"
        assert (rdir / "codex_stderr.txt").read_text() == "warning"


class TestSaveFinal:
    def test_saves_with_patch(self, basic_opts: RunOptions):
        run_dir = create_run_dir(basic_opts)
        save_final(run_dir, "# Final\nresult", "--- a/f\n+++ b/f\n", "Summary here")
        fdir = run_dir / "final"
        assert (fdir / "final.md").read_text() == "# Final\nresult"
        assert (fdir / "final.patch").exists()
        assert (fdir / "summary.md").read_text() == "Summary here"

    def test_saves_without_patch(self, basic_opts: RunOptions):
        run_dir = create_run_dir(basic_opts)
        save_final(run_dir, "# Final\nno patch", None, "Summary")
        fdir = run_dir / "final"
        assert (fdir / "final.md").exists()
        assert not (fdir / "final.patch").exists()


class TestManifest:
    def test_manifest_fields(self, basic_opts: RunOptions):
        run_dir = create_run_dir(basic_opts)
        config = CouncilConfig.defaults()
        ctx = GatheredContext(text="context", sources=[], total_size=7)

        r0 = RoundResult(
            round_name="0_generate",
            results={
                "claude": ToolResult(
                    tool_name="claude",
                    command=["claude", "-p"],
                    exit_code=0,
                    stdout="out",
                    stderr="",
                    duration_sec=2.5,
                ),
            },
        )

        start = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        end = datetime(2025, 1, 1, 12, 5, 0, tzinfo=timezone.utc)

        write_manifest(run_dir, basic_opts, config, ctx, [r0], start, end)

        manifest_path = run_dir / "manifest.json"
        assert manifest_path.exists()

        data = json.loads(manifest_path.read_text())
        assert data["mode"] == "fix"
        assert data["version"] == "1.0"
        assert "start_time" in data
        assert "end_time" in data
        assert "total_duration_sec" in data
        assert data["total_duration_sec"] == 300.0
        assert "context" in data
        assert data["context"]["total_size_bytes"] == 7
        assert "tools" in data
        assert "claude" in data["tools"]
        assert "rounds" in data
        assert len(data["rounds"]) == 1
        assert data["rounds"][0]["name"] == "0_generate"

    def test_env_redaction_in_manifest(self, basic_opts: RunOptions):
        from council.config import redact_env
        env = {"OPENAI_API_KEY": "sk-secret", "HOME": "/home/user"}
        redacted = redact_env(env)
        assert redacted["OPENAI_API_KEY"] == "***REDACTED***"
        assert redacted["HOME"] == "/home/user"
