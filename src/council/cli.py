"""CLI entry point using Typer."""

from __future__ import annotations

import asyncio
import importlib.resources
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from council import __version__
from council.apply import (
    apply_patch,
    check_patch,
    create_branch,
    load_patch,
    post_apply_diff,
    show_diff_preview,
    working_tree_clean,
)
from council.artifacts import _redact_command
from council.config import find_repo_root, load_config
from council.pipeline import resume_pipeline, run_pipeline
from council.types import ContextMode, DiffScope, Mode, RunOptions

# Path to the bundled example config.
_EXAMPLE_CONFIG = Path(__file__).parent.parent.parent / ".council.yml.example"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"council {__version__}")
        raise typer.Exit()


app = typer.Typer(
    name="council",
    help="Multi-LLM council: ping-pong workflow between Claude Code CLI and Codex CLI.",
    no_args_is_help=True,
)


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", help="Show version and exit", callback=_version_callback, is_eager=True),
    ] = None,
) -> None:
    """Multi-LLM council: ping-pong workflow."""


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
    no_save: bool = False,
    redact_paths: bool = False,
    smart_context: bool = False,
    structured_review: bool = False,
    claude_n: int = 1,
    codex_n: int = 1,
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
        no_save=no_save,
        redact_paths=redact_paths,
        smart_context=smart_context,
        structured_review=structured_review,
        claude_n=claude_n,
        codex_n=codex_n,
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
        raise typer.Exit(130) from None


@app.command()
def fix(
    task: Annotated[str, typer.Argument(help="Bug/error description or traceback")] = "",
    task_file: Annotated[Path | None, typer.Option("--task-file", help="Read task from file")] = None,
    context: Annotated[ContextMode, typer.Option("--context", help="Context gathering mode")] = ContextMode.AUTO,
    diff: Annotated[DiffScope, typer.Option("--diff", help="Which diffs to include")] = DiffScope.ALL,
    include: Annotated[list[str] | None, typer.Option("--include", help="Include file content")] = None,
    include_glob: Annotated[list[str] | None, typer.Option("--include-glob", help="Include files matching glob")] = None,
    include_from_diff: Annotated[bool, typer.Option("--include-from-diff", help="Include changed file contents")] = False,
    max_context_kb: Annotated[int, typer.Option("--max-context-kb", help="Max total context size in KB")] = 300,
    max_file_kb: Annotated[int, typer.Option("--max-file-kb", help="Max single file size in KB")] = 60,
    timeout_sec: Annotated[int, typer.Option("--timeout-sec", help="Timeout per tool call in seconds")] = 180,
    outdir: Annotated[Path, typer.Option("--outdir", help="Output directory for runs")] = Path("runs"),
    tools: Annotated[str, typer.Option("--tools", help="Comma-separated tool names")] = "claude,codex",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Write prompts only, don't call tools")] = False,
    print_prompts: Annotated[bool, typer.Option("--print-prompts", help="Print prompts to terminal")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="Verbose output")] = False,
    no_save: Annotated[bool, typer.Option("--no-save", help="Only save final output and minimal manifest")] = False,
    redact_paths: Annotated[bool, typer.Option("--redact-paths", help="Redact absolute paths in saved artifacts")] = False,
    smart_context: Annotated[bool, typer.Option("--smart-context/--no-smart-context", help="Auto-include files referenced in tracebacks/logs")] = True,
    structured_review: Annotated[bool, typer.Option("--structured-review/--no-structured-review", help="Request JSON-structured critique output")] = False,
    claude_n: Annotated[int, typer.Option("--claude-n", help="Number of Claude candidates to generate in Round 0", min=1, max=5)] = 1,
    codex_n: Annotated[int, typer.Option("--codex-n", help="Number of Codex candidates to generate in Round 0", min=1, max=5)] = 1,
    config: Annotated[Path | None, typer.Option("--config", help="Path to config file")] = None,
) -> None:
    """Fix a bug or error using the multi-LLM council."""
    opts = _common_options(
        task=task, mode=Mode.FIX, task_file=task_file, context=context,
        diff=diff, include=include, include_glob=include_glob,
        include_from_diff=include_from_diff, max_context_kb=max_context_kb,
        max_file_kb=max_file_kb, timeout_sec=timeout_sec, outdir=outdir,
        tools=tools, dry_run=dry_run, print_prompts=print_prompts,
        verbose=verbose, no_save=no_save, redact_paths=redact_paths,
        smart_context=smart_context, structured_review=structured_review,
        claude_n=claude_n, codex_n=codex_n, config=config,
    )
    _run(opts)


