"""Tests for cross-platform compatibility utilities."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from council.compat import (
    _UNIX_ABS_RE,
    _WIN_ABS_RE,
    _basename,
    as_posix,
    normalize_glob,
    normalize_path_str,
    redact_abs_paths,
)


class TestNormalizePathStr:
    def test_forward_slashes_unchanged(self):
        assert normalize_path_str("src/utils/helpers.py") == "src/utils/helpers.py"

    def test_backslashes_converted(self):
        assert normalize_path_str("src\\utils\\helpers.py") == "src/utils/helpers.py"

    def test_mixed_separators(self):
        assert normalize_path_str("src/utils\\helpers.py") == "src/utils/helpers.py"

    def test_windows_absolute_path(self):
        assert normalize_path_str("C:\\Users\\dev\\project\\main.py") == "C:/Users/dev/project/main.py"

    def test_empty_string(self):
        assert normalize_path_str("") == ""

    def test_single_component(self):
        assert normalize_path_str("file.py") == "file.py"

    def test_trailing_separator(self):
        assert normalize_path_str("src\\utils\\") == "src/utils/"


class TestAsPosix:
    def test_unix_path(self):
        p = Path("src/utils/helpers.py")
        result = as_posix(p)
        assert "/" in result or result == "src/utils/helpers.py"
        assert "\\" not in result

    def test_simple_path(self):
        p = Path("file.py")
        assert as_posix(p) == "file.py"


class TestNormalizeGlob:
    def test_returns_sorted_list(self, tmp_path: Path):
        # Create some files.
        (tmp_path / "b.txt").write_text("b")
        (tmp_path / "a.txt").write_text("a")
        result = normalize_glob(str(tmp_path / "*.txt"))
        assert len(result) == 2
        # Should be sorted.
        basenames = [Path(r).name for r in result]
        assert basenames == sorted(basenames)

    def test_results_use_forward_slashes(self, tmp_path: Path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "file.py").write_text("x")
        result = normalize_glob(str(tmp_path / "**" / "*.py"), recursive=True)
        for r in result:
            assert "\\" not in r

    def test_empty_result(self, tmp_path: Path):
        result = normalize_glob(str(tmp_path / "*.nonexistent"))
        assert result == []


class TestBasename:
    def test_unix_path(self):
        assert _basename("/home/user/project/file.py") == "file.py"

    def test_windows_path(self):
        assert _basename("C:\\Users\\dev\\project\\file.py") == "file.py"

    def test_mixed_path(self):
        assert _basename("/home/user\\project/file.py") == "file.py"

    def test_trailing_slash(self):
        assert _basename("/home/user/project/") == "project"

    def test_single_component(self):
        assert _basename("file.py") == "file.py"


class TestRedactAbsPaths:
    # --- Unix paths ---
    def test_redacts_unix_home(self):
        text = "Error in /home/user/project/src/auth.py"
        result = redact_abs_paths(text)
        assert "/home/user" not in result
        assert "<REDACTED>/auth.py" in result

    def test_redacts_unix_users(self):
        text = "File: /Users/developer/code/main.py"
        result = redact_abs_paths(text)
        assert "/Users/developer" not in result
        assert "<REDACTED>/main.py" in result

    def test_redacts_var_path(self):
        text = "Log at /var/log/app/error.log"
        result = redact_abs_paths(text)
        assert "/var/log" not in result
        assert "<REDACTED>/error.log" in result

    def test_preserves_relative_paths(self):
        text = "src/auth.py:42"
        result = redact_abs_paths(text)
        assert result == text

    def test_preserves_urls(self):
        text = "See https://example.com/home/page for details"
        result = redact_abs_paths(text)
        assert "https://example.com/home/page" in result

    # --- Windows paths ---
    def test_redacts_windows_users(self):
        text = "Error in C:\\Users\\dev\\project\\src\\main.py"
        result = redact_abs_paths(text)
        assert "C:\\Users\\dev" not in result
        assert "<REDACTED>/main.py" in result

    def test_redacts_windows_forward_slash(self):
        text = "File: C:/Users/dev/project/main.py"
        result = redact_abs_paths(text)
        assert "C:/Users/dev" not in result
        assert "<REDACTED>/main.py" in result

    def test_redacts_windows_projects(self):
        text = "Path: D:\\projects\\myapp\\src\\index.ts"
        result = redact_abs_paths(text)
        assert "D:\\projects" not in result
        assert "<REDACTED>/index.ts" in result

    def test_redacts_windows_program_files(self):
        text = "Installed at C:\\Program Files\\App\\bin\\tool.exe"
        result = redact_abs_paths(text)
        assert "C:\\Program Files" not in result
        assert "<REDACTED>/tool.exe" in result

    def test_multiple_paths_in_text(self):
        text = (
            "Source: /home/user/project/src/app.py\n"
            "Config: C:\\Users\\dev\\config\\settings.yml"
        )
        result = redact_abs_paths(text)
        assert "/home/user" not in result
        assert "C:\\Users\\dev" not in result
        assert "<REDACTED>/app.py" in result
        assert "<REDACTED>/settings.yml" in result

    def test_preserves_short_windows_drive(self):
        # Just "C:" shouldn't be redacted.
        text = "drive is C:"
        result = redact_abs_paths(text)
        assert result == text


class TestUnixAbsRegex:
    def test_matches_common_roots(self):
        for root in ("home", "Users", "root", "var", "tmp", "opt", "usr"):
            path = f"/{root}/user/project/file.py"
            assert _UNIX_ABS_RE.search(path) is not None


class TestWinAbsRegex:
    def test_matches_drive_letter(self):
        assert _WIN_ABS_RE.search("C:\\Users\\dev\\file.py") is not None
        assert _WIN_ABS_RE.search("D:/projects/app/main.go") is not None

    def test_no_match_without_known_root(self):
        # Arbitrary folder after drive letter shouldn't match.
        assert _WIN_ABS_RE.search("C:\\random\\file.py") is None
