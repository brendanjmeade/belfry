# belfry

A [Textual](https://github.com/Textualize/textual) TUI for
rediscovering what your Python scripts actually do.

![belfry in action](https://raw.githubusercontent.com/brendanjmeade/belfry/main/docs/screenshot.png)

You know the folder: dozens of `.py` files accumulated over months — mainline
analyses, throwaway one-offs, and half-remembered experiments. Which ones take
arguments? What files do they read? What do they write? `belfry` answers those
questions at a glance, without you opening a single file.


## What it shows

Run `belfry` in any folder. It lists every `.py` file (recursively, honoring
`.gitignore`) with a script-type badge, last-modified date, and git provenance.
Select a file and belfry shows you:

- the **CLI arguments** it accepts — parsed from `argparse` / `click` / `typer`,
  or `sys.argv` indexing, even when there's no `--help` text to be found;
- the **hardcoded input filenames** it reads (`pd.read_csv`, `open`,
  `xr.open_dataset`, `np.load`, …);
- the **output files** it writes (`.to_csv`, `plt.savefig`, `json.dump`,
  `gmsh.write`, …);
- its **docstring and leading comments**;
- a **script-type badge** — `cli`, `cell-script` (Jupyter `# %%`), `script`, or
  `error`;
- the module-level constants ("knobs") for scripts driven by hardcoded values
  instead of CLI flags;
- a **syntax-highlighted source preview**.

It even resolves f-strings and `Path(...)` expressions, so
`f"./runs/{RUN_NAME}/"` shows up as the real path and unresolved values are
clearly flagged.

## How it works

belfry reads each script with Python's `ast` module and **never executes it** —
safe to point at code you don't trust or barely remember. Analysis and git
lookups happen lazily as you move through the list and are cached, so it stays
responsive in large trees. Git provenance falls back cleanly to filesystem
mtime when a file is untracked or you're not in a git repo.

## Install

```bash
```
pip install belfry
```

```
Requires Python 3.10+ (the only runtime dependency is `textual`).

## Usage

```bash
belfry [PATH] [--no-recurse]
```

- `PATH` — directory to scan (defaults to the current directory).
- `--no-recurse` — only scan the top level instead of descending into subdirectories.

You can also run it as a module: `python -m belfry`.

## Key bindings

| Key | Action |
| --- | --- |
| `j` / `k`, arrows | navigate the file list (or scroll the details when the lower pane is focused) |
| `J` / `K` | jump focus to the lower / upper pane |
| `h` / `l` | switch tabs in the lower pane (Summary / Source) |
| `/` | filter files by name |
| `r` | toggle recursion / rescan |
| `enter` | open the selected file in `$EDITOR` |
| `q` | quit |

## Development

```bash
pip install -e ".[dev]"
pytest
```

The static analyzer (`src/belfry/analyzer.py`) is covered by a focused test
suite with fixtures for each pattern it handles.
