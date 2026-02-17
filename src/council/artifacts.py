"""Run folder creation, manifest writing, and artifact management."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from council.compat import redact_abs_paths  # cross-platform path redaction
from council.config import CouncilConfig, redact_env
from council.types import ContextSource, GatheredContext, RoundResult, RunOptions, ToolResult


def _make_slug(task: str) -> str:
    """Create a short filesystem-safe slug from task text."""
    # Take first 40 chars, lowercase, replace non-alnum with underscore.
    cleaned = re.sub(r"[^a-z0-9]+", "_", task[:40].lower()).strip("_")
    return cleaned or "task"


def create_run_dir(opts: RunOptions) -> Path:
    """Create and return a unique run directory for this invocation.

    Uses microsecond-precision timestamp plus a short random suffix
    to avoid collisions from rapid sequential invocations.
    """
    now = datetime.now(UTC)
    slug = _make_slug(opts.task)
    # Include microseconds and a 4-hex random suffix for uniqueness.
    rand_suffix = os.urandom(2).hex()
    dirname = f"{now.strftime('%Y-%m-%d_%H%M%S')}_{now.strftime('%f')}_{rand_suffix}_{slug}"
    run_dir = opts.outdir / dirname

    # Use exist_ok=False to detect collision; retry once if needed.
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        rand_suffix = os.urandom(4).hex()
        dirname = f"{now.strftime('%Y-%m-%d_%H%M%S')}_{now.strftime('%f')}_{rand_suffix}_{slug}"
        run_dir = opts.outdir / dirname
        run_dir.mkdir(parents=True, exist_ok=True)

    # Create subdirectories.
    if not opts.no_save:
        (run_dir / "rounds" / "0_generate").mkdir(parents=True, exist_ok=True)
        (run_dir / "rounds" / "1_claude_improve").mkdir(parents=True, exist_ok=True)
        (run_dir / "rounds" / "2_codex_critique").mkdir(parents=True, exist_ok=True)
        (run_dir / "rounds" / "3_claude_finalize").mkdir(parents=True, exist_ok=True)
    (run_dir / "final").mkdir(parents=True, exist_ok=True)

    return run_dir


def save_task(run_dir: Path, task: str) -> None:
    """Save the task description."""
    (run_dir / "task.md").write_text(task, encoding="utf-8")


def save_context(run_dir: Path, ctx: GatheredContext) -> None:
    """Save context text and source metadata."""
    (run_dir / "context.md").write_text(ctx.text, encoding="utf-8")

    sources_data = [_source_to_dict(s) for s in ctx.sources]
    (run_dir / "context_sources.json").write_text(json.dumps(sources_data, indent=2), encoding="utf-8")


def _source_to_dict(src: ContextSource) -> dict:
    """Convert a ContextSource to a JSON-serializable dict."""
    d = asdict(src)
    return {k: v for k, v in d.items() if v is not None}


def save_round0(run_dir: Path, prompts: dict[str, str], results: dict[str, ToolResult]) -> None:
    """Save Round 0 artifacts (parallel generation).

    Saves all tool results by their actual names (supports multi-candidate
    names like ``claude_0``, ``codex_1`` in addition to plain ``claude``).
    """
    rdir = run_dir / "rounds" / "0_generate"
    for name, prompt_text in prompts.items():
        (rdir / f"prompt_{name}.md").write_text(prompt_text, encoding="utf-8")
    for name, r in results.items():
        (rdir / f"{name}_stdout.md").write_text(r.stdout, encoding="utf-8")
        (rdir / f"{name}_stderr.txt").write_text(r.stderr, encoding="utf-8")


def save_round(
    run_dir: Path,
    round_name: str,
    prompt: str,
    result: ToolResult,
) -> None:
    """Save artifacts for Rounds 1, 2, or 3."""
    rdir = run_dir / "rounds" / round_name
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "prompt.md").write_text(prompt, encoding="utf-8")
    (rdir / "stdout.md").write_text(result.stdout, encoding="utf-8")
    (rdir / "stderr.txt").write_text(result.stderr, encoding="utf-8")


def save_final(run_dir: Path, final_md: str, patch: str | None, summary: str) -> None:
    """Save final outputs."""
    fdir = run_dir / "final"
    (fdir / "final.md").write_text(final_md, encoding="utf-8")
    if patch:
        (fdir / "final.patch").write_text(patch, encoding="utf-8")
    (fdir / "summary.md").write_text(summary, encoding="utf-8")


def write_manifest(
    run_dir: Path,
    opts: RunOptions,
    config: CouncilConfig,
    ctx: GatheredContext,
    rounds: list[RoundResult],
    start_time: datetime,
    end_time: datetime,
) -> None:
    """Write the manifest.json with full run metadata."""
    manifest = {
        "version": "1.0",
        "mode": opts.mode.value,
        "task_preview": opts.task[:200],
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "total_duration_sec": (end_time - start_time).total_seconds(),
        "context": {
            "mode": opts.context_mode.value,
            "diff_scope": opts.diff_scope.value,
            "total_size_bytes": ctx.total_size,
            "max_context_kb": opts.max_context_kb,
            "max_file_kb": opts.max_file_kb,
            "sources_count": len(ctx.sources),
            "files_included": [s.path for s in ctx.sources if s.source_type == "file" and not s.excluded and s.path],
            "files_truncated": [s.path for s in ctx.sources if s.source_type == "file" and s.truncated and s.path],
        },
        "tools": {},
        "rounds": [],
        "options": {
            "dry_run": opts.dry_run,
            "timeout_sec": opts.timeout_sec,
            "tools_requested": opts.tools,
        },
    }

    # Tool configs (with redacted env, command, and extra_args).
    for name, tcfg in config.tools.items():
        redacted_cmd = _redact_command(tcfg.command + tcfg.extra_args)
        manifest["tools"][name] = {
            "command": redacted_cmd[: len(tcfg.command)],
            "input_mode": tcfg.input_mode.value,
            "extra_args": redacted_cmd[len(tcfg.command) :],
            "env": redact_env(tcfg.env),
        }

    # Round details.
    for rnd in rounds:
        round_data: dict = {"name": rnd.round_name, "tools": {}}
        for tname, tr in rnd.results.items():
            round_data["tools"][tname] = {
                "exit_code": tr.exit_code,
                "duration_sec": round(tr.duration_sec, 2),
                "timed_out": tr.timed_out,
                "stdout_size": len(tr.stdout),
                "stderr_size": len(tr.stderr),
                "command": _redact_command(tr.command),
            }
        manifest["rounds"].append(round_data)

    # Apply path redaction to manifest if requested.
    if opts.redact_paths:
        manifest["task_preview"] = redact_abs_paths(manifest["task_preview"])
        ctx_section = manifest["context"]
        ctx_section["files_included"] = [redact_abs_paths(p) for p in ctx_section["files_included"]]
        ctx_section["files_truncated"] = [redact_abs_paths(p) for p in ctx_section["files_truncated"]]

    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")


_SENSITIVE_KEYWORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")

# Short flags known to carry secrets (e.g. curl -k, various CLIs using -t).
_SENSITIVE_SHORT_FLAGS = frozenset({"-k", "-t"})


def _is_sensitive_flag(arg: str) -> bool:
    """Check whether a CLI flag name looks like it carries a secret.

    Matches long flags containing KEY/TOKEN/SECRET/PASSWORD/CREDENTIAL,
    and an explicit allowlist of short flags (``-k``, ``-t``).
    """
    if arg in _SENSITIVE_SHORT_FLAGS:
        return True
    upper = arg.lstrip("-").upper()
    return any(kw in upper for kw in _SENSITIVE_KEYWORDS)


def _redact_command(cmd: list[str]) -> list[str]:
    """Redact potentially sensitive arguments from command lists.

    Handles:
    - ``--api-key sk-...``  (long flag containing KEY/TOKEN/SECRET/PASSWORD/CREDENTIAL)
    - ``--api-key=sk-...``  (flag=value in one arg)
    - ``-k sk-...``         (short flag from sensitive allowlist: -k, -t)
    """
    redacted: list[str] = []
    skip_next = False
    for arg in cmd:
        if skip_next:
            redacted.append("***REDACTED***")
            skip_next = False
            continue

        if arg.startswith("-") and "=" in arg:
            # --flag=value form.
            key, _, _ = arg.partition("=")
            if _is_sensitive_flag(key):
                redacted.append(f"{key}=***REDACTED***")
            else:
                redacted.append(arg)
        elif arg.startswith("-") and _is_sensitive_flag(arg):
            # --api-key  or  -k  (value follows as next arg).
            redacted.append(arg)
            skip_next = True
        else:
            redacted.append(arg)

    return redacted


def write_minimal_manifest(
    run_dir: Path,
    opts: RunOptions,
    start_time: datetime,
    end_time: datetime,
) -> None:
    """Write a slim manifest.json for --no-save mode.

    Contains only timing, mode, and tool names — no context details,
    file lists, or round-level data.
    """
    manifest: dict = {
        "version": "1.0",
        "mode": opts.mode.value,
        "task_preview": "(not saved)",
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "total_duration_sec": (end_time - start_time).total_seconds(),
        "no_save": True,
        "options": {
            "timeout_sec": opts.timeout_sec,
            "tools_requested": opts.tools,
        },
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")


def cleanup_intermediates(run_dir: Path) -> None:
    """Remove intermediate artifacts, keeping only final/ and manifest.json.

    Used by --no-save to strip everything except the end result.
    """
    for name in ("task.md", "context.md", "context_sources.json", "state.json"):
        path = run_dir / name
        if path.exists():
            path.unlink()

    rounds_dir = run_dir / "rounds"
    if rounds_dir.exists():
        shutil.rmtree(rounds_dir)
