"""Cross-platform compatibility utilities.

Normalises path separators and glob results so that code running on
Windows produces the same forward-slash paths used on macOS / Linux.
"""

from __future__ import annotations

import glob as globmod
import os
import re
from pathlib import Path, PurePosixPath


def normalize_path_str(path: str) -> str:
    r"""Replace backslash separators with forward slashes.

    >>> normalize_path_str(r"src\utils\helpers.py")
    'src/utils/helpers.py'
    >>> normalize_path_str("src/utils/helpers.py")
    'src/utils/helpers.py'
    """
    return path.replace("\\", "/")


def as_posix(path: Path) -> str:
    """Return the POSIX string form of a Path (forward slashes).

    Unlike ``Path.as_posix()``, this is always available – even on
    Windows ``PureWindowsPath`` objects.
    """
    return normalize_path_str(str(path))


def normalize_glob(pattern: str, *, recursive: bool = True) -> list[str]:
    """Run ``glob.glob`` and normalise results to forward-slash paths.

    On Windows, ``glob.glob`` returns results with backslash separators;
    this wrapper ensures all callers get consistent POSIX-style paths.
    """
    matches = sorted(globmod.glob(pattern, recursive=recursive))
    return [normalize_path_str(m) for m in matches]


# ---------------------------------------------------------------------------
# Windows-aware absolute-path redaction
# ---------------------------------------------------------------------------

# Unix absolute paths rooted at well-known directories.
_UNIX_ABS_RE = re.compile(
    r"(?<![:\w/])"
    r"(/(?:home|Users|root|var|tmp|opt|usr|etc|private|mnt|media|srv|data|app|workspace)"
    r"(?:/[^\s:,;'\"\\)\]}>]+)+)"
)

# Windows absolute paths: C:\Users\..., D:\projects\...  (drive letter + backslash or forward slash).
_WIN_ABS_RE = re.compile(
    r"(?<![:\w])"
    r"([A-Za-z]:[/\\]"
    r"(?:(?:Users|home|Windows|Program Files|Program Files \(x86\)|projects?|repos?|dev|workspace|data|tmp|temp)"
    r"[/\\][^\s:;'\")\]}>]+))"
)


def _basename(path_str: str) -> str:
    """Extract the basename from a path with either separator style."""
    # Normalise to forward slashes then take the last component.
    normed = normalize_path_str(path_str).rstrip("/")
    return normed.rsplit("/", 1)[-1] if "/" in normed else normed


def redact_abs_paths(text: str) -> str:
    """Replace absolute filesystem paths with ``<REDACTED>/basename``.

    Handles both Unix-style (``/home/user/...``) and Windows-style
    (``C:\\Users\\...``) absolute paths.
    """
    def _replace_unix(m: re.Match[str]) -> str:
        base = _basename(m.group(1))
        return f"<REDACTED>/{base}" if base else "<REDACTED>"

    def _replace_win(m: re.Match[str]) -> str:
        base = _basename(m.group(1))
        return f"<REDACTED>/{base}" if base else "<REDACTED>"

    text = _UNIX_ABS_RE.sub(_replace_unix, text)
    text = _WIN_ABS_RE.sub(_replace_win, text)
    return text
