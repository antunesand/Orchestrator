"""Safe-apply workflow: apply patches from council runs to the repository.

Provides ``--apply`` (interactive confirmation), ``--apply-to <branch>``
(create a branch, apply the patch, don't commit), and an optional diff
preview.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from rich.console import Console
from rich.syntax import Syntax

_console = Console(stderr=True, highlight=False)


def _git(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a git command, returning the CompletedProcess."""
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=cwd,
        check=check,
    )


def _repo_root(cwd: Path | None = None) -> Path | None:
    """Find the nearest git repo root."""
    try:
        result = _git(["rev-parse", "--show-toplevel"], cwd=cwd, check=False)
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def load_patch(run_dir: Path) -> str | None:
    """Load the patch file from a run directory.

    Returns the patch text, or None if no patch exists.
    """
    patch_path = run_dir / "final" / "final.patch"
    if not patch_path.is_file():
        return None
    text = patch_path.read_text(encoding="utf-8")
    return text if text.strip() else None


def check_patch(patch: str, repo_root: Path) -> tuple[bool, str]:
    """Dry-run ``git apply --check`` to see if the patch applies cleanly.

    Returns ``(applies_cleanly, detail_message)``.
    """
    try:
        result = subprocess.run(
            ["git", "apply", "--check", "--verbose", "-"],
            input=patch,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=repo_root,
        )
        if result.returncode == 0:
            detail = result.stderr.strip() or result.stdout.strip() or "patch applies cleanly"
            return True, detail
        detail = (result.stderr or result.stdout).strip()
        return False, detail
    except FileNotFoundError:
        return False, "git not found on PATH"
    except subprocess.TimeoutExpired:
        return False, "git apply --check timed out"


def apply_patch(patch: str, repo_root: Path) -> tuple[bool, str]:
    """Apply the patch to the working tree via ``git apply``.

    Returns ``(success, detail_message)``.
    """
    try:
        result = subprocess.run(
            ["git", "apply", "--verbose", "-"],
            input=patch,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=repo_root,
        )
        if result.returncode == 0:
            detail = result.stderr.strip() or result.stdout.strip() or "patch applied successfully"
            return True, detail
        detail = (result.stderr or result.stdout).strip()
        return False, detail
    except FileNotFoundError:
        return False, "git not found on PATH"
    except subprocess.TimeoutExpired:
        return False, "git apply timed out"


def create_branch(branch_name: str, repo_root: Path) -> tuple[bool, str]:
    """Create and checkout a new branch.

    Returns ``(success, detail_message)``.
    """
    try:
        result = _git(["checkout", "-b", branch_name], cwd=repo_root, check=False)
        if result.returncode == 0:
            return True, f"created and switched to branch '{branch_name}'"
        detail = (result.stderr or result.stdout).strip()
        return False, detail
    except FileNotFoundError:
        return False, "git not found on PATH"
    except subprocess.TimeoutExpired:
        return False, "git checkout timed out"


def show_diff_preview(patch: str) -> None:
    """Print a syntax-highlighted diff preview to the console."""
    syntax = Syntax(patch, "diff", theme="monokai", line_numbers=True)
    _console.print(syntax)


def working_tree_clean(repo_root: Path) -> tuple[bool, str]:
    """Check whether the working tree is clean.

    Returns ``(is_clean, status_output)``.
    """
    try:
        result = _git(["status", "--porcelain"], cwd=repo_root, check=False)
        output = result.stdout.strip()
        return (not output, output)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, "could not run git status"


def post_apply_diff(repo_root: Path) -> str:
    """Return ``git diff`` after applying, for review."""
    try:
        result = _git(["diff"], cwd=repo_root, check=False)
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
