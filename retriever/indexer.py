import os
import re

import httpx
import numpy as np
from search import delete_file_chunks, rebuild_fts, store_chunks
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

VAULT_PATH = os.environ.get("VAULT_PATH", "/vault")
OLLA_URL = os.environ.get("OLLA_URL", "http://olla:40114")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "512"))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "64"))

_watcher_observer: Observer | None = None
_indexing = False


def chunk_markdown(text: str, filepath: str) -> list[dict]:
    lines = text.split("\n")
    chunks = []
    current_section = []
    parent_headings: list[str] = []
    chunk_index = 0

    for line in lines:
        heading_match = re.match(r"^(#{1,6})\s+(.+)", line)
        if heading_match:
            if current_section:
                content = "\n".join(current_section).strip()
                if content:
                    ctx = " > ".join(parent_headings) if parent_headings else ""
                    prefix = f"# {ctx}\n\n" if ctx else ""
                    chunks.append(
                        {
                            "filepath": filepath,
                            "chunk_index": chunk_index,
                            "content": prefix + content,
                            "parent_heading": parent_headings[-1] if parent_headings else "",
                        }
                    )
                    chunk_index += 1
                current_section = []
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()
            parent_headings = parent_headings[: level - 1] + [heading_text]
            current_section.append(line)
        else:
            current_section.append(line)

    if current_section:
        content = "\n".join(current_section).strip()
        if content:
            ctx = " > ".join(parent_headings) if parent_headings else ""
            prefix = f"# {ctx}\n\n" if ctx else ""
            chunks.append(
                {
                    "filepath": filepath,
                    "chunk_index": chunk_index,
                    "content": prefix + content,
                    "parent_heading": parent_headings[-1] if parent_headings else "",
                }
            )

    # Sub-chunk long sections
    final_chunks = []
    for c in chunks:
        if len(c["content"]) > CHUNK_SIZE:
            sub_chunks = sub_chunk_text(c["content"], c["filepath"], c["parent_heading"])
            final_chunks.extend(sub_chunks)
        else:
            final_chunks.append(c)

    # Re-index chunk indices
    for i, c in enumerate(final_chunks):
        c["chunk_index"] = i

    return final_chunks


def sub_chunk_text(text: str, filepath: str, parent_heading: str) -> list[dict]:
    paragraphs = re.split(r"\n\s*\n", text)
    chunks = []
    current = []
    current_len = 0
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if current_len + len(para) > CHUNK_SIZE and current:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(para)
        current_len += len(para)
    if current:
        chunks.append("\n\n".join(current))

    return [
        {
            "filepath": filepath,
            "chunk_index": i,
            "content": c,
            "parent_heading": parent_heading,
        }
        for i, c in enumerate(chunks)
    ]


async def embed_text(text: str) -> np.ndarray | None:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{OLLA_URL}/olla/ollama/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": text},
            )
            resp.raise_for_status()
            data = resp.json()
            return np.array(data["embedding"], dtype=np.float32)
    except Exception:
        return None


def embed_text_sync(text: str) -> np.ndarray | None:
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{OLLA_URL}/olla/ollama/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": text},
            )
            resp.raise_for_status()
            data = resp.json()
            return np.array(data["embedding"], dtype=np.float32)
    except Exception:
        return None


def index_file(filepath: str) -> int:
    abs_path = os.path.join(VAULT_PATH, filepath) if not filepath.startswith("/") else filepath
    if not os.path.isfile(abs_path):
        return 0
    try:
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return 0

    rel_path = os.path.relpath(abs_path, VAULT_PATH)
    chunks = chunk_markdown(text, rel_path)

    for c in chunks:
        emb = embed_text_sync(c["content"])
        if emb is not None:
            c["embedding"] = emb.astype(np.float32).tobytes()
        else:
            c["embedding"] = None

    store_chunks(chunks)
    return len(chunks)


def scan_vault():
    global _indexing
    _indexing = True
    total = 0
    for root, _dirs, files in os.walk(VAULT_PATH):
        for fname in files:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(root, fname)
            count = index_file(fpath)
            if count:
                total += count
    rebuild_fts()
    _indexing = False
    return total


class VaultHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if event.is_directory or not event.src_path.endswith(".md"):
            return
        delete_file_chunks(os.path.relpath(event.src_path, VAULT_PATH))
        index_file(event.src_path)
        rebuild_fts()

    def on_created(self, event):
        if event.is_directory or not event.src_path.endswith(".md"):
            return
        index_file(event.src_path)
        rebuild_fts()

    def on_deleted(self, event):
        if event.is_directory or not event.src_path.endswith(".md"):
            return
        delete_file_chunks(os.path.relpath(event.src_path, VAULT_PATH))


def start_watcher():
    global _watcher_observer
    if _watcher_observer:
        return
    _watcher_observer = Observer()
    handler = VaultHandler()
    _watcher_observer.schedule(handler, VAULT_PATH, recursive=True)
    _watcher_observer.start()


def stop_watcher():
    global _watcher_observer
    if _watcher_observer:
        _watcher_observer.stop()
        _watcher_observer.join()
        _watcher_observer = None


def is_indexing() -> bool:
    return _indexing
