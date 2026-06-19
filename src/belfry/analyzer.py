"""Static analysis of a single Python script (the correctness-critical core).

`analyze(path)` reads a script's source, parses it with :mod:`ast`, and returns a
fully populated :class:`~belfry.models.ScriptInfo`. It NEVER executes the target
script -- everything is derived from the AST and the raw text.

Results are cached at module level keyed by ``(str(path), mtime)`` so repeated
calls for an unchanged file are essentially free.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from belfry.models import ArgInfo, ArgSpec, FileRef, ScriptInfo

# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #

# key: (str(path), mtime) -> ScriptInfo
_CACHE: dict[tuple[str, float], ScriptInfo] = {}


# --------------------------------------------------------------------------- #
# Name sets for file I/O classification
# --------------------------------------------------------------------------- #

# Attribute names that read a file (the path is the first positional arg unless
# otherwise noted).
_READER_ATTRS: frozenset[str] = frozenset(
    {
        "read_csv",
        "read_table",
        "read_excel",
        "read_parquet",
        "read_hdf",
        "read_json",
        "read_feather",
        "read_pickle",
        "read_stata",
        "read_fwf",
        "loadtxt",
        "genfromtxt",
        "fromfile",
        "open_dataset",
        "open_dataarray",
        "open_mfdataset",
        "open_zarr",
        "read_file",
        "from_disk",
        # read_text / read_bytes are handled specially (receiver is the path)
    }
)

# Attribute names that write a file.
_WRITER_ATTRS: frozenset[str] = frozenset(
    {
        "to_csv",
        "to_parquet",
        "to_hdf",
        "to_excel",
        "to_json",
        "to_feather",
        "to_pickle",
        "to_stata",
        "to_html",
        "to_latex",
        "to_netcdf",
        "to_zarr",
        "savefig",
        "savez",
        "savez_compressed",
        "savetxt",
        "write_html",
        "write_image",
        "imwrite",
        # save / write / dump / write_text / write_bytes handled specially
    }
)

# The path is captured from the receiver, not from an argument.
_RECEIVER_READER_ATTRS: frozenset[str] = frozenset({"read_text", "read_bytes"})
_RECEIVER_WRITER_ATTRS: frozenset[str] = frozenset({"write_text", "write_bytes"})

# Common keyword names that may carry a path on reader/writer calls.
_PATH_KWARGS: tuple[str, ...] = (
    "path_or_buf",
    "fname",
    "filename",
    "path",
    "file",
)

# Cell-marker detection: a line that is just `# %%` (or `#%%`) possibly indented.
# MULTILINE so `^` anchors to the start of every line in the full source.
_CELL_RE = re.compile(r"^\s*#\s?%%", re.MULTILINE)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def analyze(path: Path) -> ScriptInfo:
    """Statically analyze ``path`` and return a :class:`ScriptInfo`.

    Never raises: any failure is captured into ``ScriptInfo.parse_error`` with
    ``badge="error"``.
    """
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0

    key = (str(path), mtime)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    try:
        info = _analyze_uncached(path, mtime)
    except Exception as exc:  # pragma: no cover - last-resort guard
        info = ScriptInfo(
            path=path,
            mtime=mtime,
            badge="error",
            has_main=False,
            docstring=None,
            lead_comments=None,
            args=ArgSpec(style="none"),
            parse_error=str(exc),
        )
    _CACHE[key] = info
    return info


def _analyze_uncached(path: Path, mtime: float) -> ScriptInfo:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ScriptInfo(
            path=path,
            mtime=mtime,
            badge="error",
            has_main=False,
            docstring=None,
            lead_comments=None,
            args=ArgSpec(style="none"),
            parse_error=str(exc),
        )

    lead_comments = _extract_lead_comments(source)

    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError) as exc:
        return ScriptInfo(
            path=path,
            mtime=mtime,
            badge="error",
            has_main=False,
            docstring=None,
            lead_comments=lead_comments,
            args=ArgSpec(style="none"),
            parse_error=str(exc),
        )

    constants = _collect_constants(tree)
    docstring = _safe(lambda: ast.get_docstring(tree), None)
    has_main = _has_main_block(tree)
    is_cell = _CELL_RE.search(source) is not None

    args = _analyze_args(tree)

    if is_cell:
        badge = "cell-script"
    elif args.style in {"argparse", "click", "typer"}:
        badge = "cli"
    else:
        badge = "script"

    # Knobs note: scripts with no CLI args lean on module-level constants.
    if args.style == "none" or is_cell:
        note = "No CLI arguments; module-level constants act as the knobs."
        if note not in args.notes:
            args.notes.append(note)

    assignments = _collect_assignments(tree, constants)
    inputs, outputs = _collect_file_refs(tree, constants, assignments)

    return ScriptInfo(
        path=path,
        mtime=mtime,
        badge=badge,
        has_main=has_main,
        docstring=docstring,
        lead_comments=lead_comments,
        args=args,
        inputs=inputs,
        outputs=outputs,
        constants=constants,
        parse_error=None,
    )


# --------------------------------------------------------------------------- #
# Lead comments
# --------------------------------------------------------------------------- #


def _extract_lead_comments(source: str) -> str | None:
    """Return the leading contiguous block of ``#`` comment lines at the top.

    A shebang line is skipped. ``# %%`` cell markers are skipped but do not stop
    the scan, so the intro comment block of a cell-script is captured. Blank
    lines before any comment are skipped; a blank line *after* comments have
    started ends the block. The leading ``# `` is stripped from each line.
    """
    lines: list[str] = []
    started = False
    for raw_line in source.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if not started:
            # Leading blanks and a shebang are allowed before the block begins.
            if stripped == "":
                continue
            if stripped.startswith("#!"):
                continue

        if stripped.startswith("#"):
            if _CELL_RE.match(line):
                # A cell marker -- skip it but keep scanning for the intro text.
                started = True
                continue
            lines.append(_strip_comment_prefix(stripped))
            started = True
            continue

        # First non-comment, non-(skipped) line ends the block.
        if started:
            break
        # Not started and hit code immediately -> no lead comments.
        break

    text = "\n".join(lines).strip("\n")
    return text or None


def _strip_comment_prefix(stripped: str) -> str:
    """Strip a leading ``#`` (and one optional space) from a comment line."""
    body = stripped[1:]  # drop the '#'
    if body.startswith(" "):
        body = body[1:]
    return body


