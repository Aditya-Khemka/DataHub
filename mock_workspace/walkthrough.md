# DataHub simulation walkthrough

`simulate_datahub.py` runs the whole system end to end in one command: it starts the API, generates a small ML workspace, pushes it, edits it, pushes again, queries it and prints the history.

## Run it

It needs an **empty database**: it starts from a fresh `.datahub/` (no `HEAD`), so if `main` already has commits its first push would be correctly refused with "pull first". The script checks this and tells you to reset.

```powershell
docker compose down -v            # fresh database (deletes all commits)
docker compose up -d --build
docker compose run --rm dev-env python mock_workspace/simulate_datahub.py
```

The script regenerates `mock_workspace/train_data.csv`, `model_rf.py` and `metrics.json` on each run.

## What each step shows

| Step | What happens | What to look at |
|---|---|---|
| 1 | Starts the API inside the container | |
| 2 | `download_sample_dataset.py` writes a 15,000-row CSV, a model script and `metrics.json` | |
| 3–4 | `init`, then the first `push`: every file is new | `Files: 6 new`, `Chunks: 6 uploaded` |
| Validation | Counts chunk files in `blobs/<2 hex>/<sha256>` | `6 chunk files, 0.33 MB on disk` |
| 5 | Edits `metrics.json` (accuracy 0.94) and appends a comment to `model_rf.py` | |
| 6 | Second `push`: the unchanged 0.33 MB CSV is skipped by its whole-file hash; only the two edited small files are uploaded | `Files: 2 new / 6 total`, `Bytes: 435 uploaded` |
| Validation | Chunk count again | `8 chunk files`: 2 new chunks, the CSV not stored again |
| 7 | `query "row_count > 1000"`: stats were extracted by the CLI during push | `train_data.csv \| {'row_count': 15000, ...}` |
| 8 | `log`: the commit chain, newest first | two commits, the second pointing at the first |

## Sample output

```text
[Step 4] Pushing Initial Baseline Models & Heavy Datasets...
Files:  6 new / 6 total
Chunks: 6 uploaded / 6 total
Bytes:  329003 uploaded / 329003 total

[Validation] Chunk store footprint:
6 chunk files, 0.33 MB on disk

[Step 6] Pushing Second Commit (v2.0 Tuned)...
Files:  2 new / 6 total
Chunks: 2 uploaded / 6 total
Bytes:  435 uploaded / 329067 total

[Validation] The CSV was not stored again: only the edited files' chunks were added.
8 chunk files, 0.33 MB on disk

[Step 7] Querying Extracted Metadata Layer using DSL Parser...
train_data.csv | {'row_count': 15000, 'schema': {'id': 'int64', 'feature_A': 'float64', 'feature_B': 'float64', 'target': 'int64'}, ...}

[Step 8] Resolving Recursive Merkle DAG PostgreSQL Trace...
commit fc66a87e...
    Tuned hyperparameters (Accuracy -> 94%)
commit 42f2127c...
    Initial Baseline Dataset & RandomForest Model
```

All files here are under 4 MiB, so each is a single chunk and deduplicates as a whole file. To see chunk-level deduplication inside one big file, follow [TUTORIAL.md section 4](../TUTORIAL.md#4-change-a-big-file-and-watch-the-deduplication).
