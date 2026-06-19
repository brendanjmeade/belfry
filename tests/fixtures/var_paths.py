"""Exercises path resolution through local variable assignments."""

import gmsh
import pandas as pd
from pathlib import Path


def write_outputs(run_id, count, run_folder):
    # f-string assigned to a variable, then used as a to_csv target.
    stations_out = f"{run_id}_{count}_stations.csv"
    df = pd.DataFrame()
    df.to_csv(stations_out, index=False)

    # Path()/literal assigned to a variable, then used as a read_csv source.
    segment_file_name = Path(run_folder) / "model_segment.csv"
    segments = pd.read_csv(segment_file_name)

    # str(var) wrapping a variable whose value is an f-string ending in .msh.
    output_file = Path(f"{run_id}_mesh.msh")
    gmsh.write(str(output_file))

    # config.save(...) must NOT be treated as a figure/array save.
    config.save("config.yaml")

    # my_json.load(...) must NOT be treated as a json.load input.
    my_json.load("not_a_file.json")

    return segments
