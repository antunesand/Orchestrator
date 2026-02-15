"""Async subprocess runner with tool adapter support."""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
import time
from pathlib import Path

from council.config import ToolConfig
from council.types import InputMode, ToolResult


async def run_tool(
    tool_name: str,
    config: ToolConfig,
    prompt: str,
    timeout_sec: int = 180,
    cwd: Path | None = None,
) -> ToolResult:
    """Run a tool asynchronously, returning its result.

    Supports two input modes:
    - stdin: pipe the prompt text to the tool's stdin.
    - file: write the prompt to a temp file and pass it via prompt_file_arg.
    """
    start = time.monotonic()

    cmd = list(config.command) + list(config.extra_args)

    # Build environment: inherit current env + tool-specific overrides.
    env = {**os.environ, **config.env}

    stdin_data: bytes | None = None
    tmp_path: Path | None = None

    if config.input_mode == InputMode.FILE:
        # Write prompt to a temporary file.
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(prompt)
            tmp_path = Path(tmp.name)
        if config.prompt_file_arg:
            cmd.extend([config.prompt_file_arg, str(tmp_path)])
        else:
            cmd.append(str(tmp_path))
    else:
        # stdin mode.
        stdin_data = prompt.encode("utf-8")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(cwd) if cwd else None,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=stdin_data),
                timeout=timeout_sec,
            )
            exit_code = proc.returncode
            timed_out = False
        except TimeoutError:
            proc.kill()
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=5
                )
            except (TimeoutError, ProcessLookupError):
                stdout_bytes = b""
                stderr_bytes = b""
            exit_code = None
            timed_out = True

    except FileNotFoundError:
        elapsed = time.monotonic() - start
        return ToolResult(
            tool_name=tool_name,
            command=cmd,
            exit_code=-1,
            stdout="",
            stderr=f"Command not found: {cmd[0]}",
            duration_sec=elapsed,
            timed_out=False,
        )
    except OSError as exc:
        elapsed = time.monotonic() - start
        return ToolResult(
            tool_name=tool_name,
            command=cmd,
            exit_code=-1,
            stdout="",
            stderr=f"OS error running {cmd[0]}: {exc}",
            duration_sec=elapsed,
            timed_out=False,
        )
    finally:
        # Clean up temp file.
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)

    elapsed = time.monotonic() - start

    stdout_text = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
    stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

    return ToolResult(
        tool_name=tool_name,
        command=cmd,
        exit_code=exit_code,
        stdout=stdout_text,
        stderr=stderr_text,
        duration_sec=elapsed,
        timed_out=timed_out,
    )


async def run_tools_parallel(
    configs: dict[str, ToolConfig],
    prompts: dict[str, str],
    timeout_sec: int = 180,
    cwd: Path | None = None,
) -> dict[str, ToolResult]:
    """Run multiple tools in parallel, returning all results."""
    tasks = {
        name: run_tool(name, cfg, prompts[name], timeout_sec=timeout_sec, cwd=cwd)
        for name, cfg in configs.items()
        if name in prompts
    }

    results: dict[str, ToolResult] = {}
    gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)

    for name, result in zip(tasks.keys(), gathered, strict=False):
        if isinstance(result, Exception):
            results[name] = ToolResult(
                tool_name=name,
                command=list(configs[name].command),
                exit_code=-1,
                stdout="",
                stderr=f"Exception: {result}",
                duration_sec=0.0,
            )
        else:
            results[name] = result

    return results
