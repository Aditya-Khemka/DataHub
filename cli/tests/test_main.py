import os
import random

import pytest
from click.testing import CliRunner

import cli.utils.api as api
from api.tests.test_server import client  # noqa: F401  (fixture: fresh DB, chunk folder, tiny chunk sizes)
from cli.main import cli
from cli.utils.file_scanner import scan_files

REMOTE = "http://testserver"

# The TestClient is httpx-based and prefers content= for raw bytes; the real CLI uses requests, which takes data=
pytestmark = pytest.mark.filterwarnings("ignore:Use 'content=<...>':DeprecationWarning")


def run(*args):
    result = CliRunner().invoke(cli, list(args))
    return result.exit_code, result.output


def test_push_flow_dedup_and_history(client, monkeypatch, tmp_path):
    """init -> push -> edit one file -> push: only the change is uploaded, history is chained."""
    monkeypatch.setattr(api, "http", client)
    repo_dir = tmp_path / "repo"  # tmp_path itself holds the server's chunk folder
    repo_dir.mkdir()
    monkeypatch.chdir(repo_dir)
    data = random.Random(1).randbytes(20_000)
    os.makedirs("data")
    with open("data/train.csv", "wb") as f:
        f.write(data)
    with open("notes.txt", "w") as f:
        f.write("v1")

    assert run("init")[0] == 0
    code, out = run("push", REMOTE, "-m", "v1", "--author", "alice")
    assert code == 0, out
    assert "Files:  2 new / 2 total" in out
    with open(".datahub/HEAD") as f:
        first = f.read()

    with open("data/train.csv", "wb") as f:
        f.write(data[:500] + b"INSERTED" + data[500:])
    code, out = run("push", REMOTE, "-m", "v2", "--author", "alice")
    assert code == 0, out
    assert "Files:  1 new / 2 total" in out              # notes.txt unchanged: skipped entirely
    uploaded = int(out.split("Chunks: ")[1].split(" uploaded")[0])
    assert 0 < uploaded <= 3                            # only chunks around the insert

    code, out = run("log", REMOTE)
    assert out.index("v2") < out.index("v1") and first in out


def test_stale_head_is_rejected(client, monkeypatch, tmp_path):
    """A second copy that hasn't pulled must not overwrite the first copy's push."""
    monkeypatch.setattr(api, "http", client)
    for copy in ("a", "b"):
        os.makedirs(tmp_path / copy)
        (tmp_path / copy / "f.txt").write_text(copy)
        monkeypatch.chdir(tmp_path / copy)
        run("init")

    monkeypatch.chdir(tmp_path / "a")
    assert run("push", REMOTE)[0] == 0
    monkeypatch.chdir(tmp_path / "b")
    code, out = run("push", REMOTE)
    assert code == 1 and "pull first" in out
    assert not os.path.exists(".datahub/HEAD")          # failed push leaves HEAD untouched


def test_push_requires_init(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    code, out = run("push", REMOTE)
    assert code == 1 and "run 'init' first" in out


def test_scanner_skips_tool_folders_and_uses_posix_paths(tmp_path):
    for p in ("a/b/c.csv", "top.txt", ".git/HEAD", "venv/x.py", "a/__pycache__/m.pyc", ".datahub/HEAD"):
        os.makedirs(tmp_path / os.path.dirname(p), exist_ok=True)
        (tmp_path / p).write_text("x")
    assert scan_files(str(tmp_path)) == ["a/b/c.csv", "top.txt"]