@app.command()
def feature(
    task: Annotated[str, typer.Argument(help="Feature description")] = "",
    task_file: Annotated[Path | None, typer.Option("--task-file", help="Read task from file")] = None,
    context: Annotated[ContextMode, typer.Option("--context", help="Context gathering mode")] = ContextMode.AUTO,
    diff: Annotated[DiffScope, typer.Option("--diff", help="Which diffs to include")] = DiffScope.ALL,
    include: Annotated[list[str] | None, typer.Option("--include", help="Include file content")] = None,
    include_glob: Annotated[list[str] | None, typer.Option("--include-glob", help="Include files matching glob")] = None,
    include_from_diff: Annotated[bool, typer.Option("--include-from-diff", help="Include changed file contents")] = False,
    max_context_kb: Annotated[int, typer.Option("--max-context-kb", help="Max total context size in KB")] = 300,
    max_file_kb: Annotated[int, typer.Option("--max-file-kb", help="Max single file size in KB")] = 60,
    timeout_sec: Annotated[int, typer.Option("--timeout-sec", help="Timeout per tool call in seconds")] = 180,
    outdir: Annotated[Path, typer.Option("--outdir", help="Output directory for runs")] = Path("runs"),
    tools: Annotated[str, typer.Option("--tools", help="Comma-separated tool names")] = "claude,codex",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Write prompts only, don't call tools")] = False,
    print_prompts: Annotated[bool, typer.Option("--print-prompts", help="Print prompts to terminal")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="Verbose output")] = False,
    no_save: Annotated[bool, typer.Option("--no-save", help="Only save final output and minimal manifest")] = False,
    redact_paths: Annotated[bool, typer.Option("--redact-paths", help="Redact absolute paths in saved artifacts")] = False,
    smart_context: Annotated[bool, typer.Option("--smart-context/--no-smart-context", help="Auto-include files referenced in tracebacks/logs")] = False,
    structured_review: Annotated[bool, typer.Option("--structured-review/--no-structured-review", help="Request JSON-structured critique output")] = False,
    claude_n: Annotated[int, typer.Option("--claude-n", help="Number of Claude candidates to generate in Round 0", min=1, max=5)] = 1,
    codex_n: Annotated[int, typer.Option("--codex-n", help="Number of Codex candidates to generate in Round 0", min=1, max=5)] = 1,
    config: Annotated[Path | None, typer.Option("--config", help="Path to config file")] = None,
) -> None:
    """Implement a new feature using the multi-LLM council."""
    opts = _common_options(
        task=task, mode=Mode.FEATURE, task_file=task_file, context=context,
        diff=diff, include=include, include_glob=include_glob,
        include_from_diff=include_from_diff, max_context_kb=max_context_kb,
        max_file_kb=max_file_kb, timeout_sec=timeout_sec, outdir=outdir,
        tools=tools, dry_run=dry_run, print_prompts=print_prompts,
        verbose=verbose, no_save=no_save, redact_paths=redact_paths,
        smart_context=smart_context, structured_review=structured_review,
        claude_n=claude_n, codex_n=codex_n, config=config,
    )
    _run(opts)


