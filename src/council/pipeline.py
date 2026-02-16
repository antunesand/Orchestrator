"""Four-round ping-pong pipeline orchestrator."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console

from council.artifacts import (
    cleanup_intermediates,
    create_run_dir,
    save_context,
    save_final,
    save_round,
    save_round0,
    save_task,
    write_manifest,
    write_minimal_manifest,
)
from council.compat import redact_abs_paths
from council.config import CouncilConfig, find_repo_root
from council.context import gather_context
from council.diff_extract import extract_and_save
from council.prompts import round0_prompt, round1_prompt, round2_prompt, round3_prompt
from council.runner import run_tool, run_tools_parallel
from council.state import (
    ROUND_NAMES,
    get_failed_rounds,
    get_resume_point,
    init_state,
    load_state,
    mark_finished,
    update_round,
)
from council.types import GatheredContext, RoundResult, RoundStatus, RunOptions, ToolResult

# Shared stderr console for progress output.
_console = Console(stderr=True, highlight=False)


def _print_progress(msg: str) -> None:
    """Print a short progress line to stderr."""
    _console.print(f"  [bold cyan]>[/bold cyan] {msg}")


def _print_verbose(msg: str, verbose: bool) -> None:
    """Print additional detail when verbose mode is on."""
    if verbose:
        _console.print(f"    [dim]{msg}[/dim]")


def _tool_status_str(result: ToolResult) -> str:
    """Build a human-readable status string for a tool result."""
    if result.timed_out:
        return "[bold red]TIMED OUT[/bold red]"
    if result.exit_code == 0:
        return "[green]OK[/green]"
    return f"[bold red]FAILED (exit={result.exit_code})[/bold red]"


def _make_summary(final_output: str) -> str:
    """Extract a short summary from the final output."""
    lines = final_output.strip().splitlines()
    # Look for a "Final Decision Summary" section.
    summary_lines: list[str] = []
    capture = False
    for line in lines:
        if "final decision summary" in line.lower():
            capture = True
            continue
        if capture:
            if line.startswith("###") or line.startswith("## "):
                break
            summary_lines.append(line)

    if summary_lines:
        return "\n".join(summary_lines).strip()

    # Fallback: first 10 non-empty lines.
    non_empty = [line for line in lines if line.strip()][:10]
    return "\n".join(non_empty)


def _tool_ok(result: ToolResult | None) -> bool:
    """Check if a tool result represents success."""
    return result is not None and result.exit_code == 0 and bool(result.stdout)


def _round_tool_statuses(results: dict[str, ToolResult]) -> dict[str, str]:
    """Build a tool-name -> status mapping for checkpoint state."""
    statuses: dict[str, str] = {}
    for name, r in results.items():
        if r.timed_out:
            statuses[name] = "timed_out"
        elif r.exit_code == 0:
            statuses[name] = "ok"
        else:
            statuses[name] = "failed"
    return statuses


def _load_round_output(run_dir: Path, round_name: str, tool_name: str) -> str:
    """Load previously saved stdout for a tool in a round."""
    rdir = run_dir / "rounds" / round_name
    if round_name == "0_generate":
        path = rdir / f"{tool_name}_stdout.md"
    else:
        path = rdir / "stdout.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


async def run_pipeline(opts: RunOptions, config: CouncilConfig) -> Path:
    """Execute the full 4-round pipeline and return the run directory path."""
    start_time = datetime.now(UTC)
    repo_root = find_repo_root()
    verbose = opts.verbose

    # --- Gather context ---
    _print_progress("Gathering context...")
    ctx = gather_context(opts, repo_root)
    _print_verbose(
        f"Context: {ctx.total_size / 1024:.1f} KB, "
        f"{len(ctx.sources)} sources, "
        f"{len(ctx.changed_files)} changed files",
        verbose,
    )

    # --- Create run directory ---
    run_dir = create_run_dir(opts)
    no_save = opts.no_save
    if not no_save:
        save_task(run_dir, opts.task)
        save_context(run_dir, ctx)
    _print_verbose(f"Run directory: {run_dir}", verbose)

    # Initialize checkpoint state (skipped in no-save mode).
    state: dict = {}
    if not no_save:
        state = init_state(run_dir, opts.mode.value, opts.task, opts.tools)

    rounds: list[RoundResult] = []
    tool_names = opts.tools

    has_claude = "claude" in tool_names and "claude" in config.tools
    has_codex = "codex" in tool_names and "codex" in config.tools

    if not has_claude and not has_codex:
        _print_progress("[bold red]ERROR:[/bold red] No tools configured. Need at least 'claude' or 'codex'.")
        if not no_save:
            mark_finished(run_dir, state, status="failed")
        _finalize_manifest(run_dir, opts, config, ctx, rounds, start_time)
        return run_dir

    _print_verbose(
        f"Tools: {', '.join(t for t in tool_names if t in config.tools)}",
        verbose,
    )

    result = await _run_rounds(
        run_dir=run_dir,
        opts=opts,
        config=config,
        ctx=ctx,
        state=state,
        rounds=rounds,
        has_claude=has_claude,
        has_codex=has_codex,
        repo_root=repo_root,
    )

    if result is None:
        # Pipeline aborted (both tools failed in R0).
        if not no_save:
            mark_finished(run_dir, state, status="failed")
        _finalize_manifest(run_dir, opts, config, ctx, rounds, start_time)
        return run_dir

    # --- Finalize ---
    final_output = result
    if opts.redact_paths:
        final_output = redact_abs_paths(final_output)

    summary = _make_summary(final_output)
    patch_path = str(run_dir / "final" / "final.patch")
    patch = extract_and_save(final_output, patch_path)
    save_final(run_dir, final_output, patch, summary)

    if not no_save:
        mark_finished(run_dir, state, status="completed")
    _finalize_manifest(run_dir, opts, config, ctx, rounds, start_time)

    if no_save:
        cleanup_intermediates(run_dir)

    _print_progress(f"Done! Results in: {run_dir}")
    _print_progress(f"  Final output: {run_dir / 'final' / 'final.md'}")
    if patch:
        _print_progress(f"  Patch file:   {run_dir / 'final' / 'final.patch'}")

    return run_dir


async def resume_pipeline(
    run_dir: Path,
    config: CouncilConfig,
    retry_failed: bool = False,
    timeout_sec: int | None = None,
    verbose: bool = False,
) -> Path:
    """Resume an interrupted or failed pipeline run.

    Reads state.json and saved artifacts from *run_dir*, determines which
    round to restart from, and continues execution from that point.

    When *retry_failed* is True, only rounds with ``failed`` status are
    re-executed; rounds with ``ok`` status are always preserved.
    """
    start_time = datetime.now(UTC)
    repo_root = find_repo_root()

    state = load_state(run_dir)
    mode_str = state["mode"]
    tools_list = state.get("tools", ["claude", "codex"])

    # Reload task and context from disk.
    task_path = run_dir / "task.md"
    context_path = run_dir / "context.md"
    if not task_path.exists():
        raise FileNotFoundError(f"Missing task.md in {run_dir}")
    task = task_path.read_text(encoding="utf-8")
    context_text = context_path.read_text(encoding="utf-8") if context_path.exists() else ""

    # Build a minimal RunOptions for the resumed run.
    from council.types import Mode
    mode = Mode(mode_str)
    opts = RunOptions(
        mode=mode,
        task=task,
        outdir=run_dir.parent,
        tools=tools_list,
        timeout_sec=timeout_sec if timeout_sec is not None else 180,
        verbose=verbose,
    )

    # Build a GatheredContext stub from saved data.
    ctx = GatheredContext(text=context_text, total_size=len(context_text.encode("utf-8")))

    has_claude = "claude" in tools_list and "claude" in config.tools
    has_codex = "codex" in tools_list and "codex" in config.tools

    if not has_claude and not has_codex:
        _print_progress("[bold red]ERROR:[/bold red] No tools configured for resume.")
        return run_dir

    # Determine where to resume.
    resume_from = get_resume_point(state)
    if resume_from is None:
        _print_progress("All rounds already completed. Nothing to resume.")
        return run_dir

    failed_rounds = set(get_failed_rounds(state)) if retry_failed else set()

    _print_progress(f"Resuming run from: {resume_from}")
    if retry_failed and failed_rounds:
        _print_progress(f"  Retrying failed rounds: {', '.join(sorted(failed_rounds))}")
    _print_verbose(f"Run directory: {run_dir}", verbose)

    # Update state to mark it running again.
    state["status"] = "running"
    state["finished_at"] = None

    rounds: list[RoundResult] = []

    result = await _run_rounds(
        run_dir=run_dir,
        opts=opts,
        config=config,
        ctx=ctx,
        state=state,
        rounds=rounds,
        has_claude=has_claude,
        has_codex=has_codex,
        repo_root=repo_root,
        resume_from=resume_from,
        retry_failed=failed_rounds,
    )

    if result is None:
        mark_finished(run_dir, state, status="failed")
        _finalize_manifest(run_dir, opts, config, ctx, rounds, start_time)
        return run_dir

    final_output = result
    summary = _make_summary(final_output)
    patch_path = str(run_dir / "final" / "final.patch")
    patch = extract_and_save(final_output, patch_path)
    save_final(run_dir, final_output, patch, summary)

    mark_finished(run_dir, state, status="completed")
    _finalize_manifest(run_dir, opts, config, ctx, rounds, start_time)

    _print_progress(f"Done! Results in: {run_dir}")
    _print_progress(f"  Final output: {run_dir / 'final' / 'final.md'}")
    if patch:
        _print_progress(f"  Patch file:   {run_dir / 'final' / 'final.patch'}")

    return run_dir


async def _run_rounds(
    *,
    run_dir: Path,
    opts: RunOptions,
    config: CouncilConfig,
    ctx: GatheredContext,
    state: dict,
    rounds: list[RoundResult],
    has_claude: bool,
    has_codex: bool,
    repo_root: Path | None,
    resume_from: str | None = None,
    retry_failed: set[str] | None = None,
) -> str | None:
    """Execute pipeline rounds, optionally skipping already-completed ones.

    Returns the final output text, or None if the pipeline must abort.
    """
    verbose = opts.verbose
    no_save = opts.no_save
    retry_failed = retry_failed or set()

    # Helper to decide whether a round should actually execute.
    def _should_run(round_name: str) -> bool:
        if resume_from is None:
            return True  # Fresh run, execute everything.
        round_idx = ROUND_NAMES.index(round_name) if round_name in ROUND_NAMES else -1
        resume_idx = ROUND_NAMES.index(resume_from) if resume_from in ROUND_NAMES else 0
        if round_idx < resume_idx:
            # Before the resume point — skip unless it's a retry target.
            return round_name in retry_failed
        return True

    # ---- Round 0: Parallel generation ----
    r0_prompts: dict[str, str] = {}
    if has_claude:
        r0_prompts["claude"] = round0_prompt(opts.mode, opts.task, ctx.text)
    if has_codex:
        r0_prompts["codex"] = round0_prompt(opts.mode, opts.task, ctx.text)

    if opts.print_prompts:
        for name, prompt in r0_prompts.items():
            print(f"\n{'='*60}\nRound 0 prompt for {name}:\n{'='*60}\n{prompt}\n", file=sys.stderr)

    if opts.dry_run:
        _print_progress("DRY RUN: writing prompts and context, then exiting.")
        if not no_save:
            save_round0(run_dir, r0_prompts, {})
        return None

    if _should_run("0_generate"):
        _print_progress("Round 0: Generating responses in parallel...")
        for name in r0_prompts:
            _print_verbose(f"Calling {name}: {' '.join(config.tools[name].command)}", verbose)

        if not no_save:
            update_round(run_dir, state, "0_generate", RoundStatus.RUNNING)

        r0_configs = {n: config.tools[n] for n in r0_prompts if n in config.tools}
        r0_results = await run_tools_parallel(
            r0_configs, r0_prompts, timeout_sec=opts.timeout_sec, cwd=repo_root
        )
        if not no_save:
            save_round0(run_dir, r0_prompts, r0_results)

        r0_round = RoundResult(round_name="0_generate", results=r0_results)
        rounds.append(r0_round)

        for name, result in r0_results.items():
            _print_progress(f"  {name}: {_tool_status_str(result)} ({result.duration_sec:.1f}s)")
            _print_verbose(f"stdout: {len(result.stdout)} bytes, stderr: {len(result.stderr)} bytes", verbose)

        # Determine status.
        claude_r0 = r0_results.get("claude")
        codex_r0 = r0_results.get("codex")
        claude_r0_out = claude_r0.stdout if _tool_ok(claude_r0) else ""
        codex_r0_out = codex_r0.stdout if _tool_ok(codex_r0) else ""

        if not claude_r0_out and not codex_r0_out:
            _print_progress("[bold red]ERROR:[/bold red] Both tools failed in Round 0. Cannot continue.")
            if not no_save:
                update_round(run_dir, state, "0_generate", RoundStatus.FAILED, _round_tool_statuses(r0_results))
            return None

        if not no_save:
            update_round(run_dir, state, "0_generate", RoundStatus.OK, _round_tool_statuses(r0_results))
    else:
        _print_progress("Round 0: Reusing previous results (skipped)")
        claude_r0_out = _load_round_output(run_dir, "0_generate", "claude")
        codex_r0_out = _load_round_output(run_dir, "0_generate", "codex")

    # Track codex availability.
    codex_available = bool(codex_r0_out) and codex_r0_out != "(Codex was unavailable; no alternative analysis provided.)"

    # If only one tool succeeded, handle single-tool fallback.
    if not claude_r0_out and codex_r0_out:
        _print_progress("Claude failed; using Codex output as final result.")
        _save_single_tool_final(run_dir, codex_r0_out)
        return codex_r0_out

    if claude_r0_out and not codex_r0_out:
        _print_progress("Codex failed; using Claude output directly for finalization.")
        codex_r0_out = "(Codex was unavailable; no alternative analysis provided.)"

    # ---- Round 1: Claude improves ----
    if _should_run("1_claude_improve"):
        _print_progress("Round 1: Claude improving with alternative input...")
        r1_prompt = round1_prompt(opts.mode, opts.task, ctx.text, codex_r0_out, claude_r0_out)
        _print_verbose(f"Prompt size: {len(r1_prompt) / 1024:.1f} KB", verbose)

        if opts.print_prompts:
            print(f"\n{'='*60}\nRound 1 prompt:\n{'='*60}\n{r1_prompt}\n", file=sys.stderr)

        if not no_save:
            update_round(run_dir, state, "1_claude_improve", RoundStatus.RUNNING)

        r1_result = await run_tool(
            "claude", config.tools["claude"], r1_prompt,
            timeout_sec=opts.timeout_sec, cwd=repo_root,
        )
        if not no_save:
            save_round(run_dir, "1_claude_improve", r1_prompt, r1_result)
        rounds.append(RoundResult(round_name="1_claude_improve", results={"claude": r1_result}))

        _print_progress(f"  claude: {_tool_status_str(r1_result)} ({r1_result.duration_sec:.1f}s)")

        if _tool_ok(r1_result):
            claude_improved = r1_result.stdout
            if not no_save:
                update_round(run_dir, state, "1_claude_improve", RoundStatus.OK, {"claude": "ok"})
        else:
            claude_improved = claude_r0_out
            if not no_save:
                update_round(run_dir, state, "1_claude_improve", RoundStatus.FAILED, {"claude": "failed"})
    else:
        _print_progress("Round 1: Reusing previous results (skipped)")
        claude_improved = _load_round_output(run_dir, "1_claude_improve", "claude") or claude_r0_out

    # ---- Round 2: Codex critiques ----
    if has_codex and codex_available and "codex" in config.tools:
        if _should_run("2_codex_critique"):
            _print_progress("Round 2: Codex critiquing improved solution...")
            r2_prompt = round2_prompt(opts.mode, opts.task, ctx.text, claude_improved)
            _print_verbose(f"Prompt size: {len(r2_prompt) / 1024:.1f} KB", verbose)

            if opts.print_prompts:
                print(f"\n{'='*60}\nRound 2 prompt:\n{'='*60}\n{r2_prompt}\n", file=sys.stderr)

            if not no_save:
                update_round(run_dir, state, "2_codex_critique", RoundStatus.RUNNING)

            r2_result = await run_tool(
                "codex", config.tools["codex"], r2_prompt,
                timeout_sec=opts.timeout_sec, cwd=repo_root,
            )
            if not no_save:
                save_round(run_dir, "2_codex_critique", r2_prompt, r2_result)
            rounds.append(RoundResult(round_name="2_codex_critique", results={"codex": r2_result}))

            _print_progress(f"  codex: {_tool_status_str(r2_result)} ({r2_result.duration_sec:.1f}s)")

            if _tool_ok(r2_result):
                codex_critique = r2_result.stdout
                if not no_save:
                    update_round(run_dir, state, "2_codex_critique", RoundStatus.OK, {"codex": "ok"})
            else:
                codex_critique = "(Codex critique unavailable.)"
                if not no_save:
                    update_round(run_dir, state, "2_codex_critique", RoundStatus.FAILED, {"codex": "failed"})
        else:
            _print_progress("Round 2: Reusing previous results (skipped)")
            codex_critique = _load_round_output(run_dir, "2_codex_critique", "codex") or "(Codex critique unavailable.)"
    else:
        if has_codex and not codex_available:
            _print_progress("Round 2: Skipped (Codex failed in Round 0)")
        codex_critique = "(Codex was not available for critique.)"
        if not no_save:
            update_round(run_dir, state, "2_codex_critique", RoundStatus.SKIPPED)

    # ---- Round 3: Claude finalizes ----
    if _should_run("3_claude_finalize"):
        _print_progress("Round 3: Claude finalizing...")
        r3_prompt = round3_prompt(opts.mode, opts.task, ctx.text, claude_improved, codex_critique)
        _print_verbose(f"Prompt size: {len(r3_prompt) / 1024:.1f} KB", verbose)

        if opts.print_prompts:
            print(f"\n{'='*60}\nRound 3 prompt:\n{'='*60}\n{r3_prompt}\n", file=sys.stderr)

        if not no_save:
            update_round(run_dir, state, "3_claude_finalize", RoundStatus.RUNNING)

        r3_result = await run_tool(
            "claude", config.tools["claude"], r3_prompt,
            timeout_sec=opts.timeout_sec, cwd=repo_root,
        )
        if not no_save:
            save_round(run_dir, "3_claude_finalize", r3_prompt, r3_result)
        rounds.append(RoundResult(round_name="3_claude_finalize", results={"claude": r3_result}))

        _print_progress(f"  claude: {_tool_status_str(r3_result)} ({r3_result.duration_sec:.1f}s)")

        if _tool_ok(r3_result):
            final_output = r3_result.stdout
            if not no_save:
                update_round(run_dir, state, "3_claude_finalize", RoundStatus.OK, {"claude": "ok"})
        else:
            final_output = claude_improved
            if not no_save:
                update_round(run_dir, state, "3_claude_finalize", RoundStatus.FAILED, {"claude": "failed"})
    else:
        _print_progress("Round 3: Reusing previous results (skipped)")
        final_output = _load_round_output(run_dir, "3_claude_finalize", "claude") or claude_improved

    return final_output


def _save_single_tool_final(run_dir: Path, output: str) -> None:
    """Save a single-tool output as the final result (fallback)."""
    summary = _make_summary(output)
    patch_path = str(run_dir / "final" / "final.patch")
    patch = extract_and_save(output, patch_path)
    save_final(run_dir, output, patch, summary)


def _finalize_manifest(
    run_dir: Path,
    opts: RunOptions,
    config: CouncilConfig,
    ctx: GatheredContext,
    rounds: list[RoundResult],
    start_time: datetime,
) -> None:
    """Write the manifest with timing info."""
    end_time = datetime.now(UTC)
    if opts.no_save:
        write_minimal_manifest(run_dir, opts, start_time, end_time)
    else:
        write_manifest(run_dir, opts, config, ctx, rounds, start_time, end_time)
