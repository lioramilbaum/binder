#!/bin/sh
set -eu

# Apply version-controlled schema migrations before serving requests.
alembic -c /app/pyproject.toml upgrade head

exec binder-server
