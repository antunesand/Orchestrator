"""Checkpoint state management for resumable pipeline runs.

Writes ``state.json`` after every round so that interrupted runs can be
resumed from the last successful checkpoint.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from council.types import RoundStatus

# Ordered list of all pipeline rounds.
ROUND_NAMES = [
    "0_generate",
    "1_claude_improve",
    "2_codex_critique",
    "3_claude_finalize",
]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def init_state(
    run_dir: Path,
    mode: str,
    task_preview: str,
    tools: list[str],
) -> dict[str, Any]:
    """Create and persist an initial state.json for a new run."""
    state: dict[str, Any] = {
        "version": "1.0",
        "mode": mode,
        "task_preview": task_preview[:200],
        "tools": tools,
        "started_at": _now_iso(),
        "finished_at": None,
        "status": "running",
        "rounds": {
            name: {"status": RoundStatus.PENDING.value, "tools": {}}
            for name in ROUND_NAMES
        },
    }
    _write(run_dir, state)
    return state


def update_round(
    run_dir: Path,
    state: dict[str, Any],
    round_name: str,
    status: RoundStatus,
    tool_statuses: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Update the status of a single round and persist."""
    state["rounds"][round_name]["status"] = status.value
    if tool_statuses:
        state["rounds"][round_name]["tools"] = tool_statuses
    _write(run_dir, state)
    return state


def mark_finished(
    run_dir: Path,
    state: dict[str, Any],
    status: str = "completed",
) -> dict[str, Any]:
    """Mark the entire run as finished (completed or failed)."""
    state["finished_at"] = _now_iso()
    state["status"] = status
    _write(run_dir, state)
    return state


def load_state(run_dir: Path) -> dict[str, Any]:
    """Load state.json from a run directory.

    Raises FileNotFoundError if the state file does not exist.
    """
    state_path = run_dir / "state.json"
    if not state_path.exists():
        raise FileNotFoundError(f"No state.json found in {run_dir}")
    return json.loads(state_path.read_text(encoding="utf-8"))


def get_resume_point(state: dict[str, Any]) -> str | None:
    """Determine which round to resume from.

    Returns the name of the first round that is not ``ok``, or ``None``
    if all rounds completed successfully.
    """
    for name in ROUND_NAMES:
        rnd = state["rounds"].get(name, {})
        if rnd.get("status") != RoundStatus.OK.value:
            return name
    return None


def get_failed_rounds(state: dict[str, Any]) -> list[str]:
    """Return list of round names that have ``failed`` status."""
    return [
        name
        for name in ROUND_NAMES
        if state["rounds"].get(name, {}).get("status") == RoundStatus.FAILED.value
    ]


def _write(run_dir: Path, state: dict[str, Any]) -> None:
    """Atomically write state.json."""
    state_path = run_dir / "state.json"
    tmp_path = run_dir / "state.json.tmp"
    tmp_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    tmp_path.replace(state_path)
