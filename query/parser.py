def build_filter(query_string: str) -> dict:
    """
    Converts 'accuracy > 0.9' into a dictionary AST.
    """

    parts = query_string.strip().split()

    if len(parts) != 3:
        raise ValueError("Invalid query format. Use: metric operator value")

    metric = parts[0]
    operator = parts[1]
    value = float(parts[2])

    ast = {
        "metric": metric,
        "operator": operator,
        "value": value
    }

    return ast

from sqlalchemy.orm import Session
from sqlalchemy import select
from infrastructure.db import Metadata


def execute_query(session: Session, ast: dict, files: dict) -> list:
    """
    Runs the filter over the stats of the given files ({path: file_hash}, e.g. one commit's files).
    Returns [(path, file_hash, stats)] sorted by path; a file stored under several paths is listed once per path.
    """
    metric = ast["metric"]
    operator = ast["operator"]
    value = ast["value"]

    # Access JSON field
    column = Metadata.stats[metric].as_float()

    query = select(Metadata)

    if operator == ">":
        query = query.where(column > value)
    elif operator == "<":
        query = query.where(column < value)
    elif operator == ">=":
        query = query.where(column >= value)
    elif operator == "<=":
        query = query.where(column <= value)
    elif operator == "==":
        query = query.where(column == value)
    else:
        raise ValueError("Invalid operator")

    # filters by metric across all stored files, then keeps this commit's in Python
    # (an IN list of every file hash would hit SQLite's bound-parameter limit on big commits);
    # push the commit scope into SQL if the metadata table grows very large.
    matches = {m.target_hash: m.stats for m in session.scalars(query)}
    return sorted((path, h, matches[h]) for path, h in files.items() if h in matches)
