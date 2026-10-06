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


# ---- pull ----

import cli.main as main


def _copy(tmp_path, monkeypatch, name, files=None):
    """A fresh working copy (separate folder + .datahub) with optional files."""
    d = tmp_path / name
    d.mkdir()
    for p, text in (files or {}).items():
        (d / p).parent.mkdir(parents=True, exist_ok=True)
        (d / p).write_text(text)
    monkeypatch.chdir(d)
    run("init")
    return d


def test_pull_roundtrip_two_copies(client, monkeypatch, tmp_path):
    """A pushes; B pulls an identical copy, edits and pushes; A must pull before pushing again."""
    monkeypatch.setattr(api, "http", client)
    big = random.Random(2).randbytes(5_000).hex()
    a = _copy(tmp_path, monkeypatch, "a", {"data/x.csv": big, "old.txt": "bye"})
    assert run("push", REMOTE, "-m", "a1")[0] == 0

    b = _copy(tmp_path, monkeypatch, "b")
    code, out = run("pull", REMOTE)
    assert code == 0, out
    assert (b / "data/x.csv").read_text() == big and (b / "old.txt").read_text() == "bye"
    assert (b / ".datahub/HEAD").read_text() == (a / ".datahub/HEAD").read_text()
    assert run("pull", REMOTE)[1].strip() == "Already up to date."

    (b / "data/x.csv").write_text(big + "\nnew row")
    os.remove(b / "old.txt")
    assert run("push", REMOTE, "-m", "b1")[0] == 0

    monkeypatch.chdir(a)
    code, out = run("pull", REMOTE)
    assert code == 0, out
    assert (a / "data/x.csv").read_text() == big + "\nnew row"
    assert not (a / "old.txt").exists()                  # deletion propagated
    assert not [p for p in os.listdir(a / "data") if p.endswith(".datahub-tmp")]


def test_pull_protects_local_changes(client, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "http", client)
    a = _copy(tmp_path, monkeypatch, "a", {"f.txt": "v1"})
    run("push", REMOTE)
    b = _copy(tmp_path, monkeypatch, "b")
    run("pull", REMOTE)
    (b / "f.txt").write_text("v2 from b")
    run("push", REMOTE)

    monkeypatch.chdir(a)
    (a / "f.txt").write_text("unpushed work in a")
    code, out = run("pull", REMOTE)
    assert code == 1 and "f.txt" in out and "pull --force" in out
    assert (a / "f.txt").read_text() == "unpushed work in a"   # nothing touched
    assert run("pull", REMOTE, "--force")[0] == 0
    assert (a / "f.txt").read_text() == "v2 from b"


def test_pull_rejects_corrupt_chunks_and_unsafe_paths(client, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "http", client)
    _copy(tmp_path, monkeypatch, "a", {"f.txt": "content"})
    run("push", REMOTE)
    b = _copy(tmp_path, monkeypatch, "b")

    monkeypatch.setattr(main, "download_chunk", lambda *_: b"tampered")
    code, out = run("pull", REMOTE)
    assert code == 1 and "Corrupt chunk" in out
    assert os.listdir(b) == [".datahub"]                # no file, no temp file
    assert not (b / ".datahub/HEAD").exists()

    real_get_commit = main.get_commit
    monkeypatch.setattr(main, "get_commit", lambda *a: {**real_get_commit(*a), "files": {"../evil.txt": "0" * 64}})
    code, out = run("pull", REMOTE)
    assert code == 1 and "unsafe path" in out
    assert not (tmp_path / "evil.txt").exists()


def test_pull_from_empty_remote(client, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "http", client)
    _copy(tmp_path, monkeypatch, "a")
    code, out = run("pull", REMOTE)
    assert code == 1 and "no commits" in out


def test_pull_keeps_local_edits_to_files_the_remote_did_not_change(client, monkeypatch, tmp_path):
    """Only paths changed on BOTH sides conflict; local edits/deletions elsewhere survive and push cleanly."""
    monkeypatch.setattr(api, "http", client)
    a = _copy(tmp_path, monkeypatch, "a", {"x.txt": "x1", "y.txt": "y1", "z.txt": "z1"})
    run("push", REMOTE)
    b = _copy(tmp_path, monkeypatch, "b")
    run("pull", REMOTE)
    (b / "y.txt").write_text("y2 from b")
    run("push", REMOTE)

    monkeypatch.chdir(a)
    (a / "x.txt").write_text("x2 local, unpushed")
    os.remove(a / "z.txt")
    code, out = run("pull", REMOTE)
    assert code == 0, out
    assert (a / "y.txt").read_text() == "y2 from b"           # remote change arrives
    assert (a / "x.txt").read_text() == "x2 local, unpushed"  # local edit kept
    assert not (a / "z.txt").exists()                          # local deletion kept

    assert run("push", REMOTE)[0] == 0                         # A's work now goes on top of B's
    monkeypatch.chdir(b)
    run("pull", REMOTE)
    assert (b / "x.txt").read_text() == "x2 local, unpushed" and not (b / "z.txt").exists()
