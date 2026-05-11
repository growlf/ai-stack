# Retriever — Obsidian Vault RAG

The retriever replaces Khoj + PostgreSQL as a lightweight, API-only RAG service for your Obsidian vault. It uses sqlite-vec (file-based, no separate DB) and hybrid search (keyword + vector) for fast, accurate retrieval.

---

## How it works

```
Obsidian vault (on host, :ro)
       │
       ▼
retriever container :42000
  ├── watchdog scans for file changes (inotify)
  ├── embeds chunks via Olla → ollama-arc (nomic-embed-text)
  ├── stores vectors in sqlite-vec (embedded in SQLite)
  └── indexes keywords in FTS5 (BM25 scoring)

Search:
  POST /search {"query": "what did I write about DNS?"}
  → hybrid search (vector + keyword, RRF fusion)
  → top 10 chunks with scores
```

Configuration via `.env`:

```
RETRIEVER_PORT=42000
RETRIEVER_VAULT_PATH=/home/you/obsidian
RETRIEVER_EMBED_MODEL=nomic-embed-text
RETRIEVER_CHUNK_SIZE=512
RETRIEVER_CHUNK_OVERLAP=64
```

---

## API

### `GET /health`

```bash
curl localhost:42000/health
```

Returns:
```json
{
  "status": "ok",
  "indexed_files": 1240,
  "total_chunks": 5420,
  "vault_watching": true,
  "vault_path": "/vault",
  "is_indexing": false
}
```

### `POST /search`

```bash
curl -X POST localhost:42000/search \
  -H 'Content-Type: application/json' \
  -d '{"query": "what did I write about networking?", "top_k": 10}'
```

Returns:
```json
{
  "results": [
    {
      "filepath": "networking/dns-notes.md",
      "chunk_index": 2,
      "content": "...",
      "parent_heading": "DNS Configuration",
      "score": 0.921
    }
  ]
}
```

### `POST /reindex`

Force a full reindex:

```bash
curl -X POST localhost:42000/reindex
```

Returns immediately — reindexing runs in the background.

---

## Using with OpenCode

OpenCode calls the retriever as a native tool via the project-level `.opencode/tools/vault-search.ts`. This tool is automatically available when you run `opencode` from the project directory.

Two tools are provided:

- **`vault-search`** — search the entire vault for notes matching a query
- **`vault-search_per_source`** — search within a specific file or subdirectory

Both tools call `POST /search` on the retriever API and return file paths, content snippets, and relevance scores.

The tools are pre-configured in `.opencode/config.json` and auto-approved by default. No manual setup is needed.

---

## Performance tuning

| Setting | Effect | Default |
|---------|--------|---------|
| `CHUNK_SIZE` | Max characters per chunk. Smaller = more precise, larger = more context. | 512 |
| `CHUNK_OVERLAP` | Overlap between chunks. Helps with boundary crossing. | 64 |
| `EMBED_MODEL` | Embedding model used by ollama-arc. `nomic-embed-text` is fast and small. | nomic-embed-text |

For a very large vault:
- **Chunk size**: 512-768 works well for most notes. Increase to 1024 if notes are long-form.
- **Embedding model**: `nomic-embed-text` (768-dim, 274MB) is fast on iGPU. For better accuracy, try `mxbai-embed-large` (1024-dim, 334MB).
- **Re-indexing**: Trigger `POST /reindex` after bulk imports. The watchdog handles incremental changes in real-time.

---

## Troubleshooting

**Retriever won't start:**
```bash
docker logs retriever --tail 30
```

**Vault not indexing:**
- Verify `RETRIEVER_VAULT_PATH` in `.env` points to a real directory
- Check the mount: `docker exec retriever ls /vault`
- Check Olla is healthy: `curl localhost:40114/internal/health`

**nomic-embed-text errors:**
```bash
docker exec ollama-arc ollama pull nomic-embed-text:latest
```

**Search returns no results:**
- Check `curl localhost:42000/health` — if `indexed_files` is 0, the vault isn't populated or the path is wrong
- If indexing is running, wait for it to complete
