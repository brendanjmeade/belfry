# rab

**Ring a Bell** -- a Textual TUI for rediscovering what your Python scripts do.

Run `rab` in any folder and it lists the `.py` files it finds, each with its
last-modified date and git provenance (the short hash, date, and subject of the
last commit that touched it). Select a file and rab shows you, at a glance:

- the **CLI arguments** it accepts (argparse / click / typer / `sys.argv`),
- the **hardcoded input filenames** it reads,
- the **output files** it writes,
- its **docstring and leading comments**,
- a **script-type badge** (cell-script / cli / script / error), and
- a **syntax-highlighted source preview**.

It's for that moment when you stare at `analyze_v2_final.py` and have no idea
what it does anymore. rab rings a bell.

## Install

```bash
pip install -e .
```

## Usage

```bash
rab [PATH] [--no-recurse]
```

- `PATH` -- directory to scan (defaults to the current directory).
- `--no-recurse` -- only scan the top level instead of descending into subdirectories.

## Key bindings

| Key | Action |
| --- | --- |
| `j` / `k`, arrows | navigate the file list (or scroll the details when the lower pane is focused) |
| `J` / `K` | jump focus to the lower / upper pane |
| `h` / `l` | switch tabs in the lower pane (Summary / Source) |
| `/` | filter files |
| `r` | toggle recursion / rescan |
| `enter` | open the selected file in `$EDITOR` |
| `q` | quit |
