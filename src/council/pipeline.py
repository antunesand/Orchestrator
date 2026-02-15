"""Four-round ping-pong pipeline orchestrator."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console

from council.artifacts import (
    create_run_dir,
    save_context,
    save_final,
    save_round,
    save_round0,
    save_task,
    write_manifest,
)
from council.config import CouncilConfig, find_repo_root
from council.context import gather_context
from council.diff_extract import extract_and_save
from council.prompts import round0_prompt, round1_prompt, round2_prompt, round3_prompt
from council.runner import run_tool, run_tools_parallel
from council.types import GatheredContext, Mode, RoundResult, RunOptions, ToolResult

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
    non_empty = [l for l in lines if l.strip()][:10]
    return "\n".join(non_empty)


async def run_pipeline(opts: RunOptions, config: CouncilConfig) -> Path:
    """Execute the full 4-round pipeline and return the run directory path."""
    start_time = datetime.now(timezone.utc)
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
    save_task(run_dir, opts.task)
    save_context(run_dir, ctx)
    _print_verbose(f"Run directory: {run_dir}", verbose)

    rounds: list[RoundResult] = []
    tool_names = opts.tools

    has_claude = "claude" in tool_names and "claude" in config.tools
    has_codex = "codex" in tool_names and "codex" in config.tools

    if not has_claude and not has_codex:
        _print_progress("[bold red]ERROR:[/bold red] No tools configured. Need at least 'claude' or 'codex'.")
        _finalize_manifest(run_dir, opts, config, ctx, rounds, start_time)
        return run_dir

    _print_verbose(
        f"Tools: {', '.join(t for t in tool_names if t in config.tools)}",
        verbose,
    )

    # --- Build Round 0 prompts ---
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
        save_round0(run_dir, r0_prompts, {})
        _finalize_manifest(run_dir, opts, config, ctx, rounds, start_time)
        _print_progress(f"Run directory: {run_dir}")
        return run_dir

    # --- Round 0: Parallel generation ---
    _print_progress("Round 0: Generating responses in parallel...")
    for name in r0_prompts:
        _print_verbose(f"Calling {name}: {' '.join(config.tools[name].command)}", verbose)

    r0_configs = {n: config.tools[n] for n in r0_prompts if n in config.tools}
    r0_results = await run_tools_parallel(
        r0_configs, r0_prompts, timeout_sec=opts.timeout_sec, cwd=repo_root
    )
    save_round0(run_dir, r0_prompts, r0_results)

    r0_round = RoundResult(round_name="0_generate", results=r0_results)
    rounds.append(r0_round)

    # Track which tools succeeded at runtime.
    codex_available = False
    for name, result in r0_results.items():
        _print_progress(f"  {name}: {_tool_status_str(result)} ({result.duration_sec:.1f}s)")
        _print_verbose(f"stdout: {len(result.stdout)} bytes, stderr: {len(result.stderr)} bytes", verbose)

    claude_r0 = r0_results.get("claude")
    codex_r0 = r0_results.get("codex")

    # Determine what we can do based on available results.
    claude_r0_out = claude_r0.stdout if claude_r0 and claude_r0.exit_code == 0 else ""
    codex_r0_out = codex_r0.stdout if codex_r0 and codex_r0.exit_code == 0 else ""
    codex_available = bool(codex_r0_out)

    if not claude_r0_out and not codex_r0_out:
        _print_progress("[bold red]ERROR:[/bold red] Both tools failed in Round 0. Cannot continue.")
        _finalize_manifest(run_dir, opts, config, ctx, rounds, start_time)
        return run_dir

    # If only one tool succeeded, use its output as the final result.
    if not claude_r0_out and codex_r0_out:
        _print_progress("Claude failed; using Codex output as final result.")
        _save_single_tool_final(run_dir, codex_r0_out)
        _finalize_manifest(run_dir, opts, config, ctx, rounds, start_time)
        return run_dir

    if claude_r0_out and not codex_r0_out:
        _print_progress("Codex failed; using Claude output directly for finalization.")
        codex_r0_out = "(Codex was unavailable; no alternative analysis provided.)"

    # --- Round 1: Claude improves with Codex's input ---
    _print_progress("Round 1: Claude improving with alternative input...")
    r1_prompt = round1_prompt(opts.mode, opts.task, ctx.text, codex_r0_out, claude_r0_out)
    _print_verbose(f"Prompt size: {len(r1_prompt) / 1024:.1f} KB", verbose)

    if opts.print_prompts:
        print(f"\n{'='*60}\nRound 1 prompt:\n{'='*60}\n{r1_prompt}\n", file=sys.stderr)

    r1_result = await run_tool(
        "claude", config.tools["claude"], r1_prompt,
        timeout_sec=opts.timeout_sec, cwd=repo_root,
    )
    save_round(run_dir, "1_claude_improve", r1_prompt, r1_result)
    rounds.append(RoundResult(round_name="1_claude_improve", results={"claude": r1_result}))

    _print_progress(f"  claude: {_tool_status_str(r1_result)} ({r1_result.duration_sec:.1f}s)")

    claude_improved = r1_result.stdout if r1_result.exit_code == 0 else claude_r0_out

    # --- Round 2: Codex critiques (only if codex succeeded in Round 0) ---
    if has_codex and codex_available and "codex" in config.tools:
        _print_progress("Round 2: Codex critiquing improved solution...")
        r2_prompt = round2_prompt(opts.mode, opts.task, ctx.text, claude_improved)
        _print_verbose(f"Prompt size: {len(r2_prompt) / 1024:.1f} KB", verbose)

        if opts.print_prompts:
            print(f"\n{'='*60}\nRound 2 prompt:\n{'='*60}\n{r2_prompt}\n", file=sys.stderr)

        r2_result = await run_tool(
            "codex", config.tools["codex"], r2_prompt,
            timeout_sec=opts.timeout_sec, cwd=repo_root,
        )
        save_round(run_dir, "2_codex_critique", r2_prompt, r2_result)
        rounds.append(RoundResult(round_name="2_codex_critique", results={"codex": r2_result}))

        _print_progress(f"  codex: {_tool_status_str(r2_result)} ({r2_result.duration_sec:.1f}s)")

        codex_critique = r2_result.stdout if r2_result.exit_code == 0 else "(Codex critique unavailable.)"
    else:
        if has_codex and not codex_available:
            _print_progress("Round 2: Skipped (Codex failed in Round 0)")
        codex_critique = "(Codex was not available for critique.)"

    # --- Round 3: Claude finalizes ---
    _print_progress("Round 3: Claude finalizing...")
    r3_prompt = round3_prompt(opts.mode, opts.task, ctx.text, claude_improved, codex_critique)
    _print_verbose(f"Prompt size: {len(r3_prompt) / 1024:.1f} KB", verbose)

    if opts.print_prompts:
        print(f"\n{'='*60}\nRound 3 prompt:\n{'='*60}\n{r3_prompt}\n", file=sys.stderr)

    r3_result = await run_tool(
        "claude", config.tools["claude"], r3_prompt,
        timeout_sec=opts.timeout_sec, cwd=repo_root,
    )
    save_round(run_dir, "3_claude_finalize", r3_prompt, r3_result)
    rounds.append(RoundResult(round_name="3_claude_finalize", results={"claude": r3_result}))

    _print_progress(f"  claude: {_tool_status_str(r3_result)} ({r3_result.duration_sec:.1f}s)")

    # --- Finalize ---
    final_output = r3_result.stdout if r3_result.exit_code == 0 else claude_improved
    summary = _make_summary(final_output)

    # Extract and save diffs.
    patch_path = str(run_dir / "final" / "final.patch")
    patch = extract_and_save(final_output, patch_path)

    save_final(run_dir, final_output, patch, summary)
    _finalize_manifest(run_dir, opts, config, ctx, rounds, start_time)

    _print_progress(f"Done! Results in: {run_dir}")
    _print_progress(f"  Final output: {run_dir / 'final' / 'final.md'}")
    if patch:
        _print_progress(f"  Patch file:   {run_dir / 'final' / 'final.patch'}")

    return run_dir


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
    end_time = datetime.now(timezone.utc)
    write_manifest(run_dir, opts, config, ctx, rounds, start_time, end_time)
