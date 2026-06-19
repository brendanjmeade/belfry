"""Look up the last commit that touched a file.

A single ``git log -1`` call is used, with a unit-separator delimiter so the
commit subject can contain anything. Any failure (no repo, untracked file,
missing git, timeout, malformed output) returns None so callers can fall back
to showing the file's mtime instead.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from rab.models import GitInfo

# ASCII unit separator (0x1f) keeps the format unambiguous vs. commit text.
_FORMAT = "%h%x1f%cs%x1f%s"
_SEP = "\x1f"
_GIT_TIMEOUT = 5


def last_commit(path: Path, root: Path) -> GitInfo | None:
    """Return the most recent commit touching ``path``, or None on any failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "log", "-1", f"--format={_FORMAT}", "--", str(path)],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=True,
        )
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        subprocess.SubprocessError,
    ):
        return None

    out = result.stdout.strip()
    if not out:
        # Untracked file: git log produces no output.
        return None

    parts = out.split(_SEP)
    if len(parts) != 3:
        return None

    short_hash, date, subject = parts
    return GitInfo(short_hash=short_hash, date=date, subject=subject)
