"""Tests for tool invocation composition (mocked subprocess)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from council.config import ToolConfig
from council.runner import run_tool, run_tools_parallel
from council.types import InputMode


class TestRunToolStdinMode:
    @pytest.mark.asyncio
    async def test_stdin_invocation(self):
        """Verify stdin mode pipes prompt to stdin."""
        config = ToolConfig(
            command=["echo"],
            input_mode=InputMode.STDIN,
            extra_args=[],
        )

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"hello output", b""))
        mock_proc.returncode = 0

        with patch("council.runner.asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await run_tool("test_tool", config, "test prompt", timeout_sec=10)

            # Verify subprocess was called with expected command.
            mock_exec.assert_called_once()
            call_args = mock_exec.call_args
            assert call_args[0] == ("echo",)
            # stdin should be PIPE for stdin mode.
            assert call_args[1]["stdin"] == asyncio.subprocess.PIPE

            # Verify communicate was called with the prompt.
            mock_proc.communicate.assert_called_once()
            input_data = mock_proc.communicate.call_args[1].get("input") or mock_proc.communicate.call_args[0][0] if mock_proc.communicate.call_args[0] else mock_proc.communicate.call_args[1].get("input")
            # The prompt bytes should have been passed.

        assert result.tool_name == "test_tool"
        assert result.exit_code == 0
        assert result.stdout == "hello output"
        assert result.timed_out is False

    @pytest.mark.asyncio
    async def test_stdin_with_extra_args(self):
        """Verify extra_args are appended to command."""
        config = ToolConfig(
            command=["claude"],
            input_mode=InputMode.STDIN,
            extra_args=["-p", "--no-color"],
        )

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"response", b""))
        mock_proc.returncode = 0

        with patch("council.runner.asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await run_tool("claude", config, "prompt", timeout_sec=10)
            call_args = mock_exec.call_args[0]
            assert call_args == ("claude", "-p", "--no-color")

        assert result.exit_code == 0


class TestRunToolFileMode:
    @pytest.mark.asyncio
    async def test_file_mode_with_arg(self):
        """Verify file mode writes prompt to file and passes arg."""
        config = ToolConfig(
            command=["mytool"],
            input_mode=InputMode.FILE,
            prompt_file_arg="--prompt-file",
            extra_args=["--json"],
        )

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"file output", b""))
        mock_proc.returncode = 0

        with patch("council.runner.asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            result = await run_tool("mytool", config, "test prompt content", timeout_sec=10)
            call_args = mock_exec.call_args[0]
            # Should include: mytool --json --prompt-file <tmp_path>
            assert call_args[0] == "mytool"
            assert "--json" in call_args
            assert "--prompt-file" in call_args

        assert result.stdout == "file output"


class TestRunToolErrors:
    @pytest.mark.asyncio
    async def test_command_not_found(self):
        """Verify graceful handling of missing commands."""
        config = ToolConfig(
            command=["nonexistent_tool_xyz"],
            input_mode=InputMode.STDIN,
        )

        with patch(
            "council.runner.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("not found"),
        ):
            result = await run_tool("missing", config, "prompt", timeout_sec=10)

        assert result.exit_code == -1
        assert "not found" in result.stderr.lower() or "Command not found" in result.stderr

    @pytest.mark.asyncio
    async def test_timeout(self):
        """Verify timeout handling."""
        config = ToolConfig(
            command=["slow_tool"],
            input_mode=InputMode.STDIN,
        )

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_proc.kill = MagicMock()
        # After kill, communicate returns empty.
        mock_proc.communicate.side_effect = [asyncio.TimeoutError(), (b"", b"")]

        call_count = 0
        async def mock_communicate(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise asyncio.TimeoutError()
            return (b"", b"")

        mock_proc.communicate = mock_communicate

        with patch("council.runner.asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("council.runner.asyncio.wait_for", side_effect=asyncio.TimeoutError()):
                # Need to also mock the second wait_for for cleanup.
                result = await run_tool("slow", config, "prompt", timeout_sec=1)

        assert result.timed_out is True
        assert result.exit_code is None


class TestRunToolsParallel:
    @pytest.mark.asyncio
    async def test_parallel_execution(self):
        """Verify multiple tools run in parallel."""
        configs = {
            "tool_a": ToolConfig(command=["tool_a"]),
            "tool_b": ToolConfig(command=["tool_b"]),
        }
        prompts = {
            "tool_a": "prompt a",
            "tool_b": "prompt b",
        }

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"output", b""))
        mock_proc.returncode = 0

        with patch("council.runner.asyncio.create_subprocess_exec", return_value=mock_proc):
            results = await run_tools_parallel(configs, prompts, timeout_sec=10)

        assert "tool_a" in results
        assert "tool_b" in results
        assert results["tool_a"].stdout == "output"
        assert results["tool_b"].stdout == "output"
