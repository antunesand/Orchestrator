"""Streamlit frontend for Council Web UI.

Launch with:  council ui streamlit
              or: streamlit run src/council/ui/streamlit_app.py

Talks to the Council FastAPI backend at http://127.0.0.1:8717 by default.
"""

from __future__ import annotations

import requests
import streamlit as st

API_BASE = "http://127.0.0.1:8717"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _api_ok() -> bool:
    """Check whether the API server is reachable."""
    try:
        r = requests.get(f"{API_BASE}/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def _get_runs(limit: int = 50) -> list[dict]:
    try:
        r = requests.get(f"{API_BASE}/runs", params={"limit": limit}, timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


def _get_run_final(run_id: str) -> str | None:
    try:
        r = requests.get(f"{API_BASE}/runs/{run_id}/final", timeout=10)
        r.raise_for_status()
        return r.json().get("final")
    except Exception:
        return None


def _get_run_patch(run_id: str) -> str | None:
    try:
        r = requests.get(f"{API_BASE}/runs/{run_id}/patch", timeout=10)
        r.raise_for_status()
        return r.json().get("patch")
    except Exception:
        return None


def _submit_job(payload: dict) -> dict | None:
    try:
        r = requests.post(f"{API_BASE}/jobs", json=payload, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        st.error(f"Failed to submit job: {exc}")
        return None


def _get_job(job_id: str) -> dict | None:
    try:
        r = requests.get(f"{API_BASE}/jobs/{job_id}", timeout=5)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _get_job_logs(job_id: str) -> str:
    try:
        r = requests.get(f"{API_BASE}/jobs/{job_id}/logs", timeout=5)
        r.raise_for_status()
        return r.json().get("logs", "")
    except Exception:
        return ""


def _run_doctor() -> str:
    """Run ``council doctor`` locally and return output."""
    import shutil
    import subprocess
    import sys

    council_bin = shutil.which("council")
    cmd = [council_bin, "doctor"] if council_bin else [sys.executable, "-m", "council", "doctor"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return result.stdout + result.stderr
    except Exception as exc:
        return f"Error running council doctor: {exc}"


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Council UI", page_icon="", layout="wide")
st.title("Council CLI — Web UI")

# ---------------------------------------------------------------------------
# API connectivity check
# ---------------------------------------------------------------------------

if not _api_ok():
    st.error(
        "**Council API is not running.**\n\n"
        "Start it first:\n"
        "```\ncouncil ui api\n```\n"
        "Then reload this page."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Sidebar – run history
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Run History")
    if st.button("Refresh", key="refresh_runs"):
        pass  # triggers re-render
    runs = _get_runs()
    selected_run: str | None = None
    if runs:
        for run in runs:
            label = f"{run.get('mode', '?')} — {run.get('status', '?')}"
            name = run.get("name", "")
            preview = run.get("task_preview", "")[:60]
            if st.button(f"{name[:35]}\n{label}", key=f"run_{name}"):
                selected_run = name
    else:
        st.info("No runs found.")

# ---------------------------------------------------------------------------
# Main area – run viewer (when a sidebar run is selected)
# ---------------------------------------------------------------------------

if selected_run:
    st.subheader(f"Run: {selected_run}")
    col1, col2 = st.columns(2)
    with col1:
        final = _get_run_final(selected_run)
        if final:
            st.markdown("### Final Output")
            st.markdown(final)
        else:
            st.info("No final output yet.")
    with col2:
        patch = _get_run_patch(selected_run)
        if patch:
            st.markdown("### Patch")
            st.code(patch, language="diff")
            st.download_button("Download patch", data=patch, file_name="final.patch", mime="text/plain")
        else:
            st.info("No patch available.")

# ---------------------------------------------------------------------------
# New job form
# ---------------------------------------------------------------------------

st.markdown("---")
st.header("New Job")

with st.form("job_form"):
    mode = st.selectbox("Mode", ["fix", "feature", "review"])
    task = st.text_area("Task description", height=120, placeholder="Describe the bug, feature, or review focus...")

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        diff = st.selectbox("Diff scope", ["all", "staged", "unstaged", "none"], index=0)
        context = st.selectbox("Context", ["auto", "none"], index=0)
    with col_b:
        include_from_diff = st.checkbox("Include files from diff")
        smart_context = st.checkbox("Smart context", value=True)
        structured_review = st.checkbox("Structured review")
    with col_c:
        no_save = st.checkbox("No save (minimal artifacts)")
        redact_paths = st.checkbox("Redact paths")

    col_d, col_e = st.columns(2)
    with col_d:
        claude_n = st.number_input("Claude candidates (N)", min_value=1, max_value=5, value=1)
    with col_e:
        codex_n = st.number_input("Codex candidates (N)", min_value=1, max_value=5, value=1)

    outdir = st.text_input("Output directory", value="runs")

    submitted = st.form_submit_button("Run")

if submitted:
    if not task.strip():
        st.warning("Please enter a task description.")
    else:
        payload = {
            "mode": mode,
            "task": task,
            "diff": diff,
            "context": context,
            "include_from_diff": include_from_diff,
            "smart_context": smart_context,
            "structured_review": structured_review,
            "no_save": no_save,
            "redact_paths": redact_paths,
            "claude_n": claude_n,
            "codex_n": codex_n,
            "outdir": outdir,
        }
        result = _submit_job(payload)
        if result:
            st.session_state["active_job_id"] = result["job_id"]
            st.success(f"Job submitted: {result['job_id']}")

# ---------------------------------------------------------------------------
# Live job monitor
# ---------------------------------------------------------------------------

active_job_id = st.session_state.get("active_job_id")
if active_job_id:
    st.markdown("---")
    st.header("Job Monitor")

    job_info = _get_job(active_job_id)
    if job_info:
        status = job_info.get("status", "unknown")
        st.write(f"**Job:** {active_job_id} | **Status:** {status} | **Mode:** {job_info.get('mode', '?')}")

        if job_info.get("exit_code") is not None:
            st.write(f"**Exit code:** {job_info['exit_code']}")

        logs = _get_job_logs(active_job_id)
        st.text_area("Logs", value=logs, height=300, key="job_logs", disabled=True)

        if status == "running":
            st.info("Job is still running. Refresh to see latest logs.")
            if st.button("Refresh logs"):
                pass  # triggers re-render

        if status == "exited" and job_info.get("run_dir"):
            run_name = job_info["run_dir"].rstrip("/").split("/")[-1]
            st.success(f"Job completed. Run directory: {job_info['run_dir']}")

            final = _get_run_final(run_name)
            if final:
                st.markdown("### Final Output")
                st.markdown(final)

            patch = _get_run_patch(run_name)
            if patch:
                st.markdown("### Patch")
                st.code(patch, language="diff")
                st.download_button(
                    "Download patch",
                    data=patch,
                    file_name="final.patch",
                    mime="text/plain",
                    key="dl_patch_monitor",
                )
    else:
        st.warning(f"Job {active_job_id} not found.")

# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------

st.markdown("---")
if st.button("Run `council doctor`"):
    with st.spinner("Running doctor..."):
        output = _run_doctor()
    st.code(output)
