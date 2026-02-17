"""Tests for council init and council doctor commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from council.cli import _ensure_gitignore_entries, app

runner = CliRunner()


class TestInit:
    """Tests for `council init`."""

    def test_creates_config_file(self, tmp_path: Path):
        """init creates .council.yml when it doesn't exist."""
        with patch("council.cli.find_repo_root", return_value=tmp_path):
            result = runner.invoke(app, ["init"])

        assert result.exit_code == 0
        cfg = tmp_path / ".council.yml"
        assert cfg.exists()
        content = cfg.read_text(encoding="utf-8")
        assert "claude" in content
        assert "codex" in content

    def test_does_not_overwrite_without_force(self, tmp_path: Path):
        """init refuses to overwrite existing config without --force."""
        cfg = tmp_path / ".council.yml"
        cfg.write_text("existing config", encoding="utf-8")

        with patch("council.cli.find_repo_root", return_value=tmp_path):
            result = runner.invoke(app, ["init"])

        assert result.exit_code == 1
        assert "already exists" in result.output
        # Original content preserved.
        assert cfg.read_text(encoding="utf-8") == "existing config"

    def test_overwrites_with_force(self, tmp_path: Path):
        """init --force overwrites existing config."""
        cfg = tmp_path / ".council.yml"
        cfg.write_text("old config", encoding="utf-8")

        with patch("council.cli.find_repo_root", return_value=tmp_path):
            result = runner.invoke(app, ["init", "--force"])

        assert result.exit_code == 0
        content = cfg.read_text(encoding="utf-8")
        assert "claude" in content
        assert content != "old config"

    def test_updates_gitignore(self, tmp_path: Path):
        """init adds config entries to .gitignore."""
        with patch("council.cli.find_repo_root", return_value=tmp_path):
            result = runner.invoke(app, ["init"])

        assert result.exit_code == 0
        gitignore = tmp_path / ".gitignore"
        assert gitignore.exists()
        content = gitignore.read_text(encoding="utf-8")
        assert ".council.yml" in content
        assert "council.yml" in content

    def test_prints_next_steps(self, tmp_path: Path):
        """init prints helpful next steps."""
        with patch("council.cli.find_repo_root", return_value=tmp_path):
            result = runner.invoke(app, ["init"])

        assert result.exit_code == 0
        assert "Next steps" in result.output
        assert "council doctor" in result.output

    def test_no_secrets_in_generated_config(self, tmp_path: Path):
        """Generated config must not contain any API keys."""
        with patch("council.cli.find_repo_root", return_value=tmp_path):
            runner.invoke(app, ["init"])

        content = (tmp_path / ".council.yml").read_text(encoding="utf-8")
        # No API key patterns (sk-live-..., sk-ant-..., etc.). "ask-for-approval" is fine.
        assert "sk-live" not in content
        assert "sk-ant" not in content
        assert "ANTHROPIC_API_KEY" not in content
        assert "OPENAI_API_KEY" not in content


class TestEnsureGitignoreEntries:
    """Tests for the .gitignore helper."""

    def test_creates_gitignore_if_missing(self, tmp_path: Path):
        added = _ensure_gitignore_entries(tmp_path)
        assert ".council.yml" in added
        assert "council.yml" in added
        assert (tmp_path / ".gitignore").exists()

    def test_no_duplicates(self, tmp_path: Path):
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(".council.yml\ncouncil.yml\n", encoding="utf-8")
        added = _ensure_gitignore_entries(tmp_path)
        assert added == []

    def test_appends_missing_entries(self, tmp_path: Path):
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("*.pyc\n", encoding="utf-8")
        added = _ensure_gitignore_entries(tmp_path)
        assert ".council.yml" in added
        content = gitignore.read_text(encoding="utf-8")
        assert "*.pyc" in content  # Original preserved.
        assert ".council.yml" in content


