#!/usr/bin/env bash
# Initialize the seekdb Docker container for the stepfun track.
# Creates the stepfun_app database with the docs table (VECTOR + HNSW + IK fulltext).
# Idempotent: safe to run multiple times.
set -euo pipefail

CONTAINER=seekdb-stepfun
IMAGE=oceanbase/seekdb:latest
PORT=${SEEKDB_PORT:-2881}
VOLUME=seekdb-stepfun-data
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -n "$(docker ps -aq -f name=^${CONTAINER}$)" ]; then
  if [ -z "$(docker ps -q -f name=^${CONTAINER}$)" ]; then
    echo "[init] starting existing container ${CONTAINER}..."
    docker start "${CONTAINER}" >/dev/null
  else
    echo "[init] container ${CONTAINER} already running"
  fi
else
  echo "[init] creating container ${CONTAINER} (port ${PORT}, volume ${VOLUME})..."
  docker run -d --name "${CONTAINER}" -p "${PORT}:2881" \
    -e ROOT_PASSWORD="" -v "${VOLUME}:/var/lib/seekdb" "${IMAGE}" >/dev/null
fi

echo "[init] waiting for SQL readiness..."
for i in $(seq 1 60); do
  if docker exec "${CONTAINER}" mysql -h127.0.0.1 -uroot -P2881 -N -e "SELECT 1" >/dev/null 2>&1; then
    break
  fi
  if [ "$i" -eq 60 ]; then
    echo "[init] ERROR: seekdb not ready after 5 minutes" >&2
    docker logs --tail 20 "${CONTAINER}" >&2 || true
    exit 1
  fi
  sleep 5
done

echo "[init] applying schema..."
docker exec -i "${CONTAINER}" mysql -h127.0.0.1 -uroot -P2881 < "${SCRIPT_DIR}/schema.sql"

echo "[init] verifying..."
docker exec "${CONTAINER}" mysql -h127.0.0.1 -uroot -P2881 -N -e \
  "SELECT CONCAT('version: ', version()); SHOW TABLES FROM stepfun_app;"

echo "[init] done. seekdb is on 127.0.0.1:${PORT}, database stepfun_app"
