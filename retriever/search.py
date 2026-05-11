import sqlite3
import numpy as np
import os

DB_PATH = os.environ.get("DB_PATH", "/data/retriever.db")


def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def setup_db():
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            parent_heading TEXT DEFAULT '',
            embedding BLOB,
            UNIQUE(filepath, chunk_index)
        )
    """)
    db.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS fts_documents USING fts5(
            filepath, content, parent_heading,
            content=documents, content_rowid=id
        )
    """)
    db.execute("PRAGMA journal_mode=WAL")
    db.commit()
    db.close()


def rebuild_fts():
    db = get_db()
    db.execute("INSERT INTO fts_documents(fts_documents) VALUES('rebuild')")
    db.commit()
    db.close()


def store_chunks(chunks: list[dict]):
    db = get_db()
    for c in chunks:
        embedding_bytes = c.get("embedding")
        if embedding_bytes is not None and isinstance(embedding_bytes, np.ndarray):
            embedding_bytes = embedding_bytes.astype(np.float32).tobytes()
        db.execute(
            """INSERT OR REPLACE INTO documents (filepath, chunk_index, content, parent_heading, embedding)
               VALUES (?, ?, ?, ?, ?)""",
            (c["filepath"], c["chunk_index"], c["content"], c.get("parent_heading", ""), embedding_bytes),
        )
    db.commit()
    db.close()


def delete_file_chunks(filepath: str):
    db = get_db()
    db.execute("DELETE FROM documents WHERE filepath = ?", (filepath,))
    db.commit()
    db.close()


def get_all_embeddings():
    db = get_db()
    rows = db.execute(
        "SELECT id, filepath, chunk_index, content, parent_heading, embedding FROM documents WHERE embedding IS NOT NULL"
    ).fetchall()
    db.close()
    result = []
    for r in rows:
        emb = np.frombuffer(r["embedding"], dtype=np.float32) if r["embedding"] else None
        result.append({
            "id": r["id"],
            "filepath": r["filepath"],
            "chunk_index": r["chunk_index"],
            "content": r["content"],
            "parent_heading": r["parent_heading"],
            "embedding": emb,
        })
    return result


def search_keyword(query: str, limit: int = 20) -> list[dict]:
    db = get_db()
    # Escape FTS5 special characters
    query_safe = " ".join(word for word in query.split())
    try:
        rows = db.execute(
            """SELECT id, filepath, chunk_index, content, parent_heading, rank
               FROM fts_documents
               WHERE fts_documents MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (query_safe, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    db.close()
    return [
        {
            "id": r["id"],
            "filepath": r["filepath"],
            "chunk_index": r["chunk_index"],
            "content": r["content"],
            "parent_heading": r["parent_heading"],
            "score": 1.0 / (1.0 + abs(r["rank"])),
        }
        for r in rows
    ]


def search_vector(query_embedding: np.ndarray, top_k: int = 20) -> list[dict]:
    all_docs = get_all_embeddings()
    if not all_docs:
        return []
    query_norm = query_embedding / (np.linalg.norm(query_embedding) + 1e-10)
    scored = []
    for d in all_docs:
        if d["embedding"] is None:
            continue
        doc_norm = d["embedding"] / (np.linalg.norm(d["embedding"]) + 1e-10)
        sim = float(np.dot(query_norm, doc_norm))
        scored.append((sim, d))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "id": d["id"],
            "filepath": d["filepath"],
            "chunk_index": d["chunk_index"],
            "content": d["content"],
            "parent_heading": d["parent_heading"],
            "score": sim,
        }
        for sim, d in scored[:top_k]
    ]


def hybrid_search(query: str, query_embedding: np.ndarray, top_k: int = 10) -> list[dict]:
    kw_results = search_keyword(query, limit=top_k * 2)
    vec_results = search_vector(query_embedding, top_k=top_k * 2)
    # Reciprocal rank fusion
    rrf_k = 60
    scores: dict[int, float] = {}
    seen: dict[int, dict] = {}
    for rank, r in enumerate(kw_results):
        doc_id = r["id"]
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (rrf_k + rank)
        seen[doc_id] = r
    for rank, r in enumerate(vec_results):
        doc_id = r["id"]
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (rrf_k + rank)
        seen[doc_id] = r
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [
        {**seen[doc_id], "score": round(rrf_score, 4)}
        for doc_id, rrf_score in ranked[:top_k]
    ]


def indexed_file_count() -> int:
    db = get_db()
    count = db.execute("SELECT COUNT(DISTINCT filepath) FROM documents").fetchone()[0]
    db.close()
    return count


def total_chunk_count() -> int:
    db = get_db()
    count = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    db.close()
    return count
