"""Tests for belfry.analyzer.analyze() against the documented contract.

These tests treat the analyzer as a black box: they parse small fixture
scripts (never executing them) and assert the static-analysis facts the
contract promises -- badges, argument styles, hardcoded inputs/outputs,
captured constants, and graceful handling of broken source.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make belfry importable whether or not it is pip-installed (editable or not).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from belfry.analyzer import analyze  # noqa: E402
from belfry.models import ScriptInfo  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _raws(refs) -> list[str]:
    """Collect the .raw strings from a list of FileRef objects."""
    return [ref.raw for ref in refs]


def _contains(refs, needle: str) -> bool:
    """True if any FileRef.raw contains the given substring."""
    return any(needle in ref.raw for ref in refs)


# --------------------------------------------------------------------------- #
# argparse_tool.py -- a classic CLI
# --------------------------------------------------------------------------- #
def test_argparse_badge_is_cli():
    info = analyze(FIXTURES / "argparse_tool.py")
    assert isinstance(info, ScriptInfo)
    assert info.badge == "cli"


def test_argparse_has_main():
    info = analyze(FIXTURES / "argparse_tool.py")
    assert info.has_main is True


def test_argparse_style():
    info = analyze(FIXTURES / "argparse_tool.py")
    assert info.args.style == "argparse"


def test_argparse_arg_names_include_run_dir_and_output():
    info = analyze(FIXTURES / "argparse_tool.py")
    names = [a.name for a in info.args.args]
    assert any("run_dir" in n for n in names), names
    assert any("output" in n for n in names), names


def test_argparse_has_a_positional():
    info = analyze(FIXTURES / "argparse_tool.py")
    kinds = {a.kind for a in info.args.args}
    assert "positional" in kinds, [a.name for a in info.args.args]
    # The positional should be run_dir.
    positionals = [a for a in info.args.args if a.kind == "positional"]
    assert any("run_dir" in a.name for a in positionals)


def test_argparse_inputs_contain_input_csv():
    info = analyze(FIXTURES / "argparse_tool.py")
    assert _contains(info.inputs, "input.csv"), _raws(info.inputs)


def test_argparse_outputs_contain_result_and_fig():
    info = analyze(FIXTURES / "argparse_tool.py")
    out_raws = _raws(info.outputs)
    assert _contains(info.outputs, "result.csv"), out_raws
    assert _contains(info.outputs, "fig.png"), out_raws


def test_argparse_input_not_in_outputs():
    # input.csv is read, not written -- it must not leak into outputs.
    info = analyze(FIXTURES / "argparse_tool.py")
    assert not _contains(info.outputs, "input.csv"), _raws(info.outputs)


# --------------------------------------------------------------------------- #
# cell_script.py -- a "# %%" notebook-style cell script
# --------------------------------------------------------------------------- #
def test_cell_script_badge():
    info = analyze(FIXTURES / "cell_script.py")
    assert info.badge == "cell-script"


def test_cell_script_no_arg_style():
    info = analyze(FIXTURES / "cell_script.py")
    assert info.args.style == "none"


def test_cell_script_captures_constant():
    info = analyze(FIXTURES / "cell_script.py")
    assert info.constants.get("RUN_NAME") == "0000000074"


def test_cell_script_fstring_input_resolved_via_constant():
    info = analyze(FIXTURES / "cell_script.py")
    # The f-string "./runs/{RUN_NAME}/" should render with the known constant.
    assert _contains(info.inputs, "0000000074"), _raws(info.inputs)


def test_cell_script_input_func_mentions_from_disk():
    info = analyze(FIXTURES / "cell_script.py")
    matching = [
        ref for ref in info.inputs if "0000000074" in ref.raw
    ]
    assert matching, _raws(info.inputs)
    assert any("from_disk" in ref.func for ref in matching), [
        ref.func for ref in matching
    ]


def test_cell_script_no_main():
    info = analyze(FIXTURES / "cell_script.py")
    assert info.has_main is False


# --------------------------------------------------------------------------- #
# sysargv_tool.py -- argument access through sys.argv
# --------------------------------------------------------------------------- #
def test_sysargv_style():
    info = analyze(FIXTURES / "sysargv_tool.py")
    assert info.args.style == "sys.argv"


def test_sysargv_badge_is_not_cli():
    # sys.argv is not argparse/click/typer, so it must not earn the "cli" badge.
    info = analyze(FIXTURES / "sysargv_tool.py")
    assert info.badge != "cli"


# --------------------------------------------------------------------------- #
# plain_util.py -- a plain utility module
# --------------------------------------------------------------------------- #
def test_plain_util_badge():
    info = analyze(FIXTURES / "plain_util.py")
    assert info.badge == "script"


def test_plain_util_input():
    info = analyze(FIXTURES / "plain_util.py")
    assert _contains(info.inputs, "a.csv"), _raws(info.inputs)


def test_plain_util_outputs():
    info = analyze(FIXTURES / "plain_util.py")
    out_raws = _raws(info.outputs)
    assert _contains(info.outputs, "notes.txt"), out_raws  # open(..., "w")
    assert _contains(info.outputs, "out.json"), out_raws  # Path.write_text


def test_plain_util_no_arg_style():
    info = analyze(FIXTURES / "plain_util.py")
    assert info.args.style == "none"


# --------------------------------------------------------------------------- #
# broken.py -- a deliberate SyntaxError
# --------------------------------------------------------------------------- #
def test_broken_badge_is_error():
    info = analyze(FIXTURES / "broken.py")
    assert info.badge == "error"


def test_broken_sets_parse_error():
    info = analyze(FIXTURES / "broken.py")
    assert info.parse_error is not None
    assert info.parse_error != ""


def test_broken_does_not_raise():
    # analyze() must swallow the SyntaxError and report it, never raise.
    try:
        info = analyze(FIXTURES / "broken.py")
    except SyntaxError:  # pragma: no cover - this is the failure we guard against
        pytest.fail("analyze() raised SyntaxError instead of reporting it")
    assert isinstance(info, ScriptInfo)


# --------------------------------------------------------------------------- #
# var_paths.py -- path resolution through local variable assignments
# --------------------------------------------------------------------------- #
def test_var_paths_fstring_var_resolves_to_csv_output():
    # stations_out = f"{run_id}_{count}_stations.csv"; df.to_csv(stations_out)
    info = analyze(FIXTURES / "var_paths.py")
    assert _contains(info.outputs, "stations.csv"), _raws(info.outputs)


def test_var_paths_path_join_var_resolves_to_csv_input():
    # segment_file_name = Path(run_folder) / "model_segment.csv"; read_csv(...)
    info = analyze(FIXTURES / "var_paths.py")
    assert _contains(info.inputs, "model_segment.csv"), _raws(info.inputs)


def test_var_paths_str_wrapped_var_resolves_to_msh_output():
    # output_file = Path(f"{run_id}_mesh.msh"); gmsh.write(str(output_file))
    info = analyze(FIXTURES / "var_paths.py")
    assert _contains(info.outputs, ".msh"), _raws(info.outputs)


def test_var_paths_config_save_not_an_output():
    # config.save("config.yaml") must NOT be misclassified as a figure save.
    info = analyze(FIXTURES / "var_paths.py")
    assert not _contains(info.outputs, "config.yaml"), _raws(info.outputs)


def test_var_paths_json_named_load_not_an_input():
    # my_json.load(...) must NOT be misclassified as json.load input.
    info = analyze(FIXTURES / "var_paths.py")
    assert not _contains(info.inputs, "not_a_file.json"), _raws(info.inputs)


# --------------------------------------------------------------------------- #
# render_path -- format-spec / conversion handling
# --------------------------------------------------------------------------- #
def test_render_fstring_format_spec_marks_unresolved():
    import ast

    from belfry.analyzer import render_path

    node = ast.parse('f"out_{N:04d}.csv"', mode="eval").body
    raw, resolved, kind = render_path(node, {"N": "42"})
    # Cannot reproduce the zero-padded literal from the constant alone.
    assert resolved is False, (raw, resolved, kind)
    assert "04d" in raw, raw


def test_render_fstring_plain_constant_resolves():
    import ast

    from belfry.analyzer import render_path

    node = ast.parse('f"out_{N}.csv"', mode="eval").body
    raw, resolved, _ = render_path(node, {"N": "42"})
    assert resolved is True
    assert raw == "out_42.csv"


def test_string_join_not_treated_as_path_join():
    import ast

    from belfry.analyzer import render_path

    node = ast.parse('",".join(parts)', mode="eval").body
    raw, resolved, _ = render_path(node, {})
    # A plain string join must not render as a filesystem join.
    assert resolved is False
    assert raw != "parts"


# --------------------------------------------------------------------------- #
# Caching -- a second analyze() of an unchanged file returns the same result
# --------------------------------------------------------------------------- #
def test_analyze_is_cached_for_unchanged_file():
    path = FIXTURES / "argparse_tool.py"
    first = analyze(path)
    second = analyze(path)
    # Either the exact same object (identity cache) or an equal value.
    assert first is second or first == second
