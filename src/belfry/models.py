"""Shared data structures for belfry (the contract every module conforms to).

Every other belfry module imports the dataclasses defined here. Do NOT change the
public field names or signatures of these classes when implementing other
modules; conform to them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ArgInfo:
    """A single command-line argument discovered in a script."""

    name: str                       # "run_dir", or "--output/-o" for optionals
    kind: str                       # "positional" | "optional" | "flag"
    help: str | None = None
    type: str | None = None         # e.g. "Path", "int", "float", "str"
    default: str | None = None
    required: bool | None = None
    nargs: str | None = None
    choices: list[str] | None = None


@dataclass
class ArgSpec:
    """How a script accepts command-line arguments."""

    style: str                      # "argparse" | "click" | "typer" | "sys.argv" | "none"
    args: list[ArgInfo] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class FileRef:
    """A file path referenced (read or written) by a script."""

    raw: str                        # rendered path expr, e.g. ./runs/0000000060/ or f-string template
    func: str                       # the producing call, e.g. "pd.read_csv", "plt.savefig", "open(w)"
    lineno: int
    resolved: bool                  # True if fully literal; False if unresolved variables remain
    kind: str                       # "literal" | "fstring" | "expr"


@dataclass
class ScriptInfo:
    """Full static analysis of one Python script."""

    path: Path
    mtime: float
    badge: str                      # "cell-script" | "cli" | "script" | "error"
    has_main: bool
    docstring: str | None
    lead_comments: str | None
    args: ArgSpec
    inputs: list[FileRef] = field(default_factory=list)
    outputs: list[FileRef] = field(default_factory=list)
    constants: dict[str, str] = field(default_factory=dict)
    parse_error: str | None = None


@dataclass
class GitInfo:
    """Last commit that touched a file."""

    short_hash: str
    date: str                       # commit date, e.g. "2026-06-16"
    subject: str


@dataclass
class FileRecord:
    """A discovered .py file (cheap metadata only, gathered up front)."""

    path: Path
    mtime: float
    tracked: bool
