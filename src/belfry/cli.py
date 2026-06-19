"""Command-line entry point for belfry.

Registered as the ``belfry`` console script (see pyproject). Resolves the target
directory, then launches the Textual TUI rooted there.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="belfry",
        description="Ring a Bell -- a TUI for rediscovering what your Python scripts do.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        type=Path,
        help="directory to scan for .py files (default: current directory)",
    )
    parser.add_argument(
        "--no-recurse",
        action="store_true",
        help="only scan the top-level directory, not subdirectories",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    path: Path = args.path.expanduser().resolve()
    if not path.exists():
        print(f"belfry: error: path does not exist: {path}", file=sys.stderr)
        return 2
    if not path.is_dir():
        print(f"belfry: error: not a directory: {path}", file=sys.stderr)
        return 2

    # Import here so --help works even if textual/deps are mid-install.
    from belfry.app import BelfryApp

    app = BelfryApp(root=path, recurse=not args.no_recurse)
    app.run()
    return 0
