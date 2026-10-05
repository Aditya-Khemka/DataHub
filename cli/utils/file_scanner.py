import os

# Never part of the dataset: DataHub's own state, other VCS/tooling folders
SKIP_DIRS = {".datahub", ".git", "__pycache__", "venv", ".venv"}


def scan_files(root="."):
    """Returns repo-relative paths with '/' separators (same on every OS), sorted."""
    files = []
    for folder, dirs, filenames in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]  # prune: don't descend into skipped folders
        for name in filenames:
            files.append(os.path.relpath(os.path.join(folder, name), root).replace(os.sep, "/"))
    return sorted(files)
