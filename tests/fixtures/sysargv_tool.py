"""A bare-bones tool that pulls arguments straight from sys.argv."""

import sys


def run() -> None:
    source = sys.argv[1]
    destination = sys.argv[2]
    print(source, destination)


run()