class TestDoctor:
    """Tests for `council doctor`."""

    def _patch_doctor(self, tmp_path, which_rv="/usr/bin/fake", version_rv="v1.0", subcmd_rv=True, auth_rv=True):
        """Helper returning a combined context manager for doctor patches."""
        from contextlib import ExitStack
        from unittest.mock import patch as _patch

        stack = ExitStack()
        stack.enter_context(_patch("council.cli.find_repo_root", return_value=tmp_path))
        stack.enter_context(_patch("shutil.which", return_value=which_rv))
        stack.enter_context(_patch("council.cli._probe_tool_version", return_value=version_rv))
        stack.enter_context(_patch("council.cli._check_subcommand", return_value=subcmd_rv))
        stack.enter_context(_patch("council.cli._check_codex_auth", return_value=auth_rv))
        return stack

    def test_shows_version(self, tmp_path: Path):
        """doctor output includes version."""
        with self._patch_doctor(tmp_path, version_rv="claude 1.0"):
            result = runner.invoke(app, ["doctor"])
        assert "council" in result.output

    def test_reports_tool_found(self, tmp_path: Path):
        """doctor reports OK when tool is found."""
        with self._patch_doctor(tmp_path):
            result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "OK" in result.output
        assert "All checks passed" in result.output

    def test_reports_tool_not_found(self, tmp_path: Path):
        """doctor reports NOT FOUND and exits 1 when tools are missing."""
        with self._patch_doctor(tmp_path, which_rv=None):
            result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 1
        assert "NOT FOUND" in result.output
        assert "Some checks failed" in result.output

    def test_shows_config_source(self, tmp_path: Path):
        """doctor shows which config file is used."""
        cfg = tmp_path / ".council.yml"
        cfg.write_text("tools:\n  claude:\n    command: ['claude']\n", encoding="utf-8")
        with self._patch_doctor(tmp_path, version_rv=None):
            result = runner.invoke(app, ["doctor"])
        assert str(tmp_path) in result.output

    def test_shows_defaults_when_no_config(self, tmp_path: Path):
        """doctor shows '(built-in defaults)' when no config file exists."""
        with self._patch_doctor(tmp_path, version_rv=None):
            result = runner.invoke(app, ["doctor"])
        assert "built-in defaults" in result.output

    def test_codex_exec_subcommand_validated(self, tmp_path: Path):
        """doctor validates the codex exec subcommand."""
        with self._patch_doctor(tmp_path, subcmd_rv=True):
            result = runner.invoke(app, ["doctor"])
        assert "subcommand" in result.output
        assert result.exit_code == 0

    def test_codex_exec_subcommand_failed(self, tmp_path: Path):
        """doctor reports failure when codex exec subcommand fails."""
        with self._patch_doctor(tmp_path, subcmd_rv=False):
            result = runner.invoke(app, ["doctor"])
        assert "FAILED" in result.output
        assert result.exit_code == 1

    def test_codex_auth_logged_in(self, tmp_path: Path):
        """doctor reports codex auth as logged in when exit 0."""
        with self._patch_doctor(tmp_path, auth_rv=True):
            result = runner.invoke(app, ["doctor"])
        assert "logged in" in result.output
        assert result.exit_code == 0

    def test_codex_auth_not_logged_in(self, tmp_path: Path):
        """doctor reports codex auth failure."""
        with self._patch_doctor(tmp_path, auth_rv=False):
            result = runner.invoke(app, ["doctor"])
        assert "NOT logged in" in result.output
        assert result.exit_code == 1

    def test_codex_auth_unknown(self, tmp_path: Path):
        """doctor reports 'unknown' when codex login status cannot be run."""
        with self._patch_doctor(tmp_path, auth_rv=None):
            result = runner.invoke(app, ["doctor"])
        assert "unknown" in result.output

    def test_extra_args_redacted_in_output(self, tmp_path: Path):
        """doctor redacts sensitive values in extra_args (e.g. --api-key)."""
        cfg_content = (
            "tools:\n  claude:\n    command: ['claude']\n    extra_args: ['-p', '--api-key', 'sk-secret-123']\n"
        )
        cfg_file = tmp_path / ".council.yml"
        cfg_file.write_text(cfg_content, encoding="utf-8")
        with self._patch_doctor(tmp_path, version_rv="v1.0"):
            result = runner.invoke(app, ["doctor"])
        assert "sk-secret-123" not in result.output
        assert "***REDACTED***" in result.output

    def test_config_flag(self, tmp_path: Path):
        """doctor --config loads the specified config file."""
        cfg_content = "tools:\n  claude:\n    command: ['claude']\n    extra_args: ['-p']\n"
        cfg_file = tmp_path / "custom.yml"
        cfg_file.write_text(cfg_content, encoding="utf-8")
        with self._patch_doctor(tmp_path, version_rv="v1.0"):
            result = runner.invoke(app, ["doctor", "--config", str(cfg_file)])
        assert str(cfg_file) in result.output


