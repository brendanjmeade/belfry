# %%
# Load a completed celeri run and produce some figures interactively.

import celeri

RUN_NAME = "0000000074"

estimation = celeri.Estimation.from_disk(f"./runs/{RUN_NAME}/")
