"""CLI entry point using Typer."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer

from council.config import CouncilConfig, find_repo_root, load_config
from council.pipeline import run_pipeline
from council.types import ContextMode, DiffScope, Mode, RunOptions

app = typer.Typer(
    name="council",
    help="Multi-LLM council: ping-pong workflow between Claude Code CLI and Codex CLI.",
    no_args_is_help=True,
)


def _common_options(
    task: str,
    mode: Mode,
    task_file: Path | None = None,
    context: ContextMode = ContextMode.AUTO,
    diff: DiffScope = DiffScope.ALL,
    include: list[str] | None = None,
    include_glob: list[str] | None = None,
    include_from_diff: bool = False,
    max_context_kb: int = 300,
    max_file_kb: int = 60,
    timeout_sec: int = 180,
    outdir: Path = Path("runs"),
    tools: str = "claude,codex",
    dry_run: bool = False,
    print_prompts: bool = False,
    verbose: bool = False,
    config: Path | None = None,
) -> RunOptions:
    """Build RunOptions from CLI arguments."""
    # Resolve task from file if provided.
    actual_task = task
    if task_file is not None:
        if not task_file.is_file():
            typer.echo(f"Error: task file not found: {task_file}", err=True)
            raise typer.Exit(1)
        actual_task = task_file.read_text(encoding="utf-8")
    elif not task.strip():
        typer.echo("Error: task description is required (positional arg or --task-file)", err=True)
        raise typer.Exit(1)

    tool_list = [t.strip() for t in tools.split(",") if t.strip()]

    return RunOptions(
        mode=mode,
        task=actual_task,
        task_file=task_file,
        context_mode=context,
        diff_scope=diff,
        include_paths=include or [],
        include_globs=include_glob or [],
        include_from_diff=include_from_diff,
        max_context_kb=max_context_kb,
        max_file_kb=max_file_kb,
        timeout_sec=timeout_sec,
        outdir=outdir,
        tools=tool_list,
        dry_run=dry_run,
        print_prompts=print_prompts,
        verbose=verbose,
        config_path=config,
    )


def _run(opts: RunOptions) -> None:
    """Load config and run the pipeline."""
    repo_root = find_repo_root()
    cfg = load_config(cli_path=opts.config_path, repo_root=repo_root)

    # Validate requested tools exist in config.
    for t in opts.tools:
        if t not in cfg.tools:
            typer.echo(
                f"Warning: tool '{t}' not found in config. "
                f"Available: {', '.join(cfg.tools.keys())}",
                err=True,
            )

    try:
        asyncio.run(run_pipeline(opts, cfg))
    except KeyboardInterrupt:
        typer.echo("\nInterrupted.", err=True)
        raise typer.Exit(130)


@app.command()
def fix(
    task: Annotated[str, typer.Argument(help="Bug/error description or traceback")] = "",
    task_file: Annotated[Optional[Path], typer.Option("--task-file", help="Read task from file")] = None,
    context: Annotated[ContextMode, typer.Option("--context", help="Context gathering mode")] = ContextMode.AUTO,
    diff: Annotated[DiffScope, typer.Option("--diff", help="Which diffs to include")] = DiffScope.ALL,
    include: Annotated[Optional[list[str]], typer.Option("--include", help="Include file content")] = None,
    include_glob: Annotated[Optional[list[str]], typer.Option("--include-glob", help="Include files matching glob")] = None,
    include_from_diff: Annotated[bool, typer.Option("--include-from-diff", help="Include changed file contents")] = False,
    max_context_kb: Annotated[int, typer.Option("--max-context-kb", help="Max total context size in KB")] = 300,
    max_file_kb: Annotated[int, typer.Option("--max-file-kb", help="Max single file size in KB")] = 60,
    timeout_sec: Annotated[int, typer.Option("--timeout-sec", help="Timeout per tool call in seconds")] = 180,
    outdir: Annotated[Path, typer.Option("--outdir", help="Output directory for runs")] = Path("runs"),
    tools: Annotated[str, typer.Option("--tools", help="Comma-separated tool names")] = "claude,codex",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Write prompts only, don't call tools")] = False,
    print_prompts: Annotated[bool, typer.Option("--print-prompts", help="Print prompts to terminal")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="Verbose output")] = False,
    config: Annotated[Optional[Path], typer.Option("--config", help="Path to config file")] = None,
) -> None:
    """Fix a bug or error using the multi-LLM council."""
    opts = _common_options(
        task=task, mode=Mode.FIX, task_file=task_file, context=context,
        diff=diff, include=include, include_glob=include_glob,
        include_from_diff=include_from_diff, max_context_kb=max_context_kb,
        max_file_kb=max_file_kb, timeout_sec=timeout_sec, outdir=outdir,
        tools=tools, dry_run=dry_run, print_prompts=print_prompts,
        verbose=verbose, config=config,
    )
    _run(opts)


@app.command()
def feature(
    task: Annotated[str, typer.Argument(help="Feature description")] = "",
    task_file: Annotated[Optional[Path], typer.Option("--task-file", help="Read task from file")] = None,
    context: Annotated[ContextMode, typer.Option("--context", help="Context gathering mode")] = ContextMode.AUTO,
    diff: Annotated[DiffScope, typer.Option("--diff", help="Which diffs to include")] = DiffScope.ALL,
    include: Annotated[Optional[list[str]], typer.Option("--include", help="Include file content")] = None,
    include_glob: Annotated[Optional[list[str]], typer.Option("--include-glob", help="Include files matching glob")] = None,
    include_from_diff: Annotated[bool, typer.Option("--include-from-diff", help="Include changed file contents")] = False,
    max_context_kb: Annotated[int, typer.Option("--max-context-kb", help="Max total context size in KB")] = 300,
    max_file_kb: Annotated[int, typer.Option("--max-file-kb", help="Max single file size in KB")] = 60,
    timeout_sec: Annotated[int, typer.Option("--timeout-sec", help="Timeout per tool call in seconds")] = 180,
    outdir: Annotated[Path, typer.Option("--outdir", help="Output directory for runs")] = Path("runs"),
    tools: Annotated[str, typer.Option("--tools", help="Comma-separated tool names")] = "claude,codex",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Write prompts only, don't call tools")] = False,
    print_prompts: Annotated[bool, typer.Option("--print-prompts", help="Print prompts to terminal")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="Verbose output")] = False,
    config: Annotated[Optional[Path], typer.Option("--config", help="Path to config file")] = None,
) -> None:
    """Implement a new feature using the multi-LLM council."""
    opts = _common_options(
        task=task, mode=Mode.FEATURE, task_file=task_file, context=context,
        diff=diff, include=include, include_glob=include_glob,
        include_from_diff=include_from_diff, max_context_kb=max_context_kb,
        max_file_kb=max_file_kb, timeout_sec=timeout_sec, outdir=outdir,
        tools=tools, dry_run=dry_run, print_prompts=print_prompts,
        verbose=verbose, config=config,
    )
    _run(opts)


@app.command()
def review(
    task: Annotated[str, typer.Argument(help="Review instructions or focus areas")] = "",
    task_file: Annotated[Optional[Path], typer.Option("--task-file", help="Read task from file")] = None,
    context: Annotated[ContextMode, typer.Option("--context", help="Context gathering mode")] = ContextMode.AUTO,
    diff: Annotated[DiffScope, typer.Option("--diff", help="Which diffs to include")] = DiffScope.STAGED,
    include: Annotated[Optional[list[str]], typer.Option("--include", help="Include file content")] = None,
    include_glob: Annotated[Optional[list[str]], typer.Option("--include-glob", help="Include files matching glob")] = None,
    include_from_diff: Annotated[bool, typer.Option("--include-from-diff", help="Include changed file contents")] = False,
    max_context_kb: Annotated[int, typer.Option("--max-context-kb", help="Max total context size in KB")] = 300,
    max_file_kb: Annotated[int, typer.Option("--max-file-kb", help="Max single file size in KB")] = 60,
    timeout_sec: Annotated[int, typer.Option("--timeout-sec", help="Timeout per tool call in seconds")] = 180,
    outdir: Annotated[Path, typer.Option("--outdir", help="Output directory for runs")] = Path("runs"),
    tools: Annotated[str, typer.Option("--tools", help="Comma-separated tool names")] = "claude,codex",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Write prompts only, don't call tools")] = False,
    print_prompts: Annotated[bool, typer.Option("--print-prompts", help="Print prompts to terminal")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="Verbose output")] = False,
    config: Annotated[Optional[Path], typer.Option("--config", help="Path to config file")] = None,
) -> None:
    """Review code changes using the multi-LLM council."""
    opts = _common_options(
        task=task, mode=Mode.REVIEW, task_file=task_file, context=context,
        diff=diff, include=include, include_glob=include_glob,
        include_from_diff=include_from_diff, max_context_kb=max_context_kb,
        max_file_kb=max_file_kb, timeout_sec=timeout_sec, outdir=outdir,
        tools=tools, dry_run=dry_run, print_prompts=print_prompts,
        verbose=verbose, config=config,
    )
    _run(opts)


def app_main() -> None:
    """Entry point for the console script."""
    app()