@app.command()
def review(
    task: Annotated[str, typer.Argument(help="Review instructions or focus areas")] = "",
    task_file: Annotated[Path | None, typer.Option("--task-file", help="Read task from file")] = None,
    context: Annotated[ContextMode, typer.Option("--context", help="Context gathering mode")] = ContextMode.AUTO,
    diff: Annotated[DiffScope, typer.Option("--diff", help="Which diffs to include")] = DiffScope.STAGED,
    include: Annotated[list[str] | None, typer.Option("--include", help="Include file content")] = None,
    include_glob: Annotated[list[str] | None, typer.Option("--include-glob", help="Include files matching glob")] = None,
    include_from_diff: Annotated[bool, typer.Option("--include-from-diff", help="Include changed file contents")] = False,
    max_context_kb: Annotated[int, typer.Option("--max-context-kb", help="Max total context size in KB")] = 300,
    max_file_kb: Annotated[int, typer.Option("--max-file-kb", help="Max single file size in KB")] = 60,
    timeout_sec: Annotated[int, typer.Option("--timeout-sec", help="Timeout per tool call in seconds")] = 180,
    outdir: Annotated[Path, typer.Option("--outdir", help="Output directory for runs")] = Path("runs"),
    tools: Annotated[str, typer.Option("--tools", help="Comma-separated tool names")] = "claude,codex",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Write prompts only, don't call tools")] = False,
    print_prompts: Annotated[bool, typer.Option("--print-prompts", help="Print prompts to terminal")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="Verbose output")] = False,
    no_save: Annotated[bool, typer.Option("--no-save", help="Only save final output and minimal manifest")] = False,
    redact_paths: Annotated[bool, typer.Option("--redact-paths", help="Redact absolute paths in saved artifacts")] = False,
    smart_context: Annotated[bool, typer.Option("--smart-context/--no-smart-context", help="Auto-include files referenced in tracebacks/logs")] = False,
    structured_review: Annotated[bool, typer.Option("--structured-review/--no-structured-review", help="Request JSON-structured critique output")] = True,
    claude_n: Annotated[int, typer.Option("--claude-n", help="Number of Claude candidates to generate in Round 0", min=1, max=5)] = 1,
    codex_n: Annotated[int, typer.Option("--codex-n", help="Number of Codex candidates to generate in Round 0", min=1, max=5)] = 1,
    config: Annotated[Path | None, typer.Option("--config", help="Path to config file")] = None,
) -> None:
    """Review code changes using the multi-LLM council."""
    opts = _common_options(
        task=task, mode=Mode.REVIEW, task_file=task_file, context=context,
        diff=diff, include=include, include_glob=include_glob,
        include_from_diff=include_from_diff, max_context_kb=max_context_kb,
        max_file_kb=max_file_kb, timeout_sec=timeout_sec, outdir=outdir,
        tools=tools, dry_run=dry_run, print_prompts=print_prompts,
        verbose=verbose, no_save=no_save, redact_paths=redact_paths,
        smart_context=smart_context, structured_review=structured_review,
        claude_n=claude_n, codex_n=codex_n, config=config,
    )
    _run(opts)


@app.command()
def resume(
    run_dir: Annotated[Path, typer.Argument(help="Path to a previous run directory to resume")],
    retry_failed: Annotated[bool, typer.Option("--retry-failed", help="Only re-run failed rounds, skip succeeded ones")] = False,
    timeout_sec: Annotated[int, typer.Option("--timeout-sec", help="Timeout per tool call in seconds")] = 180,
    verbose: Annotated[bool, typer.Option("--verbose", help="Verbose output")] = False,
    config: Annotated[Path | None, typer.Option("--config", help="Path to config file")] = None,
) -> None:
    """Resume an interrupted or failed council run.

    Point this at a previous run directory (e.g. runs/2026-02-16_123456_…/)
    to pick up where it left off. The original task and context are reloaded
    from the saved artifacts.

    Use --retry-failed to re-execute only the rounds that failed while
    preserving the output of rounds that already succeeded.
    """
    run_path = Path(run_dir)
    if not run_path.is_dir():
        typer.echo(f"Error: run directory not found: {run_path}", err=True)
        raise typer.Exit(1)

    state_file = run_path / "state.json"
    if not state_file.exists():
        typer.echo(
            f"Error: no state.json in {run_path}. "
            "Only runs created with council >= 1.1 can be resumed.",
            err=True,
        )
        raise typer.Exit(1)

    repo_root = find_repo_root()
    cfg = load_config(cli_path=config, repo_root=repo_root)

    try:
        asyncio.run(
            resume_pipeline(
                run_dir=run_path,
                config=cfg,
                retry_failed=retry_failed,
                timeout_sec=timeout_sec,
                verbose=verbose,
            )
        )
    except KeyboardInterrupt:
        typer.echo("\nInterrupted.", err=True)
        raise typer.Exit(130) from None


