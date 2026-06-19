"""Discover the .py files rab should analyze.

The scanner prefers git for enumeration so it natively honors .gitignore and
picks up new-but-not-ignored files. When the target is not inside a git repo
(or git is unavailable) it falls back to a filesystem walk with a built-in
ignore set.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from rab.models import FileRecord

# Directory components skipped by the filesystem-walk fallback.
_IGNORE_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "node_modules",
    ".ipynb_checkpoints",
    "site-packages",
    ".mypy_cache",
    ".pytest_cache",
    "build",
    "dist",
}

_GIT_TIMEOUT = 5


def discover(root: Path, recurse: bool = True) -> list[FileRecord]:
    """Return .py files under ``root``, most-recently-modified first.

    When ``recurse`` is False only direct children of ``root`` are considered.
    """
    root = Path(root)

    records: list[FileRecord] | None = None
    if _is_git_repo(root):
        records = _discover_git(root, recurse)

    if records is None:
        records = _discover_walk(root, recurse)

    records.sort(key=lambda rec: rec.mtime, reverse=True)
    return records


def _is_git_repo(root: Path) -> bool:
    """True if ``root`` lies inside a git work tree."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def _discover_git(root: Path, recurse: bool) -> list[FileRecord] | None:
    """Enumerate via two ``git ls-files`` calls; None on any failure."""
    tracked = _git_ls_files(root, ["ls-files", "-z", "--", "*.py"])
    if tracked is None:
        return None
    untracked = _git_ls_files(
        root, ["ls-files", "-z", "--others", "--exclude-standard", "--", "*.py"]
    )
    if untracked is None:
        return None

    records: list[FileRecord] = []
    seen: set[Path] = set()
    # git paths are relative to root; tracked entries win over untracked.
    for rel, is_tracked in [(p, True) for p in tracked] + [
        (p, False) for p in untracked
    ]:
        abs_path = (root / rel).resolve()
        if abs_path in seen:
            continue
        if recurse is False and abs_path.parent != root.resolve():
            continue
        rec = _make_record(abs_path, is_tracked)
        if rec is not None:
            seen.add(abs_path)
            records.append(rec)
    return records


def _git_ls_files(root: Path, args: list[str]) -> list[str] | None:
    """Run a git ls-files variant; return relative paths or None on failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return [entry for entry in result.stdout.split("\x00") if entry]


def _discover_walk(root: Path, recurse: bool) -> list[FileRecord]:
    """Filesystem fallback honoring the built-in ignore set."""
    records: list[FileRecord] = []
    if recurse:
        candidates = root.rglob("*.py")
    else:
        candidates = (p for p in root.iterdir() if p.suffix == ".py")

    for path in candidates:
        # Only inspect path components BELOW root; an ancestor (or root itself)
        # named like an ignored dir -- e.g. a project living under build/ or
        # venv/ -- must not cause every file to be skipped.
        try:
            rel = path.relative_to(root)
        except ValueError:
            rel = path
        if any(part in _IGNORE_DIRS for part in rel.parts):
            continue
        rec = _make_record(path, tracked=False)
        if rec is not None:
            records.append(rec)
    return records


def _make_record(path: Path, tracked: bool) -> FileRecord | None:
    """Build a FileRecord, returning None if the file vanished."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return FileRecord(path=path, mtime=mtime, tracked=tracked)
