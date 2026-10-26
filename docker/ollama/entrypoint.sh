#!/bin/bash
set -e

ollama serve &
SERVE_PID=$!

# Wait for ollama to be ready
until ollama list > /dev/null 2>&1; do
    sleep 1
done

# Pull a model only when it is missing (idempotent across restarts).
ensure_model() {
    local name="$1"
    case "$name" in
        *:*) ;;
        *) name="${name}:latest" ;;
    esac
    if ! ollama list | awk 'NR>1 {print $1}' | grep -qxF "$name"; then
        echo "Pulling ${name}..."
        ollama pull "$name"
    fi
}

ensure_model "${BINDER_EMBEDDING_MODEL:-bge-m3}"

# Ask is best-effort: a failed chat model pull must not stop embeddings or the server.
case "${BINDER_ASK:-}" in
    0|false|no) ;;
    *) ensure_model "${BINDER_CHAT_MODEL:-gemma3:4b}" || echo "warning: could not pull ${BINDER_CHAT_MODEL:-gemma3:4b}; ask will be unavailable" >&2 ;;
esac

wait $SERVE_PID
