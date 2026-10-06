import json
import os
from typing import Dict, Any

import pandas as pd
import pyarrow.parquet as pq

READ_SIZE = 1024 * 1024


def _count_lines(file_path: str) -> int:
    """Counts lines by scanning bytes in 1 MiB blocks: constant memory, no decoding."""
    lines, last = 0, b"\n"
    with open(file_path, "rb") as f:
        while block := f.read(READ_SIZE):
            lines += block.count(b"\n")
            last = block[-1:]
    return lines + (last != b"\n")  # a final line without a trailing newline still counts


def extract_metrics(file_path: str, mime_type: str) -> Dict[str, Any]:
    """
    Given a path and MIME type (text/csv, application/json, or application/octet-stream for Parquet),
    returns schemas (column names and types) and row counts as a JSON-serializable dict.
    A flat JSON object of plain values (e.g. metrics.json) is returned as-is, so its keys are queryable.
    """
    if not os.path.exists(file_path):
        return {"error": f"File not found at {file_path}", "row_count": 0, "columns": []}

    try:
        # Handle CSV
        if mime_type == "text/csv" or file_path.endswith(".csv"):
            df = pd.read_csv(file_path, nrows=100) # Only read a sample for schema
            # counts newlines, so quoted fields containing newlines over-count; parse fully if exactness matters
            row_count = _count_lines(file_path) - 1  # minus the header
            return {
                "row_count": max(0, row_count),
                "schema": {col: str(dtype) for col, dtype in df.dtypes.items()},
                "columns": [{"name": col, "type": str(dtype)} for col, dtype in df.dtypes.items()],
                "format": "csv"
            }

        # Handle JSON
        elif mime_type == "application/json" or file_path.endswith(".json"):
            with open(file_path, "rb") as f:
                data = json.load(f)
            if isinstance(data, dict) and all(not isinstance(v, (dict, list)) for v in data.values()):
                return {"format": "json", **data}  # metrics file: {"accuracy": 0.94, ...}
            df = pd.DataFrame(data)
            return {
                "row_count": len(df),
                "schema": {col: str(dtype) for col, dtype in df.dtypes.items()},
                "columns": [{"name": col, "type": str(dtype)} for col, dtype in df.dtypes.items()],
                "format": "json"
            }

        # Handle Parquet: the footer alone has row count and schema, so never load the data
        elif "parquet" in mime_type or file_path.endswith(".parquet"):
            meta = pq.read_metadata(file_path)
            schema = meta.schema.to_arrow_schema()
            return {
                "row_count": meta.num_rows,
                "schema": {field.name: str(field.type) for field in schema},
                "columns": [{"name": field.name, "type": str(field.type)} for field in schema],
                "format": "parquet"
            }

    except Exception as e:
        return {
            "error": str(e),
            "row_count": 0,
            "schema": {},
            "columns": [],
            "status": "failed"
        }

    # Fallback default for unknown types
    return {"row_count": 0, "schema": {}, "columns": [], "status": "unknown_format"}
