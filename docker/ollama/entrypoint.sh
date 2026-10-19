#!/bin/bash
set -e

ollama serve &
SERVE_PID=$!

# Wait for ollama to be ready
until curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; do
    sleep 1
done

# Pull model if not already present (idempotent)
MODEL="${BINDER_EMBEDDING_MODEL:-bge-m3}"
if ! ollama list | grep -qE "^${MODEL}(:[[:alnum:]]|$)"; then
    echo "Pulling ${MODEL}..."
    ollama pull "${MODEL}"
fi

wait $SERVE_PID
