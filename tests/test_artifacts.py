"""Tests for run directory structure, manifest fields, and command redaction."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from council.artifacts import _redact_command, create_run_dir, save_final, save_round0, save_task, write_manifest
from council.config import CouncilConfig
from council.types import (
    GatheredContext,
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
        assert "-" in name
        assert "_" in name
        assert "fix" in name.lower() or "broken" in name.lower() or "auth" in name.lower()

    # --- Issue 4: run dir uniqueness ---
    def test_consecutive_dirs_are_unique(self, basic_opts: RunOptions):
        """Two consecutive create_run_dir calls produce different directories."""
        dir1 = create_run_dir(basic_opts)
        dir2 = create_run_dir(basic_opts)
        assert dir1 != dir2
        assert dir1.exists()
        assert dir2.exists()


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
                    command=["claude"],
                    exit_code=0,
                    stdout="out",
                    stderr="",
                    duration_sec=2.5,
                ),
            },
        )

        start = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 12, 5, 0, tzinfo=UTC)

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


# --- Issue 3: command redaction ---
class TestCommandRedaction:
    def test_flag_equals_value(self):
        """--api-key=sk-secret should redact the value."""
        cmd = ["tool", "--api-key=sk-secret123", "--verbose"]
        result = _redact_command(cmd)
        assert result[0] == "tool"
        assert result[1] == "--api-key=***REDACTED***"
        assert result[2] == "--verbose"

    def test_flag_separate_value(self):
        """--api-key sk-secret as two args: flag preserved, value redacted."""
        cmd = ["tool", "--api-key", "sk-secret123", "--output", "file.txt"]
        result = _redact_command(cmd)
        assert result[0] == "tool"
        assert result[1] == "--api-key"
        assert result[2] == "***REDACTED***"
        assert result[3] == "--output"
        assert result[4] == "file.txt"

    def test_token_flag(self):
        cmd = ["tool", "--token", "my-token-value"]
        result = _redact_command(cmd)
        assert result[1] == "--token"
        assert result[2] == "***REDACTED***"

    def test_password_equals(self):
        cmd = ["tool", "--password=hunter2"]
        result = _redact_command(cmd)
        assert result[1] == "--password=***REDACTED***"

    def test_short_flag_with_secret(self):
        cmd = ["tool", "-k", "secret-key-value"]
        # -k is in the sensitive short-flag allowlist, so it SHOULD be redacted.
        result = _redact_command(cmd)
        assert result[1] == "-k"
        assert result[2] == "***REDACTED***"

    def test_credential_flag(self):
        cmd = ["tool", "--credential", "cred-val"]
        result = _redact_command(cmd)
        assert result[1] == "--credential"
        assert result[2] == "***REDACTED***"

    def test_no_sensitive_flags(self):
        cmd = ["claude", "-p", "--no-color"]
        result = _redact_command(cmd)
        assert result == cmd

    def test_secret_env_in_flag(self):
        cmd = ["tool", "--secret-key=abc123"]
        result = _redact_command(cmd)
        assert result[1] == "--secret-key=***REDACTED***"

    def test_short_flag_t_redacted(self):
        """Short flag -t (token) should be redacted via allowlist."""
        cmd = ["tool", "-t", "my-token"]
        result = _redact_command(cmd)
        assert result[1] == "-t"
        assert result[2] == "***REDACTED***"


class TestManifestToolConfigRedaction:
    """Issue 5: manifest tool config fields must be redacted."""

    def test_extra_args_secret_redacted_in_manifest(self, basic_opts: RunOptions):
        """extra_args containing --api-key=sk-... must be redacted in manifest."""
        from council.config import CouncilConfig, ToolConfig

        config = CouncilConfig(tools={
            "claude": ToolConfig(
                command=["claude"],
                extra_args=["--api-key=sk-live-secret123", "--verbose"],
            ),
        })
        run_dir = create_run_dir(basic_opts)
        ctx = GatheredContext(text="ctx", sources=[], total_size=3)
        start = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 12, 1, 0, tzinfo=UTC)
        write_manifest(run_dir, basic_opts, config, ctx, [], start, end)

        data = json.loads((run_dir / "manifest.json").read_text())
        tool_cfg = data["tools"]["claude"]
        # The secret value must be redacted.
        assert "sk-live-secret123" not in json.dumps(tool_cfg)
        assert "***REDACTED***" in json.dumps(tool_cfg)
        # --verbose should be preserved.
        assert "--verbose" in tool_cfg["extra_args"]

    def test_command_secret_redacted_in_manifest(self, basic_opts: RunOptions):
        """Secrets in command list must also be redacted."""
        from council.config import CouncilConfig, ToolConfig

        config = CouncilConfig(tools={
            "codex": ToolConfig(
                command=["codex", "--token=abc123"],
                extra_args=[],
            ),
        })
        run_dir = create_run_dir(basic_opts)
        ctx = GatheredContext(text="ctx", sources=[], total_size=3)
        start = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 12, 1, 0, tzinfo=UTC)
        write_manifest(run_dir, basic_opts, config, ctx, [], start, end)

        data = json.loads((run_dir / "manifest.json").read_text())
        tool_cfg = data["tools"]["codex"]
        assert "abc123" not in json.dumps(tool_cfg)
        assert tool_cfg["command"][1] == "--token=***REDACTED***"
