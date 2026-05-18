"""
Tests for retriever/main.py endpoints.

Mocks DB and embedding dependencies — no sqlite DB or Ollama service required.
The retriever module is imported with sys.path set in conftest.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _make_client():
    """Create a TestClient with all heavy I/O dependencies patched out."""
    with (
        patch("main.setup_db"),
        patch("main.indexed_file_count", return_value=5),
        patch("main.total_chunk_count", return_value=42),
        patch("main.is_indexing", return_value=False),
        patch("main.VAULT_PATH", "/tmp/test-vault"),
        patch("main.stop_watcher"),
        patch("os.path.isdir", return_value=False),  # skip initial scan in lifespan
    ):
        import main as retriever_main

        with TestClient(retriever_main.app) as client:
            yield client


@pytest.fixture(scope="module")
def client():
    yield from _make_client()


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_status_is_ok(self, client):
        resp = client.get("/health")
        assert resp.json()["status"] == "ok"

    def test_response_includes_required_fields(self, client):
        resp = client.get("/health")
        body = resp.json()
        required = {"status", "indexed_files", "total_chunks", "vault_watching", "vault_path", "is_indexing"}
        assert required.issubset(body.keys())

    def test_indexed_files_and_chunks_are_integers(self, client):
        resp = client.get("/health")
        body = resp.json()
        assert isinstance(body["indexed_files"], int)
        assert isinstance(body["total_chunks"], int)


# ---------------------------------------------------------------------------
# POST /search
# ---------------------------------------------------------------------------


class TestSearch:
    def test_returns_200_for_valid_query(self, client):
        with patch("main.embed_text", new_callable=AsyncMock, return_value=[0.1] * 384):
            with patch("main.hybrid_search", return_value=[]):
                resp = client.post("/search", json={"query": "test"})
        assert resp.status_code == 200

    def test_response_has_results_list(self, client):
        with patch("main.embed_text", new_callable=AsyncMock, return_value=[0.1] * 384):
            with patch("main.hybrid_search", return_value=[]):
                resp = client.post("/search", json={"query": "test"})
        assert "results" in resp.json()
        assert isinstance(resp.json()["results"], list)

    def test_missing_query_returns_422(self, client):
        resp = client.post("/search", json={})
        assert resp.status_code == 422

    def test_embed_failure_returns_empty_results(self, client):
        """When embedding fails, return empty results gracefully."""
        with patch("main.embed_text", new_callable=AsyncMock, return_value=None):
            resp = client.post("/search", json={"query": "broken embed"})
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_results_have_correct_schema(self, client):
        mock_result = {
            "filepath": "/vault/notes.md",
            "chunk_index": 0,
            "content": "Sample content here",
            "parent_heading": "## Notes",
            "score": 0.87,
        }
        with patch("main.embed_text", new_callable=AsyncMock, return_value=[0.1] * 384):
            with patch("main.hybrid_search", return_value=[mock_result]):
                resp = client.post("/search", json={"query": "notes"})
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert len(results) == 1
        r = results[0]
        assert "filepath" in r
        assert "chunk_index" in r
        assert "content" in r
        assert "score" in r

    def test_top_k_is_accepted(self, client):
        with patch("main.embed_text", new_callable=AsyncMock, return_value=[0.1] * 384):
            with patch("main.hybrid_search", return_value=[]):
                resp = client.post("/search", json={"query": "test", "top_k": 3})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /reindex
# ---------------------------------------------------------------------------


class TestReindex:
    def test_returns_200(self, client):
        mock_db = MagicMock()
        # get_db is imported locally inside the reindex handler, so patch at source
        with patch("search.get_db", return_value=mock_db):
            resp = client.post("/reindex")
        assert resp.status_code == 200

    def test_returns_reindexing_status(self, client):
        mock_db = MagicMock()
        with patch("search.get_db", return_value=mock_db):
            resp = client.post("/reindex")
        assert resp.json()["status"] == "reindexing"
