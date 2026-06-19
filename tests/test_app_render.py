"""App-level regression tests for detail rendering.

Rendering must never crash on file references whose call expressions contain
markup-significant characters ([], (), commas). These previously broke
Textual's markup parser and crashed the whole app.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from textual.widgets import DataTable, Static  # noqa: E402

from belfry.app import BelfryApp  # noqa: E402


def _render_first_file(directory: Path) -> tuple[int, str, str]:
    async def run() -> tuple[int, str, str]:
        app = BelfryApp(directory, recurse=False)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#files", DataTable)
            table.move_cursor(row=0)
            await pilot.pause()
            await pilot.press("l")  # also exercise the Source tab
            await pilot.pause()
            outputs = str(app.query_one("#sec_outputs", Static).render())
            inputs = str(app.query_one("#sec_inputs", Static).render())
            return table.row_count, outputs, inputs

    return asyncio.run(run())


def test_renders_filerefs_with_markup_significant_chars(tmp_path):
    # An output whose unparsed call expression contains [], (), and commas and
    # is long enough to be truncated with "..." — the exact shape that crashed.
    (tmp_path / "tricky.py").write_text(
        "import pandas as pd\n"
        "import numpy as np\n"
        "pd.DataFrame(dict(lon=lon_cen[fx], lat=lat_cen[fy], val=arr[idx],\n"
        "                  more=other[jj], extra=zzz[kk])).to_csv('audit.csv')\n"
        "data = pd.read_csv(paths[0])\n"
    )
    rows, outputs, inputs = _render_first_file(tmp_path)
    assert rows == 1
    # Did not crash, and the panels rendered with the expected literal content.
    assert "OUTPUTS" in outputs and "audit.csv" in outputs
    assert "INPUTS" in inputs
