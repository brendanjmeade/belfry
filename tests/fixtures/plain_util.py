"""A plain utility module with no command-line arguments."""

from pathlib import Path

import pandas as pd

df = pd.read_csv("a.csv")
open("notes.txt", "w").write("x")
Path("out.json").write_text("{}")
