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

    def _patch_doctor(self, tmp_path, which_rv="/usr/bin/fake", version_rv="v1.0",
                      subcmd_rv=True, auth_rv=True):
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
