"""Tests for the Council Web UI FastAPI backend."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from council.ui.api import (
    JobRequest,
    _build_command,
    _Job,
    _run_job,
    app,
    list_runs,
    parse_manifest,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_ok(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# parse_manifest
# ---------------------------------------------------------------------------


class TestParseManifest:
    def test_manifest_json(self, tmp_path: Path):
        """Manifest data is extracted from manifest.json."""
        run_dir = tmp_path / "2025-01-01_000000_abc_test"
        run_dir.mkdir(parents=True)
        manifest = {
            "start_time": "2025-01-01T00:00:00",
            "mode": "fix",
            "task_preview": "Fix auth",
            "total_duration_sec": 42.5,
        }
        (run_dir / "manifest.json").write_text(json.dumps(manifest))
        (run_dir / "state.json").write_text(json.dumps({"status": "completed"}))

        result = parse_manifest(run_dir)
        assert result["mode"] == "fix"
        assert result["task_preview"] == "Fix auth"
        assert result["timestamp"] == "2025-01-01T00:00:00"
        assert result["status"] == "completed"

    def test_state_json_fallback(self, tmp_path: Path):
        """Falls back to state.json when no manifest.json."""
        run_dir = tmp_path / "2025-01-01_000000_abc_test"
        run_dir.mkdir(parents=True)
        state = {
            "mode": "feature",
            "task_preview": "Add endpoint",
            "status": "running",
            "started_at": "2025-01-01T00:00:00",
        }
        (run_dir / "state.json").write_text(json.dumps(state))

        result = parse_manifest(run_dir)
        assert result["mode"] == "feature"
        assert result["status"] == "running"

    def test_no_files(self, tmp_path: Path):
        """Handles missing manifest and state gracefully."""
        run_dir = tmp_path / "empty"
        run_dir.mkdir(parents=True)

        result = parse_manifest(run_dir)
        assert result["name"] == "empty"
        assert result["status"] == "unknown"

    def test_corrupt_manifest(self, tmp_path: Path):
        """Handles corrupt JSON gracefully."""
        run_dir = tmp_path / "corrupt"
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text("{bad json")

        result = parse_manifest(run_dir)
        assert result["name"] == "corrupt"


# ---------------------------------------------------------------------------
# list_runs
# ---------------------------------------------------------------------------


class TestListRuns:
    def test_empty_dir(self, tmp_path: Path):
        assert list_runs(outdir=tmp_path) == []

    def test_nonexistent_dir(self, tmp_path: Path):
        assert list_runs(outdir=tmp_path / "nope") == []

    def test_lists_and_limits(self, tmp_path: Path):
        for i in range(5):
            d = tmp_path / f"run_{i:03d}"
            d.mkdir()
            (d / "state.json").write_text(json.dumps({"status": "completed", "mode": "fix"}))

        result = list_runs(outdir=tmp_path, limit=3)
        assert len(result) == 3

    def test_sorts_newest_first(self, tmp_path: Path):
        for name in ["aaa_old", "zzz_new"]:
            d = tmp_path / name
            d.mkdir()
            (d / "state.json").write_text(json.dumps({"status": "completed"}))

        result = list_runs(outdir=tmp_path)
        assert result[0]["name"] == "zzz_new"


# ---------------------------------------------------------------------------
# _build_command
# ---------------------------------------------------------------------------


class TestBuildCommand:
    def test_basic(self):
        req = JobRequest(mode="fix", task="Fix auth bug")
        cmd = _build_command(req)
        assert "fix" in cmd
        assert "Fix auth bug" in cmd

    def test_all_options(self):
        req = JobRequest(
            mode="review",
            task="Review changes",
            diff="staged",
            context="auto",
            include=["src/a.py"],
            include_glob=["*.py"],
            include_from_diff=True,
            no_save=True,
            redact_paths=True,
            smart_context=True,
            structured_review=True,
            claude_n=3,
            codex_n=2,
            outdir="custom_runs",
        )
        cmd = _build_command(req)
        assert "--diff" in cmd
        assert "staged" in cmd
        assert "--include" in cmd
        assert "--include-glob" in cmd
        assert "--include-from-diff" in cmd
        assert "--no-save" in cmd
        assert "--redact-paths" in cmd
        assert "--smart-context" in cmd
        assert "--structured-review" in cmd
        assert "--claude-n" in cmd
        assert "3" in cmd
        assert "--codex-n" in cmd
        assert "2" in cmd
        assert "--outdir" in cmd
        assert "custom_runs" in cmd

    def test_false_smart_context(self):
        req = JobRequest(mode="fix", task="t", smart_context=False)
        cmd = _build_command(req)
        assert "--no-smart-context" in cmd

    def test_none_smart_context_omitted(self):
        req = JobRequest(mode="fix", task="t", smart_context=None)
        cmd = _build_command(req)
        assert "--smart-context" not in cmd
        assert "--no-smart-context" not in cmd


# ---------------------------------------------------------------------------
# Job lifecycle (mocked subprocess)
# ---------------------------------------------------------------------------


class TestJobLifecycle:
    def test_job_status_pending(self):
        job = _Job(job_id="test1", mode="fix", task="t", cmd=["echo"])
        assert job.status == "pending"
        assert job.exit_code is None

    def test_job_log_buffer(self):
        job = _Job(job_id="test2", mode="fix", task="t", cmd=["echo"])
        job.append_log("line 1\n")
        job.append_log("line 2\n")
        assert "line 1\n" in job.get_log()
        assert "line 2\n" in job.get_log()

    def test_run_job_detects_run_dir(self):
        """_run_job should parse RUN_DIR= from subprocess output."""
        fake_proc = MagicMock(spec=subprocess.Popen)
        fake_proc.stdout = iter(["Starting...\n", "RUN_DIR=/tmp/runs/test_run\n", "Done\n"])
        fake_proc.wait.return_value = 0
        fake_proc.poll.return_value = 0

        with patch("council.ui.api.subprocess.Popen", return_value=fake_proc):
            job = _Job(job_id="test3", mode="fix", task="t", cmd=["council", "fix", "t"])
            _run_job(job)

        assert job.run_dir == "/tmp/runs/test_run"
        assert "Starting..." in job.get_log()
        assert "Done" in job.get_log()


# ---------------------------------------------------------------------------
# Endpoint integration
# ---------------------------------------------------------------------------


class TestEndpoints:
    def test_get_runs_empty(self):
        """GET /runs returns an empty list when no runs dir exists."""
        with patch("council.ui.api._find_runs_dir", return_value=Path("/nonexistent")):
            resp = client.get("/runs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_job_invalid_mode(self):
        resp = client.post("/jobs", json={"mode": "delete", "task": "bad"})
        assert resp.status_code == 400

    def test_create_and_get_job(self):
        with patch("council.ui.api.threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            resp = client.post("/jobs", json={"mode": "fix", "task": "test task"})

        assert resp.status_code == 200
        data = resp.json()
        job_id = data["job_id"]
        assert job_id

        # GET /jobs/{job_id}
        resp2 = client.get(f"/jobs/{job_id}")
        assert resp2.status_code == 200
        assert resp2.json()["mode"] == "fix"

        # GET /jobs/{job_id}/logs
        resp3 = client.get(f"/jobs/{job_id}/logs")
        assert resp3.status_code == 200
        assert "logs" in resp3.json()

    def test_get_job_not_found(self):
        resp = client.get("/jobs/nonexistent")
        assert resp.status_code == 404

    def test_get_job_logs_not_found(self):
        resp = client.get("/jobs/nonexistent/logs")
        assert resp.status_code == 404

    def test_get_run_final(self, tmp_path: Path):
        run_dir = tmp_path / "test_run"
        final_dir = run_dir / "final"
        final_dir.mkdir(parents=True)
        (final_dir / "final.md").write_text("# Final result")

        with patch("council.ui.api._find_runs_dir", return_value=tmp_path):
            resp = client.get("/runs/test_run/final")
        assert resp.status_code == 200
        assert resp.json()["final"] == "# Final result"

    def test_get_run_final_not_found(self):
        with patch("council.ui.api._find_runs_dir", return_value=Path("/nonexistent")):
            resp = client.get("/runs/nope/final")
        assert resp.status_code == 404

    def test_get_run_patch(self, tmp_path: Path):
        run_dir = tmp_path / "test_run"
        final_dir = run_dir / "final"
        final_dir.mkdir(parents=True)
        (final_dir / "final.patch").write_text("--- a/foo\n+++ b/foo")

        with patch("council.ui.api._find_runs_dir", return_value=tmp_path):
            resp = client.get("/runs/test_run/patch")
        assert resp.status_code == 200
        assert "--- a/foo" in resp.json()["patch"]

    def test_get_run_patch_missing(self, tmp_path: Path):
        run_dir = tmp_path / "test_run"
        final_dir = run_dir / "final"
        final_dir.mkdir(parents=True)

        with patch("council.ui.api._find_runs_dir", return_value=tmp_path):
            resp = client.get("/runs/test_run/patch")
        assert resp.status_code == 200
        assert resp.json()["patch"] is None