class TestListRuns:
    """Tests for `council list`."""

    def test_empty_runs_dir(self, tmp_path: Path):
        """list shows a message when no runs exist."""
        runs = tmp_path / "runs"
        runs.mkdir()
        result = runner.invoke(app, ["list", "--outdir", str(runs)])
        assert "No council runs found" in result.output

    def test_missing_runs_dir(self, tmp_path: Path):
        """list exits 1 when runs directory doesn't exist."""
        result = runner.invoke(app, ["list", "--outdir", str(tmp_path / "missing")])
        assert result.exit_code == 1

    def test_lists_runs_with_state(self, tmp_path: Path):
        """list shows runs that have state.json."""
        import json

        runs = tmp_path / "runs"
        runs.mkdir()

        # Create two run directories with state.json.
        for name, mode, status in [
            ("2025-06-15_143022_fix_auth", "fix", "completed"),
            ("2025-06-16_100000_feature_dark", "feature", "failed"),
        ]:
            d = runs / name
            d.mkdir()
            state = {
                "mode": mode,
                "status": status,
                "rounds": {
                    "0_generate": {"status": "ok"},
                    "1_claude_improve": {"status": "ok"},
                    "2_codex_critique": {"status": "ok" if status == "completed" else "failed"},
                    "3_claude_finalize": {"status": "ok" if status == "completed" else "pending"},
                },
            }
            (d / "state.json").write_text(json.dumps(state), encoding="utf-8")

        result = runner.invoke(app, ["list", "--outdir", str(runs)])
        assert result.exit_code == 0
        assert "fix" in result.output
        assert "feature" in result.output
        assert "completed" in result.output
        assert "failed" in result.output

    def test_limit_flag(self, tmp_path: Path):
        """list respects --limit flag."""
        import json

        runs = tmp_path / "runs"
        runs.mkdir()

        for i in range(5):
            d = runs / f"run_{i:03d}"
            d.mkdir()
            (d / "state.json").write_text(
                json.dumps(
                    {
                        "mode": "fix",
                        "status": "completed",
                        "rounds": {"0_generate": {"status": "ok"}},
                    }
                ),
                encoding="utf-8",
            )

        result = runner.invoke(app, ["list", "--outdir", str(runs), "--limit", "2"])
        assert result.exit_code == 0
        assert "3 more runs not shown" in result.output


class TestAskCommand:
    """Tests for `council ask`."""

    def test_ask_requires_question(self):
        """ask with no question should fail."""
        with patch("council.cli._run"):
            result = runner.invoke(app, ["ask"])
        assert result.exit_code != 0

    def test_ask_sets_ask_mode(self):
        """ask should set mode=ASK and pass the question as the task."""
        with patch("council.cli._run") as mock_run:
            result = runner.invoke(app, ["ask", "Explain what this repo does"])
        assert result.exit_code == 0
        opts = mock_run.call_args[0][0]
        from council.types import Mode

        assert opts.mode == Mode.ASK
        assert opts.task == "Explain what this repo does"

    def test_ask_defaults_to_no_diff(self):
        """ask should default to --diff none (no diffs for questions)."""
        with patch("council.cli._run") as mock_run:
            result = runner.invoke(app, ["ask", "What does config.py do?"])
        assert result.exit_code == 0
        opts = mock_run.call_args[0][0]
        from council.types import DiffScope

        assert opts.diff_scope == DiffScope.NONE

    def test_ask_accepts_include(self):
        """ask should accept --include to focus on specific files."""
        with patch("council.cli._run") as mock_run:
            result = runner.invoke(
                app, ["ask", "Explain this file", "--include", "src/council/config.py"]
            )
        assert result.exit_code == 0
        opts = mock_run.call_args[0][0]
        assert "src/council/config.py" in opts.include_paths

    def test_ask_with_task_file(self, tmp_path: Path):
        """ask should accept --task-file."""
        q_file = tmp_path / "question.txt"
        q_file.write_text("How does the pipeline work?", encoding="utf-8")
        with patch("council.cli._run") as mock_run:
            result = runner.invoke(app, ["ask", "--task-file", str(q_file)])
        assert result.exit_code == 0
        opts = mock_run.call_args[0][0]
        assert "pipeline" in opts.task.lower()


class TestAskPrompt:
    """Tests for the ASK mode prompt template."""

    def test_ask_prompt_uses_answer_format(self):
        """ASK mode should produce answer-oriented output format, not patch format."""
        from council.prompts import round0_prompt
        from council.types import Mode

        prompt = round0_prompt(Mode.ASK, "What does this repo do?", "some context")
        assert "### Answer" in prompt
        assert "### Key Details" in prompt
        # Should NOT include patch-oriented sections.
        assert "### Patch" not in prompt
        assert "### Rollback Plan" not in prompt

    def test_ask_prompt_contains_question_framing(self):
        """ASK mode frame should mention answering a question."""
        from council.prompts import round0_prompt
        from council.types import Mode

        prompt = round0_prompt(Mode.ASK, "Explain the architecture", "context here")
        assert "answering a question" in prompt.lower()

    def test_fix_prompt_still_uses_patch_format(self):
        """FIX mode should still produce patch-oriented output format."""
        from council.prompts import round0_prompt
        from council.types import Mode

        prompt = round0_prompt(Mode.FIX, "Fix the bug", "some context")
        assert "### Patch" in prompt
        assert "### Answer" not in prompt
