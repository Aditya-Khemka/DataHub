import click
import requests

# Module-level so tests can swap in FastAPI's TestClient (same get/post/put interface)
http = requests.Session()

BATCH = 10_000  # matches the server's MAX_BATCH


def _call(method, url, **kwargs):
    """Sends the request; any 4xx/5xx becomes a CLI error carrying the server's own message."""
    response = getattr(http, method)(url, **kwargs)
    if response.status_code == 409:
        raise click.ClickException("Remote has newer commits than this copy; pull first.")
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise click.ClickException(f"Server error {response.status_code}: {detail}")
    return response.json()


def _batched(hashes):
    for i in range(0, len(hashes), BATCH):
        yield hashes[i:i + BATCH]


def existing_files(remote_url, file_hashes):
    return {h for batch in _batched(file_hashes)
            for h in _call("post", f"{remote_url}/files/exists", json={"hashes": batch})["existing"]}


def missing_chunks(remote_url, chunk_hashes):
    return [h for batch in _batched(chunk_hashes)
            for h in _call("post", f"{remote_url}/chunks/missing", json={"hashes": batch})["missing"]]


def upload_chunk(remote_url, chunk_hash, data):
    return _call("put", f"{remote_url}/chunks/{chunk_hash}", data=data)


def register_files(remote_url, files):
    return _call("post", f"{remote_url}/files/", json={"files": files})


def create_commit(remote_url, payload):
    return _call("post", f"{remote_url}/commit/", json=payload)


def get_log(remote_url):
    return _call("get", f"{remote_url}/log")


def query_metadata(remote_url, query_str):
    return _call("post", f"{remote_url}/query/", json={"query": query_str})
