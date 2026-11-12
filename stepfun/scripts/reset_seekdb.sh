#!/usr/bin/env bash
# Reset the stepfun seekdb: drop all data and re-initialize from scratch.
# WARNING: deletes the stepfun_app database and all imported documents.
set -euo pipefail

CONTAINER=seekdb-stepfun
VOLUME=seekdb-stepfun-data
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[reset] stopping and removing container ${CONTAINER}..."
docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
echo "[reset] removing data volume ${VOLUME}..."
docker volume rm "${VOLUME}" >/dev/null 2>&1 || true

exec "${SCRIPT_DIR}/init_seekdb.sh"
