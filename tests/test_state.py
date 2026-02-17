"""Tests for the checkpoint state module."""

from __future__ import annotations

import json
from pathlib import Path

from council.state import (
    ROUND_NAMES,
    get_failed_rounds,
    get_resume_point,
    init_state,
    load_state,
    mark_finished,
    update_round,
)
from council.types import RoundStatus


class TestInitState:
    def test_creates_state_file(self, tmp_path: Path):
        state = init_state(tmp_path, "fix", "fix the bug", ["claude", "codex"])
        assert (tmp_path / "state.json").exists()
        assert state["mode"] == "fix"
        assert state["status"] == "running"
        assert state["tools"] == ["claude", "codex"]

    def test_all_rounds_pending(self, tmp_path: Path):
        state = init_state(tmp_path, "feature", "add dark mode", ["claude"])
        for name in ROUND_NAMES:
            assert state["rounds"][name]["status"] == "pending"

    def test_task_preview_truncated(self, tmp_path: Path):
        long_task = "x" * 500
        state = init_state(tmp_path, "fix", long_task, ["claude"])
        assert len(state["task_preview"]) == 200


class TestUpdateRound:
    def test_updates_status(self, tmp_path: Path):
        state = init_state(tmp_path, "fix", "task", ["claude"])
        update_round(tmp_path, state, "0_generate", RoundStatus.OK, {"claude": "ok"})
        assert state["rounds"]["0_generate"]["status"] == "ok"
        assert state["rounds"]["0_generate"]["tools"]["claude"] == "ok"

    def test_persists_to_disk(self, tmp_path: Path):
        state = init_state(tmp_path, "fix", "task", ["claude"])
        update_round(tmp_path, state, "0_generate", RoundStatus.FAILED, {"claude": "failed"})
        reloaded = json.loads((tmp_path / "state.json").read_text())
        assert reloaded["rounds"]["0_generate"]["status"] == "failed"


class TestMarkFinished:
    def test_marks_completed(self, tmp_path: Path):
        state = init_state(tmp_path, "fix", "task", ["claude"])
        mark_finished(tmp_path, state, status="completed")
        assert state["status"] == "completed"
        assert state["finished_at"] is not None

    def test_marks_failed(self, tmp_path: Path):
        state = init_state(tmp_path, "fix", "task", ["claude"])
        mark_finished(tmp_path, state, status="failed")
        assert state["status"] == "failed"


class TestLoadState:
    def test_loads_existing(self, tmp_path: Path):
        init_state(tmp_path, "review", "review code", ["claude", "codex"])
        loaded = load_state(tmp_path)
        assert loaded["mode"] == "review"

    def test_raises_if_missing(self, tmp_path: Path):
        import pytest

        with pytest.raises(FileNotFoundError):
            load_state(tmp_path)


class TestGetResumePoint:
    def test_returns_first_non_ok(self, tmp_path: Path):
        state = init_state(tmp_path, "fix", "task", ["claude"])
        update_round(tmp_path, state, "0_generate", RoundStatus.OK)
        update_round(tmp_path, state, "1_claude_improve", RoundStatus.OK)
        # 2_codex_critique is still pending.
        assert get_resume_point(state) == "2_codex_critique"

    def test_returns_none_when_all_ok(self, tmp_path: Path):
        state = init_state(tmp_path, "fix", "task", ["claude"])
        for name in ROUND_NAMES:
            update_round(tmp_path, state, name, RoundStatus.OK)
        assert get_resume_point(state) is None

    def test_returns_failed_round(self, tmp_path: Path):
        state = init_state(tmp_path, "fix", "task", ["claude"])
        update_round(tmp_path, state, "0_generate", RoundStatus.OK)
        update_round(tmp_path, state, "1_claude_improve", RoundStatus.FAILED)
        assert get_resume_point(state) == "1_claude_improve"


class TestGetFailedRounds:
    def test_returns_failed_rounds(self, tmp_path: Path):
        state = init_state(tmp_path, "fix", "task", ["claude"])
        update_round(tmp_path, state, "0_generate", RoundStatus.OK)
        update_round(tmp_path, state, "1_claude_improve", RoundStatus.FAILED)
        update_round(tmp_path, state, "2_codex_critique", RoundStatus.OK)
        update_round(tmp_path, state, "3_claude_finalize", RoundStatus.FAILED)
        assert get_failed_rounds(state) == ["1_claude_improve", "3_claude_finalize"]

    def test_returns_empty_when_none_failed(self, tmp_path: Path):
        state = init_state(tmp_path, "fix", "task", ["claude"])
        for name in ROUND_NAMES:
            update_round(tmp_path, state, name, RoundStatus.OK)
        assert get_failed_rounds(state) == []
