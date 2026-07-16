#!/bin/sh
# Run on every container start. Idempotent: alembic upgrade head is a
# no-op if the DB is already at head, so this is safe to run every
# time, not just the first time.
set -e

echo "Waiting for the database to accept connections..."
python -m scripts.wait_for_db

echo "Running database migrations..."
alembic upgrade head

echo "Starting the app."
exec "$@"
