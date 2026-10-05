import click
import getpass
import os
import json
from datetime import datetime, timezone

from core import chunker
from core.objects import format_time
from cli.utils.file_scanner import scan_files
from cli.utils.api import existing_files, missing_chunks, upload_chunk, register_files, create_commit, get_log, query_metadata

HEAD_PATH = os.path.join(".datahub", "HEAD")


@click.group()
def cli():
    pass


@cli.command()
def init():
    """Initialize .datahub directory locally"""

    if os.path.exists(".datahub"):
        click.echo("Already initialized")
        return

    os.mkdir(".datahub")

    with open(".datahub/config.json", "w") as f:
        json.dump({"initialized": True}, f)

    click.echo("Initialized DataHub")


@cli.command()
@click.argument("remote_url")
@click.option("-m", "--message", default="Auto-commit", help="Commit message")
@click.option("--author", default=getpass.getuser, show_default="your login name", help="Commit author")
def push(remote_url, message, author):
    """Uploads only the chunks the server lacks, then commits on top of this copy's HEAD."""
    if not os.path.isdir(".datahub"):
        raise click.ClickException("Not a DataHub repository; run 'init' first.")
    remote_url = remote_url.rstrip("/")

    click.echo("Scanning and chunking files...")
    paths = scan_files()
    # Read sizes from the module (not defaults) so client and server always agree
    chunked = {p: chunker.chunk_file(p, chunker.MIN_SIZE, chunker.AVG_SIZE, chunker.MAX_SIZE) for p in paths}

    # Whole-file check first: unchanged files need no chunk work at all
    known = existing_files(remote_url, sorted({fh for fh, _, _ in chunked.values()}))
    new_files = {}  # file_hash -> (path, size, chunks); identical files are sent once
    for path, (file_hash, size, chunks) in chunked.items():
        if file_hash not in known:
            new_files.setdefault(file_hash, (path, size, chunks))

    # Where to read each chunk from (first occurrence wins; same hash => same bytes)
    sources = {}
    for path, _, chunks in new_files.values():
        for h, offset, length in chunks:
            sources.setdefault(h, (path, offset, length))

    to_upload = missing_chunks(remote_url, list(sources))
    uploaded_bytes = 0
    for h in to_upload:
        path, offset, length = sources[h]
        with open(path, "rb") as f:
            f.seek(offset)
            upload_chunk(remote_url, h, f.read(length))
        uploaded_bytes += length

    if new_files:
        register_files(remote_url, [
            {"file_hash": fh, "size": size, "chunker": chunker.CHUNKER,
             "chunks": [{"hash": h, "length": n} for h, _, n in chunks]}
            for fh, (_, size, chunks) in new_files.items()
        ])

    parent = None
    if os.path.exists(HEAD_PATH):
        with open(HEAD_PATH) as f:
            parent = f.read().strip() or None

    result = create_commit(remote_url, {
        "parent_hash": parent,
        "files": {p: fh for p, (fh, _, _) in chunked.items()},
        "author": author,
        "message": message,
        "time": format_time(datetime.now(timezone.utc)),
    })
    with open(HEAD_PATH, "w") as f:
        f.write(result["commit_hash"])

    total_chunks = sum(len(c) for _, _, c in chunked.values())
    total_bytes = sum(size for _, size, _ in chunked.values())
    click.echo(f"Files:  {len(new_files)} new / {len(paths)} total")
    click.echo(f"Chunks: {len(to_upload)} uploaded / {total_chunks} total")
    click.echo(f"Bytes:  {uploaded_bytes} uploaded / {total_bytes} total")
    click.echo(f"Commit: {result['commit_hash']}")

@cli.command()
@click.argument("remote_url")
def log(remote_url):
    """Shows the commit history from the server."""
    try:
        response = get_log(remote_url)
        history = response.get("history", [])
        if not history:
            click.echo("No commits found.")
            return
        for commit in history:
            click.echo(f"commit {commit['commit_hash']}")
            click.echo(f"Author:   {commit['author']}")
            click.echo(f"Date:     {commit['created_at']}")
            click.echo(f"\n    {commit['message']}\n")
    except Exception as e:
        click.echo(f"Error fetching log: {str(e)}")

@cli.command()
@click.argument("remote_url")
@click.argument("query_str")
def query(remote_url, query_str):
    """Query metadata using DSL 'metric operator value'."""
    try:
        response = query_metadata(remote_url, query_str)
        results = response.get("results", [])
        if not results:
            click.echo("No matching metadata found.")
            return
        for res in results:
            click.echo(f"Object: {res['target_hash']} | Metrics: {res['stats']}")
    except Exception as e:
        click.echo(f"Error executing query: {str(e)}")

if __name__ == '__main__':
    cli()
    