import os
import pytest
import pandas as pd
import json
import pyarrow as pa
import pyarrow.parquet as pq
from metadata.extractor import extract_metrics

def test_extract_metrics_csv(tmp_path):
    # Create sample CSV
    csv_file = tmp_path / "test.csv"
    df = pd.DataFrame({"age": [25, 30], "name": ["Alice", "Bob"]})
    df.to_csv(csv_file, index=False)

    result = extract_metrics(str(csv_file), "text/csv")
    assert result["row_count"] == 2
    assert len(result["columns"]) == 2
    assert result["columns"][0]["name"] == "age"
    assert result["format"] == "csv"

def test_extract_metrics_json(tmp_path):
    # Create sample JSON
    json_file = tmp_path / "test.json"
    data = {"id": [1, 2], "value": [10.5, 20.1]}
    with open(json_file, "w") as f:
        json.dump(data, f)

    result = extract_metrics(str(json_file), "application/json")
    assert result["row_count"] == 2
    assert result["columns"][1]["name"] == "value"
    assert result["format"] == "json"

def test_extract_metrics_parquet(tmp_path):
    # Create sample Parquet
    parquet_file = tmp_path / "test.parquet"
    df = pd.DataFrame({"col1": [1, 2, 3], "col2": ["x", "y", "z"]})
    df.to_parquet(parquet_file)

    result = extract_metrics(str(parquet_file), "application/octet-stream")
    assert result["row_count"] == 3
    assert result["format"] == "parquet"

def test_extract_metrics_file_not_found():
    result = extract_metrics("non_existent.csv", "text/csv")
    assert "error" in result
    assert result["row_count"] == 0

def test_extract_metrics_invalid_format(tmp_path):
    # Create dummy text file
    txt_file = tmp_path / "test.txt"
    txt_file.write_text("just some text")

def test_extract_metrics_schema_contract(tmp_path):
    # Create sample CSV to test the precise README contract
    csv_file = tmp_path / "contract.csv"
    df = pd.DataFrame({"id": [1], "value": [0.5]})
    df.to_csv(csv_file, index=False)

    result = extract_metrics(str(csv_file), "text/csv")
    # Verify both README and modern columns formats coexist
    assert "schema" in result
    assert result["schema"] == {"id": "int64", "value": "float64"}
    assert "columns" in result
    assert result["columns"][0]["name"] == "id"


def test_flat_metrics_json_is_returned_as_queryable_values(tmp_path):
    """metrics.json-style files used to fail ('If using all scalar values, you must pass an index')."""
    f = tmp_path / "metrics.json"
    f.write_text(json.dumps({"accuracy": 0.94, "loss": 0.05, "model_version": "v2.0-tuned"}))
    assert extract_metrics(str(f), "application/json") == {
        "format": "json", "accuracy": 0.94, "loss": 0.05, "model_version": "v2.0-tuned"}


def test_json_list_of_records(tmp_path):
    f = tmp_path / "rows.json"
    f.write_text(json.dumps([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}, {"a": 3, "b": "z"}]))
    result = extract_metrics(str(f), "application/json")
    assert result["row_count"] == 3 and [c["name"] for c in result["columns"]] == ["a", "b"]


def test_parquet_reads_only_the_footer(tmp_path, monkeypatch):
    """Row count and schema come from metadata; loading the table would blow up RAM on large files."""
    f = tmp_path / "big.parquet"
    pd.DataFrame({"x": range(1000), "y": ["v"] * 1000}).to_parquet(f)
    monkeypatch.setattr(pq, "read_table", lambda *a, **k: pytest.fail("read_table must not be used"))
    result = extract_metrics(str(f), "application/octet-stream")
    assert result["row_count"] == 1000 and result["schema"] == {"x": "int64", "y": "string"}


def test_csv_row_count_without_trailing_newline(tmp_path):
    f = tmp_path / "t.csv"
    f.write_bytes(b"a,b\n1,2\n3,4")  # last row has no newline
    assert extract_metrics(str(f), "text/csv")["row_count"] == 2
