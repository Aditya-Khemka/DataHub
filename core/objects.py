import hashlib
import re
import unicodedata
from datetime import datetime, timezone

TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
OBJECT_TYPES = ("tree", "file")


def is_valid_hash(h):
    """True only for a lowercase SHA-256 hex digest (also blocks path tricks like '../x')."""
    return isinstance(h, str) and re.fullmatch(r"[0-9a-f]{64}", h) is not None


def _clean_name(name):
# Unicode normalized to one form; empty names, ., .., /, newline and null are rejected
    name = unicodedata.normalize("NFC", name)
    if name in ("", ".", "..") or any(c in name for c in "/\n\0"):
        raise ValueError(f"Invalid name: {name!r}")
    return name


def tree_hash(entries):
    """
    Hashes one folder. entries: [(type, hash, name)] where type is 'tree' or 'file'.
    Returns (tree_hash, entries_sorted).
    """
    seen = set()
    cleaned = []
    for obj_type, obj_hash, name in entries:
        name = _clean_name(name)
        if obj_type not in OBJECT_TYPES:
            raise ValueError(f"Invalid object type: {obj_type!r}")
        if not is_valid_hash(obj_hash):
            raise ValueError(f"Invalid hash for {name!r}: {obj_hash!r}")
        if name in seen:
            raise ValueError(f"Duplicate name in tree: {name!r}")
        seen.add(name)
        cleaned.append((obj_type, obj_hash, name))

    cleaned.sort(key=lambda e: e[2].encode("utf-8"))
    body = "datahub-tree-v1\n" + "".join(f"{t} {h} {n}\n" for t, h, n in cleaned)
    return hashlib.sha256(body.encode("utf-8")).hexdigest(), cleaned


def build_trees(files):
    """
    Builds the Merkle tree bottom-up from {"dir/sub/name.csv": file_hash}.
    Returns (root_hash, {tree_hash: sorted_entries}); identical folders appear once.
    """
    root = {}
    for path, file_hash in files.items():
        parts = [_clean_name(p) for p in path.split("/")]
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise ValueError(f"Path is both a file and a folder: {path!r}")
        if parts[-1] in node:
            raise ValueError(f"Duplicate or conflicting path: {path!r}")
        node[parts[-1]] = file_hash

    trees = {}

    def visit(node):
        entries = [
            ("tree", visit(child), name) if isinstance(child, dict) else ("file", child, name)
            for name, child in node.items()
        ]
        h, sorted_entries = tree_hash(entries)
        trees[h] = sorted_entries
        return h

    return visit(root), trees


def format_time(dt):
    """Canonical commit time: UTC, whole seconds."""
    return dt.astimezone(timezone.utc).strftime(TIME_FORMAT)


def commit_hash(tree, parent, author, time, message):
    """Hashes a commit; parent is None for the first commit. time must come from format_time()."""
    if not is_valid_hash(tree):
        raise ValueError(f"Invalid tree hash: {tree!r}")
    if parent is not None and not is_valid_hash(parent):
        raise ValueError(f"Invalid parent hash: {parent!r}")
    if "\n" in author:
        raise ValueError("Author must not contain a newline")
    if datetime.strptime(time, TIME_FORMAT).strftime(TIME_FORMAT) != time:
        raise ValueError(f"Non-canonical time: {time!r}")

    body = (
        f"datahub-commit-v1\ntree {tree}\nparent {parent or 'none'}\n"
        f"author {author}\ntime {time}\n\n{message}"
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