@app.command(name="apply")
def apply_cmd(
    run_dir: Annotated[Path, typer.Argument(help="Path to a council run directory containing final.patch")],
    apply_to: Annotated[str | None, typer.Option("--apply-to", help="Create a new branch, apply the patch there (no commit)")] = None,
    check: Annotated[bool, typer.Option("--check", help="Dry-run: verify the patch applies cleanly without modifying files")] = False,
    diff: Annotated[bool, typer.Option("--diff", help="Show a syntax-highlighted preview of the patch before applying")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation prompt")] = False,
) -> None:
    """Apply a patch from a previous council run to the current repository.

    By default this is interactive: it shows the patch, asks for
    confirmation, then applies.  Use --yes to skip the prompt.

    Use --apply-to <branch> to create a new branch first, apply the
    patch there, and leave it uncommitted for review.

    Use --check to verify the patch applies without modifying any files.
    """
    run_path = Path(run_dir)
    if not run_path.is_dir():
        typer.echo(f"Error: run directory not found: {run_path}", err=True)
        raise typer.Exit(1)

    # Load the patch.
    patch = load_patch(run_path)
    if patch is None:
        typer.echo(
            f"Error: no final.patch found in {run_path / 'final'}.\n"
            "The council run may not have produced a diff.",
            err=True,
        )
        raise typer.Exit(1)

    # Determine repo root.
    repo_root = find_repo_root()
    if repo_root is None:
        typer.echo("Error: not inside a git repository.", err=True)
        raise typer.Exit(1)

    # Show diff preview if requested, or always in interactive mode.
    if diff or (not yes and not check):
        typer.echo("")
        show_diff_preview(patch)
        typer.echo("")

    # Dry-run check.
    if check:
        ok, detail = check_patch(patch, repo_root)
        if ok:
            typer.echo(f"Patch check: OK — {detail}")
        else:
            typer.echo(f"Patch check: FAILED\n{detail}", err=True)
            raise typer.Exit(1)
        return

    # Verify the patch can be applied before prompting.
    ok, detail = check_patch(patch, repo_root)
    if not ok:
        typer.echo(f"Patch cannot be applied cleanly:\n{detail}", err=True)
        typer.echo("\nThe working tree may have diverged from the state when the council run was created.", err=True)
        raise typer.Exit(1)

    # Interactive confirmation (unless --yes).
    if not yes:
        if apply_to:
            action = f"Create branch '{apply_to}' and apply patch"
        else:
            action = "Apply patch to working tree"
        confirmed = typer.confirm(f"{action}?")
        if not confirmed:
            typer.echo("Aborted.")
            raise typer.Exit(0)

    # Create branch if requested.
    if apply_to:
        ok, detail = create_branch(apply_to, repo_root)
        if not ok:
            typer.echo(f"Failed to create branch: {detail}", err=True)
            raise typer.Exit(1)
        typer.echo(f"Branch: {detail}")

    # Apply the patch.
    ok, detail = apply_patch(patch, repo_root)
    if not ok:
        typer.echo(f"Apply failed: {detail}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Applied: {detail}")

    # Show post-apply diff summary.
    post_diff = post_apply_diff(repo_root)
    if post_diff:
        lines = post_diff.splitlines()
        files_changed = [l for l in lines if l.startswith("diff --git")]
        typer.echo(f"\n{len(files_changed)} file(s) modified in working tree.")
        typer.echo("Review changes with: git diff")
        if not apply_to:
            typer.echo("Commit when ready:   git add -p && git commit")
    else:
        typer.echo("No visible changes after apply (patch may have been empty).")


def _ensure_gitignore_entries(directory: Path) -> list[str]:
    """Append .council.yml / council.yml to .gitignore if missing.

    Returns a list of entries that were added.
    """
    gitignore = directory / ".gitignore"
    entries_to_add = [".council.yml", "council.yml"]
    added: list[str] = []

    existing_lines: set[str] = set()
    if gitignore.exists():
        existing_lines = {line.strip() for line in gitignore.read_text(encoding="utf-8").splitlines()}

    missing = [e for e in entries_to_add if e not in existing_lines]
    if missing:
        with open(gitignore, "a", encoding="utf-8") as f:
            # Add a blank line separator if file doesn't end with newline.
            if gitignore.exists() and gitignore.stat().st_size > 0:
                content = gitignore.read_bytes()
                if not content.endswith(b"\n"):
                    f.write("\n")
            f.write("\n# Council config (may contain tokens/paths)\n")
            for entry in missing:
                f.write(f"{entry}\n")
                added.append(entry)

    return added


def _get_example_config_text() -> str:
    """Read the bundled .council.yml.example content."""
    if _EXAMPLE_CONFIG.is_file():
        return _EXAMPLE_CONFIG.read_text(encoding="utf-8")
    # Fallback: try importlib.resources (for installed packages).
    try:
        ref = importlib.resources.files("council").parent.parent / ".council.yml.example"
        return ref.read_text(encoding="utf-8")
    except Exception:
        # Hardcoded minimal fallback — keep in sync with CouncilConfig.defaults().
        return (
            "# Council CLI configuration\n"
            "# See README.md for full documentation.\n"
            "tools:\n"
            "  claude:\n"
            '    command: ["claude"]\n'
            '    input_mode: "stdin"\n'
            "    extra_args:\n"
            '      - "-p"\n'
            '      - "Use the piped input as the full task instructions.'
            ' Produce the best possible answer."\n'
            "    env: {}\n"
            "  codex:\n"
            '    command: ["codex", "exec"]\n'
            '    input_mode: "stdin"\n'
            '    extra_args: ["--ask-for-approval", "never",'
            ' "--sandbox", "read-only",'
            ' "--color", "never",'
            ' "-"]\n'
            "    env: {}\n"
        )


@app.command()
def init(
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing .council.yml")] = False,
) -> None:
    """Create a .council.yml config file and update .gitignore."""
    repo_root = find_repo_root()
    target_dir = repo_root if repo_root else Path.cwd()
    target_file = target_dir / ".council.yml"

    if target_file.exists() and not force:
        typer.echo(f"Config already exists: {target_file}")
        typer.echo("Use --force to overwrite.")
        raise typer.Exit(1)

    config_text = _get_example_config_text()
    target_file.write_text(config_text, encoding="utf-8")
    typer.echo(f"Created {target_file}")

    # Update .gitignore.
    added = _ensure_gitignore_entries(target_dir)
    if added:
        typer.echo(f"Added to .gitignore: {', '.join(added)}")

    typer.echo("")
    typer.echo("Next steps:")
    typer.echo("  1. Edit .council.yml to customize tool settings")
    typer.echo("  2. Run `council doctor` to verify your setup")
    typer.echo('  3. Try a dry run: council fix --dry-run --print-prompts "test task"')


@app.command()
def doctor(
    config: Annotated[Path | None, typer.Option("--config", help="Path to config file")] = None,
) -> None:
    """Check tool availability and configuration."""
    repo_root = find_repo_root()
    cfg = load_config(cli_path=config, repo_root=repo_root)

    typer.echo(f"council {__version__}\n")

    # Config source.
    if config and config.is_file():
        typer.echo(f"Config:     {config}")
    else:
        config_locations: list[Path] = []
        if repo_root:
            config_locations.extend([
                repo_root / ".council.yml",
                repo_root / "council.yml",
            ])
        config_locations.append(Path.home() / ".council.yml")

        config_used = None
        for loc in config_locations:
            if loc.is_file():
                config_used = loc
                break
        if config_used:
            typer.echo(f"Config:     {config_used}")
        else:
            typer.echo("Config:     (built-in defaults)")

    # Repo root.
    if repo_root:
        typer.echo(f"Repo root:  {repo_root}")
    else:
        typer.echo("Repo root:  (not in a git repo)")

    typer.echo("")
    typer.echo("Tools:")

    all_ok = True
    for name, tcfg in cfg.tools.items():
        cmd_name = tcfg.command[0] if tcfg.command else "(empty)"
        full_cmd = list(tcfg.command)

        # 1. Check if base command is on PATH.
        found = shutil.which(cmd_name)
        if found is None and Path(cmd_name).is_absolute():
            found = cmd_name if Path(cmd_name).exists() else None

        if not found:
            typer.echo(f"  {name:12s} {cmd_name:20s} NOT FOUND")
            all_ok = False
            continue

        # 2. Probe version/help for the base command.
        version_str = _probe_tool_version(cmd_name)
        status = f"OK ({version_str})" if version_str else "OK (found)"
        typer.echo(f"  {name:12s} {' '.join(full_cmd):20s} {status}")

        if tcfg.extra_args:
            typer.echo(f"{'':14s} extra_args: {_redact_command(tcfg.extra_args)}")

        # 3. If command has a subcommand (e.g. "codex exec"), validate it.
        if len(full_cmd) > 1:
            sub_ok = _check_subcommand(full_cmd)
            sub_label = " ".join(full_cmd)
            if sub_ok:
                typer.echo(f"{'':14s} subcommand '{sub_label}': OK")
            else:
                typer.echo(f"{'':14s} subcommand '{sub_label}': FAILED (try `{sub_label} --help`)")
                all_ok = False

        # 4. Codex-specific: check login status if tool looks like codex.
        if cmd_name in ("codex",) or (len(full_cmd) > 1 and full_cmd[0] == "codex"):
            auth_ok = _check_codex_auth()
            if auth_ok is True:
                typer.echo(f"{'':14s} codex auth: logged in")
            elif auth_ok is False:
                typer.echo(f"{'':14s} codex auth: NOT logged in (run `codex login`)")
                all_ok = False
            else:
                typer.echo(f"{'':14s} codex auth: unknown (could not run `codex login status`)")

    # 5. Suggestions.
    typer.echo("")
    if all_ok:
        typer.echo("All checks passed.")
    else:
        typer.echo("Some checks failed. See suggestions above.")
        typer.echo("Tips:")
        typer.echo('  - If claude doesn\'t work in -p mode, test: echo test | claude -p "explain"')
        typer.echo("  - If codex exec isn't available, update Codex CLI")
        typer.echo("  - If codex auth fails, run: codex login")
        raise typer.Exit(1)


def _probe_tool_version(cmd: str) -> str | None:
    """Try to get a version string from a tool (--version, then --help).

    Returns version text or None. Never calls the tool with a prompt.
    """
    for flag in ("--version", "--help"):
        try:
            result = subprocess.run(
                [cmd, flag],
                capture_output=True,
                text=True,
                timeout=5,
            )
            output = (result.stdout or result.stderr or "").strip()
            if output and result.returncode == 0:
                # Return first line, truncated.
                first_line = output.splitlines()[0][:60]
                return first_line
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    return None


def _check_subcommand(full_cmd: list[str]) -> bool:
    """Check if a subcommand exists by running it with --help.

    E.g. ``["codex", "exec", "--help"]``. Returns True if exit code is 0.
    """
    try:
        result = subprocess.run(
            [*full_cmd, "--help"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _check_codex_auth() -> bool | None:
    """Check Codex login status via ``codex login status``.

    Returns True if logged in (exit 0), False if not (non-zero), None on error.
    """
    try:
        result = subprocess.run(
            ["codex", "login", "status"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def app_main() -> None:
    """Entry point for the console script."""
    app()