# --------------------------------------------------------------------------- #
# Constants table
# --------------------------------------------------------------------------- #


def _collect_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = <str|int|float constant>`` assignments."""
    constants: dict[str, str] = {}
    for node in tree.body:
        try:
            if isinstance(node, ast.Assign):
                if len(node.targets) != 1:
                    continue
                target = node.targets[0]
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                if node.value is None:
                    continue
                target = node.target
                value = node.value
            else:
                continue

            if not isinstance(target, ast.Name):
                continue
            if not isinstance(value, ast.Constant):
                continue
            if not isinstance(value.value, (str, int, float)) or isinstance(
                value.value, bool
            ):
                # bool is a subclass of int; skip True/False as "constants".
                continue
            constants[target.id] = str(value.value)
        except Exception:
            continue
    return constants


# --------------------------------------------------------------------------- #
# main block / args
# --------------------------------------------------------------------------- #


def _has_main_block(tree: ast.Module) -> bool:
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        try:
            test = node.test
            if (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == "__name__"
                and len(test.comparators) == 1
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value == "__main__"
            ):
                return True
        except Exception:
            continue
    return False


def _analyze_args(tree: ast.Module) -> ArgSpec:
    """Detect the CLI style and extract argument metadata."""
    add_arg_calls = _find_add_argument_calls(tree)
    if add_arg_calls:
        return _argparse_spec(add_arg_calls)

    click_typer = _detect_click_typer(tree)
    if click_typer is not None:
        return click_typer

    sys_argv = _detect_sys_argv(tree)
    if sys_argv is not None:
        return sys_argv

    return ArgSpec(style="none")


# --------------------------------------------------------------------------- #
# argparse
# --------------------------------------------------------------------------- #


def _find_add_argument_calls(tree: ast.Module) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "add_argument":
            calls.append(node)
    return calls


def _argparse_spec(calls: list[ast.Call]) -> ArgSpec:
    spec = ArgSpec(style="argparse")
    for call in calls:
        try:
            info = _argparse_one(call)
            if info is not None:
                spec.args.append(info)
        except Exception:
            continue
    return spec


def _argparse_one(call: ast.Call) -> ArgInfo | None:
    # Leading positional strings are the name / flag strings.
    flag_strings: list[str] = []
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            flag_strings.append(arg.value)
        else:
            break

    kwargs = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}

    help_text = _const_str(kwargs.get("help"))
    type_str = _unparse(kwargs.get("type"))
    default_str = _unparse(kwargs.get("default"))
    required = _const_bool(kwargs.get("required"))
    nargs = _unparse(kwargs.get("nargs"))
    choices = _literal_list(kwargs.get("choices"))
    action = _const_str(kwargs.get("action"))

    if not flag_strings:
        return None

    is_flag = flag_strings[0].startswith("-")
    if is_flag:
        name = "/".join(flag_strings)
        kind = "optional"
        if action in {"store_true", "store_false", "count"} or (
            action is not None and "BooleanOptional" in action
        ):
            kind = "flag"
    else:
        name = flag_strings[0]
        kind = "positional"

    return ArgInfo(
        name=name,
        kind=kind,
        help=help_text,
        type=type_str,
        default=default_str,
        required=required,
        nargs=nargs,
        choices=choices,
    )


# --------------------------------------------------------------------------- #
# click / typer
# --------------------------------------------------------------------------- #


def _detect_click_typer(tree: ast.Module) -> ArgSpec | None:
    """Best-effort detection of click/typer decorated entry points."""
    uses_click = False
    uses_typer = False

    # Imports give a hint at the style.
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root == "click":
                    uses_click = True
                elif root == "typer":
                    uses_typer = True
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root == "click":
                uses_click = True
            elif root == "typer":
                uses_typer = True

    decorated: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    decorator_hit_click = False
    decorator_hit_typer = False

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            dotted = _decorator_dotted(dec)
            if dotted is None:
                continue
            if _is_click_decorator(dotted):
                decorator_hit_click = True
                if node not in decorated:
                    decorated.append(node)
            elif _is_typer_decorator(dotted):
                decorator_hit_typer = True
                if node not in decorated:
                    decorated.append(node)

    if not (uses_click or uses_typer or decorator_hit_click or decorator_hit_typer):
        return None

    if decorator_hit_typer or (uses_typer and not decorator_hit_click):
        style = "typer"
    else:
        style = "click"

    spec = ArgSpec(style=style)

    # Pull @click.option / @click.argument metadata from decorators.
    for func in decorated:
        for dec in func.decorator_list:
            try:
                info = _click_decorator_arg(dec)
                if info is not None:
                    spec.args.append(info)
            except Exception:
                continue

    # For typer, parameters of the command function are the arguments.
    if style == "typer" and not spec.args:
        for func in decorated:
            try:
                spec.args.extend(_typer_params(func))
            except Exception:
                continue

    return spec


def _decorator_dotted(dec: ast.expr) -> str | None:
    """Return the dotted name of a decorator (handles plain and called forms)."""
    target = dec.func if isinstance(dec, ast.Call) else dec
    return _dotted_name(target)


def _is_click_decorator(dotted: str) -> bool:
    tail = dotted.split(".")[-1]
    return ("click" in dotted and tail in {"option", "argument", "command", "group"}) or (
        tail in {"option", "argument"} and "click" in dotted
    )


def _is_typer_decorator(dotted: str) -> bool:
    tail = dotted.split(".")[-1]
    return tail in {"command", "callback"} and "click" not in dotted


def _click_decorator_arg(dec: ast.expr) -> ArgInfo | None:
    if not isinstance(dec, ast.Call):
        return None
    dotted = _dotted_name(dec.func)
    if dotted is None:
        return None
    tail = dotted.split(".")[-1]
    if tail not in {"option", "argument"}:
        return None

    flag_strings: list[str] = []
    for arg in dec.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            flag_strings.append(arg.value)
        else:
            break
    if not flag_strings:
        return None

    kwargs = {kw.arg: kw.value for kw in dec.keywords if kw.arg is not None}
    help_text = _const_str(kwargs.get("help"))
    type_str = _unparse(kwargs.get("type"))
    default_str = _unparse(kwargs.get("default"))
    required = _const_bool(kwargs.get("required"))
    is_flag_kw = _const_bool(kwargs.get("is_flag"))

    dash = [f for f in flag_strings if f.startswith("-")]
    if tail == "argument" or not dash:
        name = flag_strings[0].lstrip("-")
        kind = "positional"
    else:
        name = "/".join(dash)
        kind = "flag" if is_flag_kw else "optional"

    return ArgInfo(
        name=name,
        kind=kind,
        help=help_text,
        type=type_str,
        default=default_str,
        required=required,
    )


def _typer_params(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ArgInfo]:
    out: list[ArgInfo] = []
    args = func.args
    posonly = list(getattr(args, "posonlyargs", []))
    regular = list(args.args)
    all_pos = posonly + regular
    defaults = list(args.defaults)
    # Align defaults to the tail of all_pos.
    pad = len(all_pos) - len(defaults)

    for idx, a in enumerate(all_pos):
        default_node = defaults[idx - pad] if idx >= pad else None
        type_str = _unparse(a.annotation)
        kind = "positional"
        default_str = None
        if default_node is not None:
            default_str = _unparse(default_node)
            # typer.Option(...) -> optional, typer.Argument(...) -> positional
            dn = _dotted_name(
                default_node.func if isinstance(default_node, ast.Call) else default_node
            )
            if dn and dn.split(".")[-1] == "Option":
                kind = "optional"
        out.append(
            ArgInfo(name=a.arg, kind=kind, type=type_str, default=default_str)
        )
    return out


# --------------------------------------------------------------------------- #
# sys.argv
# --------------------------------------------------------------------------- #


def _detect_sys_argv(tree: ast.Module) -> ArgSpec | None:
    """Detect direct ``sys.argv`` usage and which indices/names are used."""
    indices: set[int] = set()
    used_argv = False
    assignments: dict[int, str] = {}

    for node in ast.walk(tree):
        # Subscript like sys.argv[i]
        if isinstance(node, ast.Subscript) and _is_sys_argv(node.value):
            used_argv = True
            idx = _const_index(node.slice)
            if idx is not None:
                indices.add(idx)
        # len(sys.argv)
        elif isinstance(node, ast.Call) and _dotted_name(node.func) == "len":
            for a in node.args:
                if _is_sys_argv(a):
                    used_argv = True
        # bare reference sys.argv (e.g. for arg in sys.argv)
        elif _is_sys_argv(node):
            used_argv = True

    if not used_argv:
        return None

    # Capture `name = sys.argv[i]` assignments for friendlier notes.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        value = node.value
        if isinstance(value, ast.Subscript) and _is_sys_argv(value.value):
            idx = _const_index(value.slice)
            if idx is not None:
                assignments[idx] = node.targets[0].id

    spec = ArgSpec(style="sys.argv")
    notes: list[str] = []
    for i in sorted(indices):
        spec.args.append(ArgInfo(name=f"sys.argv[{i}]", kind="positional"))
        if i in assignments:
            notes.append(f"sys.argv[{i}] -> {assignments[i]}")
        else:
            notes.append(f"sys.argv[{i}]")
    if not indices:
        notes.append("sys.argv read (indices not statically determinable).")
    spec.notes = notes
    return spec


def _is_sys_argv(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "argv"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def _const_index(slice_node: ast.AST) -> int | None:
    # Python 3.9+: slice is the expression directly.
    if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, int):
        return slice_node.value
    return None


# --------------------------------------------------------------------------- #
# File references (inputs / outputs)
# --------------------------------------------------------------------------- #


def _collect_assignments(
    tree: ast.Module, constants: dict[str, str]
) -> dict[str, list[tuple[str, bool, str]]]:
    """Map ``NAME -> [(raw, resolved, kind), ...]`` for path-like assignments.

    Unlike :func:`_collect_constants` (module-level scalar literals only), this
    captures simple ``NAME = <path-expr>`` assignments in any scope -- f-strings,
    ``Path(...) / "literal"``, ``str(var)`` -- so that a later file-I/O call that
    references the variable (e.g. ``df.to_csv(stations_out)``) can recover the
    literal path fragments. The rendered ``raw`` may be unresolved (carry
    ``{...}`` placeholders) but still contains the literal filename text.

    A name assigned along several branches (e.g. ``output_file`` set in both an
    ``if`` and its ``else``) keeps every distinct render, so each variant becomes
    its own FileRef rather than being dropped as ambiguous.
    """
    rendered: dict[str, list[tuple[str, bool, str]]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        name = node.targets[0].id
        if name in constants:
            # A module-level scalar constant already covers this name.
            continue
        if not _is_pathlike_value(node.value):
            continue
        try:
            entry = _render_path(node.value, constants, {})
        except Exception:
            continue
        bucket = rendered.setdefault(name, [])
        if entry not in bucket:
            bucket.append(entry)

    return rendered


def _is_pathlike_value(value: ast.expr) -> bool:
    """Heuristic: is this RHS worth rendering as a candidate path expression?

    We only record assignments whose value plausibly builds a path string -- an
    f-string, a string concat/``Path /`` join, a ``Path(...)``/``str(...)`` call,
    or a string literal. This keeps the assignment table small and avoids
    capturing arbitrary numeric/object assignments.
    """
    if isinstance(value, ast.JoinedStr):
        return True
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return True
    if isinstance(value, ast.BinOp) and isinstance(value.op, (ast.Add, ast.Div)):
        return True
    if isinstance(value, ast.Call):
        dotted = _dotted_name(value.func) or ""
        tail = dotted.split(".")[-1]
        return tail in {"Path", "str"} or dotted.endswith("os.path.join")
    return False


def _collect_file_refs(
    tree: ast.Module,
    constants: dict[str, str],
    assignments: dict[str, list[tuple[str, bool, str]]],
) -> tuple[list[FileRef], list[FileRef]]:
    inputs: list[FileRef] = []
    outputs: list[FileRef] = []
    seen_in: set[tuple[str, str, int]] = set()
    seen_out: set[tuple[str, str, int]] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        try:
            classified = _classify_call(node, constants, assignments)
        except Exception:
            classified = []
        for direction, ref in classified:
            key = (ref.raw, ref.func, ref.lineno)
            if direction == "input":
                if key not in seen_in:
                    seen_in.add(key)
                    inputs.append(ref)
            else:
                if key not in seen_out:
                    seen_out.add(key)
                    outputs.append(ref)

    return inputs, outputs


def _classify_call(
    call: ast.Call,
    constants: dict[str, str],
    assignments: dict[str, list[tuple[str, bool, str]]],
) -> list[tuple[str, FileRef]]:
    """Return a list of ("input"|"output", FileRef); empty if not file I/O.

    A list (rather than a single ref) lets a path variable assigned along
    several branches contribute one FileRef per distinct render.
    """
    func = call.func

    # bare open(path, mode)
    if isinstance(func, ast.Name) and func.id == "open":
        return _handle_open(call, constants, assignments)

    if not isinstance(func, ast.Attribute):
        return []

    attr = func.attr

    # Receiver-as-path families (read_text/read_bytes/write_text/write_bytes).
    if attr in _RECEIVER_READER_ATTRS:
        return _receiver_ref(call, "input", constants, assignments)
    if attr in _RECEIVER_WRITER_ATTRS:
        return _receiver_ref(call, "output", constants, assignments)

    # Plain reader / writer attrs (path is an argument).
    if attr in _READER_ATTRS:
        return _arg_ref(call, "input", constants, assignments)
    if attr in _WRITER_ATTRS:
        return _arg_ref(call, "output", constants, assignments)

    # Ambiguous attrs requiring receiver inspection.
    recv = _dotted_name(func.value)
    recv_l = (recv or "").lower()

    if attr == "load":
        if _receiver_contains(recv_l, ("np", "numpy", "json", "pickle", "joblib")) or (
            isinstance(func.value, ast.Call)
            and isinstance(func.value.func, ast.Name)
            and func.value.func.id == "open"
        ):
            return _arg_ref(call, "input", constants, assignments)
        return []

    if attr == "save":
        if _receiver_contains(recv_l, ("np", "numpy", "plt", "fig", "image", "img")):
            return _arg_ref(call, "output", constants, assignments)
        return []

    if attr == "write":
        if _receiver_contains(recv_l, ("gmsh",)):
            return _arg_ref(call, "output", constants, assignments)
        return []

    # dump (json.dump/pickle.dump) writes to a handle; path comes from open().
    if attr == "dump":
        return []

    return []


def _handle_open(
    call: ast.Call,
    constants: dict[str, str],
    assignments: dict[str, list[tuple[str, bool, str]]],
) -> list[tuple[str, FileRef]]:
    mode = "r"
    if len(call.args) >= 2:
        m = _const_str(call.args[1])
        if m is not None:
            mode = m
    else:
        for kw in call.keywords:
            if kw.arg == "mode":
                m = _const_str(kw.value)
                if m is not None:
                    mode = m

    is_write = any(c in mode for c in ("w", "a", "x"))
    direction = "output" if is_write else "input"
    label = "open(w)" if is_write else "open(r)"

    path_node = _first_path_node(call)
    if path_node is None:
        return []
    return [
        (direction, FileRef(raw=raw, func=label, lineno=call.lineno, resolved=res, kind=k))
        for raw, res, k in _render_candidates(path_node, constants, assignments)
    ]


def _arg_ref(
    call: ast.Call,
    direction: str,
    constants: dict[str, str],
    assignments: dict[str, list[tuple[str, bool, str]]],
) -> list[tuple[str, FileRef]]:
    path_node = _first_path_node(call)
    if path_node is None:
        return []
    label = _callee_label(call.func)
    return [
        (direction, FileRef(raw=raw, func=label, lineno=call.lineno, resolved=res, kind=k))
        for raw, res, k in _render_candidates(path_node, constants, assignments)
    ]


def _receiver_ref(
    call: ast.Call,
    direction: str,
    constants: dict[str, str],
    assignments: dict[str, list[tuple[str, bool, str]]],
) -> list[tuple[str, FileRef]]:
    func = call.func
    if not isinstance(func, ast.Attribute):
        return []
    label = _callee_label(func)
    return [
        (direction, FileRef(raw=raw, func=label, lineno=call.lineno, resolved=res, kind=k))
        for raw, res, k in _render_candidates(func.value, constants, assignments)
    ]


def _render_candidates(
    node: ast.AST,
    constants: dict[str, str],
    assignments: dict[str, list[tuple[str, bool, str]]],
) -> list[tuple[str, bool, str]]:
    """Render ``node`` to one or more ``(raw, resolved, kind)`` candidates.

    When the path is a bare ``Name`` (or ``str(Name)`` / ``Path(Name)``) bound to
    several distinct path-like assignments, each variant is returned so every
    one surfaces as its own FileRef. Otherwise a single render is returned.
    """
    target = node
    # See through a single str(...) / Path(...) wrapper to the inner name.
    if (
        isinstance(target, ast.Call)
        and len(target.args) == 1
        and not target.keywords
    ):
        tail = (_dotted_name(target.func) or "").split(".")[-1]
        if tail in {"str", "Path"}:
            target = target.args[0]

    if isinstance(target, ast.Name) and target.id not in constants:
        variants = assignments.get(target.id)
        if variants:
            return list(variants)

    return [render_path(node, constants, assignments)]


def _first_path_node(call: ast.Call) -> ast.expr | None:
    if call.args:
        return call.args[0]
    for kw in call.keywords:
        if kw.arg in _PATH_KWARGS:
            return kw.value
    return None


def _callee_label(func: ast.expr) -> str:
    label = _unparse(func)
    if label is None:
        if isinstance(func, ast.Attribute):
            return func.attr
        return "<call>"
    # Truncate overly long dotted chains for readability.
    if len(label) > 60:
        label = label[:57] + "..."
    return label


def _receiver_contains(recv_lower: str, needles: tuple[str, ...]) -> bool:
    """True if any needle equals a whole token of the dotted receiver.

    Matching is on whole, dot/bracket/paren-delimited tokens only -- never an
    arbitrary substring -- so e.g. ``config.save(...)`` is NOT treated as a
    figure ``save`` just because "config" contains "fig", and ``my_json.load``
    is NOT treated as ``json.load``. This kills the false positives the prior
    substring fallback produced (config/myconfig/imager for save; my_json /
    pickled_obj / jsonish for load).
    """
    parts = re.split(r"[.\(\)\[\] ]", recv_lower)
    partset = {p for p in parts if p}
    return any(n in partset for n in needles)


# --------------------------------------------------------------------------- #
# Path rendering
# --------------------------------------------------------------------------- #


def render_path(
    node: ast.AST,
    constants: dict[str, str],
    assignments: dict[str, list[tuple[str, bool, str]]] | None = None,
) -> tuple[str, bool, str]:
    """Render a path expression to ``(raw, resolved, kind)``.

    ``resolved`` is True only when the value is fully literal (or every variable
    resolves to a known module-level constant). ``kind`` is one of
    ``"literal"``, ``"fstring"``, ``"expr"``.

    ``assignments`` (optional) maps variable names to previously rendered
    ``(raw, resolved, kind)`` tuples, letting a bare ``Name`` or ``str(var)``
    recover the literal path fragments of a single earlier assignment.
    """
    try:
        return _render_path(node, constants, assignments or {})
    except Exception:
        return _unparse(node) or "<expr>", False, "expr"


def _render_path(
    node: ast.AST,
    constants: dict[str, str],
    assignments: dict[str, list[tuple[str, bool, str]]],
) -> tuple[str, bool, str]:
    # Literal string / bytes / number.
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value, True, "literal"
        return str(node.value), True, "literal"

    # f-string
    if isinstance(node, ast.JoinedStr):
        return _render_fstring(node, constants, assignments)

    # bare Name -- resolve from constants first, then from an assignment. When a
    # name has several recorded renders (branchy assignment) the first is used
    # for nested expressions; the ref builders enumerate all candidates.
    if isinstance(node, ast.Name):
        if node.id in constants:
            return constants[node.id], True, "expr"
        if node.id in assignments and assignments[node.id]:
            return assignments[node.id][0]
        return node.id, False, "expr"

    # BinOp: Add (concat) or Div (pathlib /)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Div)):
        left_raw, left_res, _ = _render_path(node.left, constants, assignments)
        right_raw, right_res, _ = _render_path(node.right, constants, assignments)
        if isinstance(node.op, ast.Div):
            joined = _join_paths(left_raw, right_raw)
        else:
            joined = left_raw + right_raw
        return joined, (left_res and right_res), "expr"

    # Call: Path(...) / os.path.join(...) / str(...). Only treat a ``.join()``
    # as a filesystem join when the receiver is os.path-qualified -- otherwise
    # an ordinary string join like ``",".join(parts)`` would be misread as a
    # path.
    if isinstance(node, ast.Call):
        dotted = _dotted_name(node.func) or ""
        tail = dotted.split(".")[-1]
        receiver = dotted[: -len(tail) - 1] if "." in dotted else ""

        # str(x) / Path(x) wrapping a single arg is transparent for our purposes.
        if tail in {"str", "Path"} and len(node.args) == 1 and not node.keywords:
            return _render_path(node.args[0], constants, assignments)

        is_path_join = tail == "join" and (
            receiver.endswith("os.path") or receiver.endswith("path")
        )
        if tail == "Path" or dotted.endswith("os.path.join") or is_path_join:
            parts: list[str] = []
            resolved = True
            for a in node.args:
                r, res, _ = _render_path(a, constants, assignments)
                parts.append(r)
                resolved = resolved and res
            if not parts:
                return _unparse(node) or "<expr>", False, "expr"
            if tail == "Path" and len(parts) == 1:
                return parts[0], resolved, "expr"
            return _join_paths_many(parts), resolved, "expr"

    # Fallback.
    return _unparse(node) or "<expr>", False, "expr"


def _render_fstring(
    node: ast.JoinedStr,
    constants: dict[str, str],
    assignments: dict[str, list[tuple[str, bool, str]]],
) -> tuple[str, bool, str]:
    pieces: list[str] = []
    resolved = True
    for part in node.values:
        if isinstance(part, ast.Constant):
            pieces.append(str(part.value))
        elif isinstance(part, ast.FormattedValue):
            inner = part.value
            # A conversion (!r/!s/!a) or format spec (:04d, :>10, ...) means the
            # literal path cannot be reproduced from the constant alone, so even
            # when the inner value is known we substitute for display only and
            # mark the ref unresolved.
            has_conversion = part.conversion != -1
            has_format_spec = part.format_spec is not None
            if (
                isinstance(inner, ast.Name)
                and inner.id in constants
                and not has_conversion
                and not has_format_spec
            ):
                pieces.append(constants[inner.id])
            elif isinstance(inner, ast.Name) and inner.id in constants:
                # Known constant but with a conversion/spec we cannot apply.
                suffix = ""
                if has_conversion:
                    suffix += "!" + chr(part.conversion)
                if has_format_spec:
                    suffix += ":" + _format_spec_text(part.format_spec)
                pieces.append("{" + constants[inner.id] + suffix + "}")
                resolved = False
            else:
                pieces.append("{" + (_unparse(inner) or "?") + "}")
                resolved = False
        else:
            pieces.append("{?}")
            resolved = False
    return "".join(pieces), resolved, "fstring"


def _format_spec_text(spec: ast.expr | None) -> str:
    """Render an f-string format spec node (a JoinedStr) to its literal text.

    e.g. the ``:04d`` part of ``f"{n:04d}"`` -> ``"04d"``. Nested expressions in
    the spec are kept as ``{...}`` placeholders.
    """
    if spec is None:
        return ""
    if isinstance(spec, ast.JoinedStr):
        out: list[str] = []
        for v in spec.values:
            if isinstance(v, ast.Constant):
                out.append(str(v.value))
            else:
                out.append("{" + (_unparse(v) or "?") + "}")
        return "".join(out)
    return _unparse(spec) or "?"


def _join_paths(left: str, right: str) -> str:
    if left and not left.endswith("/"):
        left = left + "/"
    right = right.lstrip("/")
    return left + right


def _join_paths_many(parts: list[str]) -> str:
    out = parts[0]
    for p in parts[1:]:
        out = _join_paths(out, p)
    return out


# --------------------------------------------------------------------------- #
# Small AST helpers
# --------------------------------------------------------------------------- #


def _dotted_name(node: ast.AST | None) -> str | None:
    """Return a dotted-name string for Name/Attribute chains, else None."""
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        if base is None:
            return node.attr
        return f"{base}.{node.attr}"
    return None


def _unparse(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None


def _const_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _const_bool(node: ast.AST | None) -> bool | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    return None


def _literal_list(node: ast.AST | None) -> list[str] | None:
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return None
    out: list[str] = []
    for elt in node.elts:
        if isinstance(elt, ast.Constant):
            out.append(str(elt.value))
        else:
            out.append(_unparse(elt) or "?")
    return out or None


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default
