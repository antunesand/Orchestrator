"""Shared fixtures for council tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from council.types import ContextMode, DiffScope, Mode, RunOptions


@pytest.fixture
def tmp_run_dir(tmp_path: Path) -> Path:
    """Provide a temporary directory for run outputs."""
    return tmp_path / "runs"


@pytest.fixture
def basic_opts(tmp_run_dir: Path) -> RunOptions:
    """Provide a basic RunOptions for testing."""
    return RunOptions(
        mode=Mode.FIX,
        task="Fix the broken authentication handler",
        context_mode=ContextMode.NONE,
        diff_scope=DiffScope.NONE,
        outdir=tmp_run_dir,
        tools=["claude", "codex"],
    )
