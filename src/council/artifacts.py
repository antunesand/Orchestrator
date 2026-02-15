"""Run folder creation, manifest writing, and artifact management."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from council.config import CouncilConfig, redact_env
from council.types import ContextSource, GatheredContext, RoundResult, RunOptions, ToolResult


def _make_slug(task: str) -> str:
    """Create a short filesystem-safe slug from task text."""
    # Take first 40 chars, lowercase, replace non-alnum with underscore.
    cleaned = re.sub(r"[^a-z0-9]+", "_", task[:40].lower()).strip("_")
    return cleaned or "task"


def create_run_dir(opts: RunOptions) -> Path:
    """Create and return the run directory for this invocation."""
    now = datetime.now(timezone.utc)
    slug = _make_slug(opts.task)
    dirname = f"{now.strftime('%Y-%m-%d_%H%M%S')}_{slug}"
    run_dir = opts.outdir / dirname
    run_dir.mkdir(parents=True, exist_ok=True)

    # Create subdirectories.
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
    (run_dir / "context_sources.json").write_text(
        json.dumps(sources_data, indent=2), encoding="utf-8"
    )


def _source_to_dict(src: ContextSource) -> dict:
    """Convert a ContextSource to a JSON-serializable dict."""
    d = asdict(src)
    return {k: v for k, v in d.items() if v is not None}


def save_round0(run_dir: Path, prompts: dict[str, str], results: dict[str, ToolResult]) -> None:
    """Save Round 0 artifacts (parallel generation)."""
    rdir = run_dir / "rounds" / "0_generate"
    for name in ("claude", "codex"):
        if name in prompts:
            (rdir / f"prompt_{name}.md").write_text(prompts[name], encoding="utf-8")
        if name in results:
            r = results[name]
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
            "files_included": [
                s.path for s in ctx.sources
                if s.source_type == "file" and not s.excluded and s.path
            ],
            "files_truncated": [
                s.path for s in ctx.sources
                if s.source_type == "file" and s.truncated and s.path
            ],
        },
        "tools": {},
        "rounds": [],
        "options": {
            "dry_run": opts.dry_run,
            "timeout_sec": opts.timeout_sec,
            "tools_requested": opts.tools,
        },
    }

    # Tool configs (with redacted env).
    for name, tcfg in config.tools.items():
        manifest["tools"][name] = {
            "command": tcfg.command,
            "input_mode": tcfg.input_mode.value,
            "extra_args": tcfg.extra_args,
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

    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str), encoding="utf-8"
    )


def _redact_command(cmd: list[str]) -> list[str]:
    """Redact potentially sensitive arguments from command lists."""
    redacted = []
    skip_next = False
    for arg in cmd:
        if skip_next:
            redacted.append("***")
            skip_next = False
            continue
        upper = arg.upper()
        if any(kw in upper for kw in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            redacted.append("***")
        elif arg.startswith("--") and "=" in arg:
            key, _, _ = arg.partition("=")
            ku = key.upper()
            if any(kw in ku for kw in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
                redacted.append(f"{key}=***")
            else:
                redacted.append(arg)
        else:
            redacted.append(arg)
    return redacted
