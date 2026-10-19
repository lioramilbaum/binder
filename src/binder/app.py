import os
from http.server import ThreadingHTTPServer

from .binder import Binder
from .config import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    OLLAMA_BASE_URL,
    SEMANTIC_SEARCH_ENABLED,
)
from .embeddings import EmbeddingWorker, OllamaClient


def run(host: str | None = None, port: int | None = None) -> None:
    host = host or os.environ.get("BINDER_HOST", "127.0.0.1")
    port = port or int(os.environ.get("BINDER_PORT", "8000"))
    server = ThreadingHTTPServer((host, port), Binder)
    print(f"Binder running at http://{host}:{port}")
    worker = None
    if SEMANTIC_SEARCH_ENABLED:
        client = OllamaClient(OLLAMA_BASE_URL, EMBEDDING_MODEL, EMBEDDING_DIMENSIONS)
        worker = EmbeddingWorker(Binder.store.factory, client)
        Binder.store.enable_semantic_search(client, worker)
        worker.start()
        print(f"Semantic search: {EMBEDDING_MODEL} via {OLLAMA_BASE_URL}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if worker is not None:
            worker.stop()
        server.server_close()


if __name__ == "__main__":
    run()
