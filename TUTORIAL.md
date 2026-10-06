# DataHub tutorial

This walks you through using DataHub from scratch: start the server, version a dataset, see deduplication happen, work with a teammate's copy, and search your data by its stats. Allow about 20 minutes.

Commands are for **Windows PowerShell**, run from the **repository root** unless a step says otherwise. Everything runs inside Docker, so you don't need Python installed.

> New to the ideas (Merkle trees, chunks, commits)? Read [How it works](README.md#1-how-it-works-step-by-step) first; it builds them up step by step.

## Contents
1. [Start the server](#1-start-the-server)
2. [Create a dataset folder and a `datahub` shortcut](#2-create-a-dataset-folder-and-a-datahub-shortcut)
3. [Version your data: init and push](#3-version-your-data-init-and-push)
4. [Change a big file and watch the deduplication](#4-change-a-big-file-and-watch-the-deduplication)
5. [Look at the history](#5-look-at-the-history)
6. [Search your data by its stats](#6-search-your-data-by-its-stats)
7. [Work with a teammate: pull, and what happens on conflicts](#7-work-with-a-teammate-pull-and-what-happens-on-conflicts)
8. [Look inside the database](#8-look-inside-the-database)
9. [Command reference](#9-command-reference)
10. [Troubleshooting](#10-troubleshooting)
11. [Clean up](#11-clean-up)

---

## 1. Start the server

You need **Docker Desktop** running.

**1.1 Database credentials (once).** Create `credentials.json` in the repository root (it is git-ignored; never commit it):

```json
{
  "database": {
    "user": "user",
    "password": "password",
    "name": "datahub",
    "host": "db",
    "port": 5432
  }
}
```

**1.2 Start the containers** (PostgreSQL + the `dev-env` container that runs DataHub):

```powershell
./sync_credentials_env.ps1        # writes .env from credentials.json
docker compose up -d --build
```

**1.3 Start the API.** Open a terminal you can leave running ("Terminal A"):

```powershell
docker compose exec dev-env uvicorn api.server:app --host 0.0.0.0 --port 8000
```

Leave it running. Everything else happens in a second terminal ("Terminal B"), also at the repository root.

## 2. Create a dataset folder and a `datahub` shortcut

DataHub versions a folder. The folder has to be inside the repository, because that is what the container can see (it is mounted at `/app`).

```powershell
mkdir tutorial_data
function datahub { docker compose exec -w /app/tutorial_data dev-env python -m cli.main @args }
$hub = "http://localhost:8000"
```

- `datahub ...` now runs the DataHub CLI inside the container, in `tutorial_data`.
- `$hub` is the server address. The CLI talks to the server **only** over HTTP, exactly as it would from a laptop.

Put some data in it: a metrics file and a small CSV.

```powershell
Set-Content tutorial_data/metrics.json '{"accuracy": 0.91, "loss": 0.12}'
Set-Content tutorial_data/labels.csv "id,label`n1,cat`n2,dog`n3,cat"
```

## 3. Version your data: init and push

```powershell
datahub init
datahub push $hub -m "First version"
```

```text
Initialized DataHub
Scanning and chunking files...
Files:  2 new / 2 total
Chunks: 2 uploaded / 2 total
Bytes:  62 uploaded / 62 total
Commit: 549f…
```

What happened:
1. Every file was split into chunks (small files are a single chunk) and hashed.
2. The CLI asked the server which files and chunks it already has (none yet) and uploaded the rest.
3. The **server** built the Merkle tree of the folder and recorded commit `549f…`, whose parent is "none".
4. `tutorial_data/.datahub/HEAD` now remembers that commit. Your next push will name it as its parent.

Push again without changing anything:

```powershell
datahub push $hub -m "Nothing changed"
```

```text
Files:  0 new / 2 total
Chunks: 0 uploaded / 2 total
Bytes:  0 uploaded / 62 total
```

Nothing is uploaded: the server already knows both files by their whole-file hash.

## 4. Change a big file and watch the deduplication

Deduplication matters for big files. Create a ~55 MB CSV (2 million rows). This pipes a short Python script into the container:

```powershell
@'
import random
random.seed(1)
with open("big.csv", "w") as f:
    f.write("id,feature_a,feature_b,label\n")
    for i in range(2_000_000):
        f.write(f"{i},{random.random():.6f},{random.random():.6f},{random.randint(0, 1)}\n")
'@ | docker compose exec -T -w /app/tutorial_data dev-env python -
datahub push $hub -m "Add big dataset"
```

```text
Files:  1 new / 3 total
Chunks: 5 uploaded / 7 total
Bytes:  54888919 uploaded / 54888981 total
```

`big.csv` was split into 5 content-defined chunks (16 MiB on average; the exact count depends on the data). The other 2 chunks are the two small files, already stored.

Now **insert a row near the top**, the edit that defeats fixed-size chunking:

```powershell
@'
lines = open("big.csv").readlines()
lines.insert(10, "999999,0.5,0.5,1\n")
open("big.csv", "w").writelines(lines)
'@ | docker compose exec -T -w /app/tutorial_data dev-env python -
datahub push $hub -m "Insert one row near the top"
```

```text
Files:  1 new / 3 total
Chunks: 1 uploaded / 7 total
Bytes:  12953026 uploaded / 54888998 total
```

Only the chunk containing the new row was uploaded (13 MB of 55 MB); every later chunk kept its hash because content-defined boundaries move with the content ([why content-defined chunking](README.md#step-8-why-content-defined-chunking)). With fixed-size chunks every chunk after the insert would have changed. The exact bytes depend on where the boundaries fall: one chunk is 4–64 MiB.

## 5. Look at the history

```powershell
datahub log $hub
```

```text
commit c9d8…
Author:   root
Date:     2026-10-06T08:22:13Z

    Insert one row near the top

commit d250…
...
```

Newest first. Each commit points to its parent, all the way back to "First version". (`Author` is your login name inside the container; set it with `datahub push $hub -m "..." --author "Your Name"`.)

## 6. Search your data by its stats

On every push the CLI extracted stats from new files: row counts and column types for CSV/Parquet, and the values of flat JSON files like `metrics.json`. Search them:

```powershell
datahub query $hub "accuracy > 0.9"
datahub query $hub "row_count > 1000000"
```

```text
metrics.json | {'format': 'json', 'accuracy': 0.91, 'loss': 0.12}
big.csv | {'row_count': 2000001, 'schema': {...}, 'columns': [...], 'format': 'csv'}   (shortened)
```

Queries search the files of the **latest** commit. Make the model worse and query again:

```powershell
Set-Content tutorial_data/metrics.json '{"accuracy": 0.85, "loss": 0.20}'
datahub push $hub -m "Retrained: worse model"
datahub query $hub "accuracy > 0.9"
```

```text
No matching files in the latest commit.
```

Operators: `>`, `<`, `>=`, `<=`, `==`, on numbers.

## 7. Work with a teammate: pull, and what happens on conflicts

Simulate a teammate with a second folder and its own shortcut:

```powershell
mkdir tutorial_teammate
function teammate { docker compose exec -w /app/tutorial_teammate dev-env python -m cli.main @args }
teammate init
teammate pull $hub
```

```text
Files:  3 updated, 0 removed
Bytes:  54888998 downloaded
Commit: 6a0b…
```

The teammate now has an identical copy: every chunk and every file hash was verified during the download.

**The teammate changes something and pushes:**

```powershell
Set-Content tutorial_teammate/labels.csv "id,label`n1,cat`n2,dog`n3,bird"
teammate push $hub -m "Fix label 3"
```

**You push without pulling first:**

```powershell
Set-Content tutorial_data/notes.txt "my experiment notes"
datahub push $hub -m "Add notes"
```

```text
Error: Remote has newer commits than this copy; pull first.
```

DataHub refuses because `main` moved since your last push. Nothing is overwritten. **Pull, then push:**

```powershell
datahub pull $hub
datahub push $hub -m "Add notes"
```

`pull` brought in the teammate's `labels.csv` and **kept your new `notes.txt`**, because the teammate never touched it. Your push then goes on top of the teammate's commit.

**A real conflict** is when you *both* change the same file:

```powershell
teammate pull $hub
Set-Content tutorial_teammate/metrics.json '{"accuracy": 0.95}'
teammate push $hub -m "Teammate's new model"
Set-Content tutorial_data/metrics.json '{"accuracy": 0.97}'
datahub pull $hub
```

```text
Error: Local changes would be overwritten (push them, or pull --force):
  metrics.json
```

Nothing was touched. Your choices:
- keep the teammate's version: `datahub pull $hub --force` (your local `metrics.json` is replaced);
- keep yours: copy your `metrics.json` somewhere, `pull --force`, copy it back, then `push`.

## 8. Look inside the database

List the commits and see how files are stored as chunk lists:

```powershell
docker compose exec db psql -U user -d datahub -c "SELECT commit_hash, parent_hash, message FROM commit ORDER BY created_at;"
docker compose exec db psql -U user -d datahub -c "SELECT f.file_hash, f.size_bytes, count(*) AS chunks FROM file f JOIN file_chunk fc ON fc.file_hash = f.file_hash GROUP BY f.file_hash, f.size_bytes ORDER BY f.size_bytes DESC;"
```

The two versions of `big.csv` appear as two files that **share most of their chunks**. Compare the storage actually used with the total size of all file versions:

```powershell
docker compose exec db psql -U user -d datahub -c "SELECT (SELECT sum(size_bytes) FROM file) AS all_file_versions, (SELECT sum(size_bytes) FROM chunk) AS actually_stored;"
```

```text
 all_file_versions | actually_stored
-------------------+-----------------
         109778021 |        67842111
```

Every version of every file adds up to 110 MB, but only 68 MB is stored: the second 55 MB version of `big.csv` cost one 13 MB chunk.

Or browse every table live in the dashboard (refreshes every 5 seconds):

```powershell
cd live_demo_workspace
.\run_db_dashboard.ps1            # then open http://localhost:8090
```

## 9. Command reference

Run `python -m cli.main <command> --help` for full help.

| Command | What it does |
|---|---|
| `init` | Makes the current folder a DataHub working copy (creates `.datahub/`). |
| `push <server> [-m MSG] [--author NAME]` | Uploads only what the server lacks and records a new commit on top of `.datahub/HEAD`. Fails with "pull first" if someone pushed since. |
| `pull <server> [--force]` | Brings the folder to the newest commit. Keeps your changes to files the remote didn't change; stops on real conflicts unless `--force`. |
| `log <server>` | History of `main`, newest first. |
| `query <server> "<metric> <op> <number>"` | Files in the latest commit whose stats match. |

Folders that are never versioned: `.datahub`, `.git`, `__pycache__`, `venv`, `.venv`.

## 10. Troubleshooting

| Message | Meaning and fix |
|---|---|
| `Remote has newer commits than this copy; pull first.` | Someone pushed since your last push or pull. Run `pull`, then `push`. |
| `Local changes would be overwritten ... : <files>` | You and the remote both changed those files. See [section 7](#7-work-with-a-teammate-pull-and-what-happens-on-conflicts). |
| `Not a DataHub repository; run 'init' first.` | Run `init` in that folder (or you're in the wrong folder). |
| `Remote has no commits yet.` | Nothing to pull: push something first. |
| `Server error 400: ...` | The server rejected the input; the message says why (e.g. an invalid query like `accuracy 0.9`). |
| `Connection refused` / `Max retries exceeded` | The API isn't running: start it (section 1.3). |
| `fastcdc compiled extension not loaded` | You're running outside Docker on an unsupported Python. Use Docker, or a Python 3.11 venv (see [DEVELOPMENT.md](DEVELOPMENT.md)). |
| `docker compose exec` says *service "dev-env" is not running* | Run `docker compose up -d` at the repository root. |

## 11. Clean up

```powershell
Remove-Item -Recurse -Force tutorial_data, tutorial_teammate
docker compose down -v      # stops everything and deletes the database (all commits)
```

Use `docker compose down` (without `-v`) to stop the containers but keep the data.
