"""Tests for belfry.scanner discovery, focused on the fallback walk."""
from __future__ import annotations

import sys
from pathlib import Path

# Make belfry importable whether or not it is pip-installed (editable or not).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from belfry.scanner import _discover_walk  # noqa: E402


def test_walk_ignores_only_below_root(tmp_path: Path):
    # A scan root (or ancestor) named like an ignored dir must not suppress
    # everything: the ignore set should apply only to components below root.
    root = tmp_path / "build"  # 'build' is in _IGNORE_DIRS
    (root / "sub").mkdir(parents=True)
    (root / "script.py").write_text("print(1)\n")
    (root / "sub" / "mod.py").write_text("print(2)\n")

    names = {r.path.name for r in _discover_walk(root, recurse=True)}
    assert "script.py" in names
    assert "mod.py" in names


def test_walk_skips_ignored_dirs_below_root(tmp_path: Path):
    root = tmp_path / "project"
    (root / "__pycache__").mkdir(parents=True)
    (root / "keep.py").write_text("print(1)\n")
    (root / "__pycache__" / "cached.py").write_text("print(2)\n")

    names = {r.path.name for r in _discover_walk(root, recurse=True)}
    assert "keep.py" in names
    assert "cached.py" not in names
