"""A small CLI tool that reads a CSV and writes a result CSV plus a figure."""

import argparse

import matplotlib.pyplot as plt
import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(description="Process a run directory.")
    parser.add_argument("run_dir", help="Directory of the run to process.")
    parser.add_argument(
        "-o", "--output", default="result.csv", help="Where to write results."
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Emit extra diagnostics."
    )
    args = parser.parse_args()

    df = pd.read_csv("input.csv")
    df.to_csv("result.csv")
    plt.savefig("fig.png")


if __name__ == "__main__":
    main()
