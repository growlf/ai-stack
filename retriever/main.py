import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from indexer import VAULT_PATH, embed_text, is_indexing, scan_vault, start_watcher, stop_watcher
from pydantic import BaseModel
from search import hybrid_search, indexed_file_count, rebuild_fts, setup_db, total_chunk_count


class SearchRequest(BaseModel):
    query: str
    top_k: int = 10


class SearchResult(BaseModel):
    filepath: str
    chunk_index: int
    content: str
    parent_heading: str
    score: float


class SearchResponse(BaseModel):
    results: list[SearchResult]


class HealthResponse(BaseModel):
    status: str
    indexed_files: int
    total_chunks: int
    vault_watching: bool
    vault_path: str
    is_indexing: bool


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_db()
    if os.path.isdir(VAULT_PATH):
        scan_thread = threading.Thread(target=run_initial_scan, daemon=True)
        scan_thread.start()
    yield
    stop_watcher()


def run_initial_scan():
    scan_vault()
    rebuild_fts()
    start_watcher()


app = FastAPI(title="Retriever", version="0.1.0", lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        indexed_files=indexed_file_count(),
        total_chunks=total_chunk_count(),
        vault_watching=True,
        vault_path=VAULT_PATH,
        is_indexing=is_indexing(),
    )


@app.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest):
    emb = await embed_text(req.query)
    if emb is None:
        return SearchResponse(results=[])
    results = hybrid_search(req.query, emb, top_k=req.top_k)
    return SearchResponse(results=[SearchResult(**r) for r in results])


@app.post("/reindex")
async def reindex():
    from search import get_db

    db = get_db()
    db.execute("DELETE FROM documents")
    db.commit()
    db.close()
    threading.Thread(target=lambda: (scan_vault(), rebuild_fts()), daemon=True).start()
    return {"status": "reindexing"}
