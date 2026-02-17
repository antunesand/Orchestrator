"""Tests for the safe-apply workflow (apply module + CLI command)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from council.apply import (
    apply_patch,
    check_patch,
    create_branch,
    load_patch,
    post_apply_diff,
    show_diff_preview,
    working_tree_clean,
)

# ---------------------------------------------------------------------------
# load_patch
# ---------------------------------------------------------------------------


class TestLoadPatch:
    def test_loads_existing_patch(self, tmp_path: Path):
        final = tmp_path / "final"
        final.mkdir()
        patch_file = final / "final.patch"
        patch_file.write_text("--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-old\n+new\n")
        result = load_patch(tmp_path)
        assert result is not None
        assert "+new" in result

    def test_returns_none_when_missing(self, tmp_path: Path):
        assert load_patch(tmp_path) is None

    def test_returns_none_when_empty(self, tmp_path: Path):
        final = tmp_path / "final"
        final.mkdir()
        (final / "final.patch").write_text("   \n\n")
        assert load_patch(tmp_path) is None

    def test_returns_none_for_no_final_dir(self, tmp_path: Path):
        assert load_patch(tmp_path) is None


# ---------------------------------------------------------------------------
# check_patch
# ---------------------------------------------------------------------------


class TestCheckPatch:
    def test_clean_patch(self, tmp_path: Path):
        """check_patch returns True when git apply --check succeeds."""
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="checking...")
        with patch("council.apply.subprocess.run", return_value=mock_result):
            ok, detail = check_patch("patch content", tmp_path)
        assert ok is True

    def test_failing_patch(self, tmp_path: Path):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error: patch does not apply"
        )
        with patch("council.apply.subprocess.run", return_value=mock_result):
            ok, detail = check_patch("bad patch", tmp_path)
        assert ok is False
        assert "does not apply" in detail

    def test_git_not_found(self, tmp_path: Path):
        with patch("council.apply.subprocess.run", side_effect=FileNotFoundError):
            ok, detail = check_patch("patch", tmp_path)
        assert ok is False
        assert "git not found" in detail

    def test_timeout(self, tmp_path: Path):
        with patch("council.apply.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=30)):
            ok, detail = check_patch("patch", tmp_path)
        assert ok is False
        assert "timed out" in detail


# ---------------------------------------------------------------------------
# apply_patch
# ---------------------------------------------------------------------------


class TestApplyPatch:
    def test_successful_apply(self, tmp_path: Path):
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="applied ok")
        with patch("council.apply.subprocess.run", return_value=mock_result):
            ok, detail = apply_patch("patch content", tmp_path)
        assert ok is True
        assert "applied" in detail

    def test_failed_apply(self, tmp_path: Path):
        mock_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="conflict")
        with patch("council.apply.subprocess.run", return_value=mock_result):
            ok, detail = apply_patch("bad patch", tmp_path)
        assert ok is False
        assert "conflict" in detail

    def test_git_not_found(self, tmp_path: Path):
        with patch("council.apply.subprocess.run", side_effect=FileNotFoundError):
            ok, detail = apply_patch("patch", tmp_path)
        assert ok is False


# ---------------------------------------------------------------------------
# create_branch
# ---------------------------------------------------------------------------


class TestCreateBranch:
    def test_success(self, tmp_path: Path):
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("council.apply._git", return_value=mock_result):
            ok, detail = create_branch("council/fix-auth", tmp_path)
        assert ok is True
        assert "council/fix-auth" in detail

    def test_branch_exists(self, tmp_path: Path):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr="fatal: branch already exists"
        )
        with patch("council.apply._git", return_value=mock_result):
            ok, detail = create_branch("main", tmp_path)
        assert ok is False
        assert "already exists" in detail


# ---------------------------------------------------------------------------
# working_tree_clean
# ---------------------------------------------------------------------------


class TestWorkingTreeClean:
    def test_clean(self, tmp_path: Path):
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("council.apply._git", return_value=mock_result):
            clean, output = working_tree_clean(tmp_path)
        assert clean is True

    def test_dirty(self, tmp_path: Path):
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=" M src/app.py\n?? new.py\n", stderr="")
        with patch("council.apply._git", return_value=mock_result):
            clean, output = working_tree_clean(tmp_path)
        assert clean is False
        assert "src/app.py" in output


# ---------------------------------------------------------------------------
# post_apply_diff
# ---------------------------------------------------------------------------


class TestPostApplyDiff:
    def test_returns_diff(self, tmp_path: Path):
        mock_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-old\n+new",
            stderr="",
        )
        with patch("council.apply._git", return_value=mock_result):
            diff = post_apply_diff(tmp_path)
        assert "diff --git" in diff

    def test_returns_empty_on_error(self, tmp_path: Path):
        with patch("council.apply._git", side_effect=FileNotFoundError):
            diff = post_apply_diff(tmp_path)
        assert diff == ""


# ---------------------------------------------------------------------------
# show_diff_preview
# ---------------------------------------------------------------------------


class TestShowDiffPreview:
    def test_does_not_raise(self, capsys):
        """show_diff_preview should print without raising."""
        show_diff_preview("--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-old\n+new\n")
        # Rich prints to stderr console, so we just confirm no exception.


# ---------------------------------------------------------------------------
# CLI integration (via typer)
# ---------------------------------------------------------------------------


class TestApplyCLI:
    """Test the CLI apply command via CliRunner."""

    def test_missing_run_dir(self):
        from typer.testing import CliRunner

        from council.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["apply", "/nonexistent/path"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "error" in result.output.lower()

    def test_no_patch_file(self, tmp_path: Path):
        from typer.testing import CliRunner

        from council.cli import app

        # Create a run dir without final.patch
        run = tmp_path / "run"
        run.mkdir()
        (run / "final").mkdir()

        runner = CliRunner()
        result = runner.invoke(app, ["apply", str(run)])
        assert result.exit_code != 0
        assert "no final.patch" in result.output.lower() or "error" in result.output.lower()

    def test_check_mode_success(self, tmp_path: Path):
        from typer.testing import CliRunner

        from council.cli import app

        run = tmp_path / "run"
        run.mkdir()
        (run / "final").mkdir()
        (run / "final" / "final.patch").write_text("--- a/f\n+++ b/f\n")

        mock_check = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="ok")
        runner = CliRunner()
        with (
            patch("council.cli.find_repo_root", return_value=tmp_path),
            patch("council.apply.subprocess.run", return_value=mock_check),
        ):
            result = runner.invoke(app, ["apply", str(run), "--check"])
        assert result.exit_code == 0
        assert "OK" in result.output.upper() or "ok" in result.output.lower()

    def test_check_mode_failure(self, tmp_path: Path):
        from typer.testing import CliRunner

        from council.cli import app

        run = tmp_path / "run"
        run.mkdir()
        (run / "final").mkdir()
        (run / "final" / "final.patch").write_text("--- a/f\n+++ b/f\n")

        mock_check = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="does not apply")
        runner = CliRunner()
        with (
            patch("council.cli.find_repo_root", return_value=tmp_path),
            patch("council.apply.subprocess.run", return_value=mock_check),
        ):
            result = runner.invoke(app, ["apply", str(run), "--check"])
        assert result.exit_code != 0

    def test_apply_with_yes(self, tmp_path: Path):
        from typer.testing import CliRunner

        from council.cli import app

        run = tmp_path / "run"
        run.mkdir()
        (run / "final").mkdir()
        (run / "final" / "final.patch").write_text("--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-old\n+new\n")

        # Mock both check and apply to succeed.
        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="ok")

        mock_git_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n",
            stderr="",
        )

        runner = CliRunner()
        with (
            patch("council.cli.find_repo_root", return_value=tmp_path),
            patch("council.cli.working_tree_clean", return_value=(True, "")),
            patch("council.apply.subprocess.run", side_effect=mock_run),
            patch("council.apply._git", return_value=mock_git_result),
        ):
            result = runner.invoke(app, ["apply", str(run), "--yes"])
        assert result.exit_code == 0
        assert "applied" in result.output.lower()

    def test_apply_to_branch(self, tmp_path: Path):
        from typer.testing import CliRunner

        from council.cli import app

        run = tmp_path / "run"
        run.mkdir()
        (run / "final").mkdir()
        (run / "final" / "final.patch").write_text("--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-old\n+new\n")

        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="ok")

        mock_diff = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="diff --git a/f.py b/f.py\n",
            stderr="",
        )

        runner = CliRunner()
        with (
            patch("council.cli.find_repo_root", return_value=tmp_path),
            patch("council.cli.working_tree_clean", return_value=(True, "")),
            patch("council.apply.subprocess.run", side_effect=mock_run),
            patch("council.apply._git", return_value=mock_diff),
        ):
            result = runner.invoke(app, ["apply", str(run), "--apply-to", "fix/auth", "--yes"])
        assert result.exit_code == 0

    def test_not_in_git_repo(self, tmp_path: Path):
        from typer.testing import CliRunner

        from council.cli import app

        run = tmp_path / "run"
        run.mkdir()
        (run / "final").mkdir()
        (run / "final" / "final.patch").write_text("--- a/f\n+++ b/f\n")

        runner = CliRunner()
        with patch("council.cli.find_repo_root", return_value=None):
            result = runner.invoke(app, ["apply", str(run), "--yes"])
        assert result.exit_code != 0
        assert "git repository" in result.output.lower() or "error" in result.output.lower()

    def test_dirty_tree_blocks_apply(self, tmp_path: Path):
        """Apply should refuse on a dirty working tree without --force."""
        from typer.testing import CliRunner

        from council.cli import app

        run = tmp_path / "run"
        run.mkdir()
        (run / "final").mkdir()
        (run / "final" / "final.patch").write_text("--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-old\n+new\n")

        runner = CliRunner()
        with (
            patch("council.cli.find_repo_root", return_value=tmp_path),
            patch("council.cli.working_tree_clean", return_value=(False, " M src/app.py")),
        ):
            result = runner.invoke(app, ["apply", str(run), "--yes"])
        assert result.exit_code != 0
        assert "uncommitted" in result.output.lower()

    def test_dirty_tree_with_force(self, tmp_path: Path):
        """Apply should proceed on a dirty tree when --force is given."""
        from typer.testing import CliRunner

        from council.cli import app

        run = tmp_path / "run"
        run.mkdir()
        (run / "final").mkdir()
        (run / "final" / "final.patch").write_text("--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-old\n+new\n")

        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="ok")

        mock_diff = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="diff --git a/f.py b/f.py\n",
            stderr="",
        )

        runner = CliRunner()
        with (
            patch("council.cli.find_repo_root", return_value=tmp_path),
            patch("council.cli.working_tree_clean", return_value=(False, " M src/app.py")),
            patch("council.apply.subprocess.run", side_effect=mock_run),
            patch("council.apply._git", return_value=mock_diff),
        ):
            result = runner.invoke(app, ["apply", str(run), "--yes", "--force"])
        assert result.exit_code == 0
        assert "warning" in result.output.lower()

    def test_check_mode_skips_dirty_tree_check(self, tmp_path: Path):
        """--check (read-only) should not care about dirty working tree."""
        from typer.testing import CliRunner

        from council.cli import app

        run = tmp_path / "run"
        run.mkdir()
        (run / "final").mkdir()
        (run / "final" / "final.patch").write_text("--- a/f\n+++ b/f\n")

        mock_check = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="ok")
        runner = CliRunner()
        with (
            patch("council.cli.find_repo_root", return_value=tmp_path),
            patch("council.apply.subprocess.run", return_value=mock_check),
        ):
            # Note: no working_tree_clean mock — if it were called it would error.
            result = runner.invoke(app, ["apply", str(run), "--check"])
        assert result.exit_code == 0
