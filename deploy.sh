#!/usr/bin/env bash
# Deploys this local checkout to production (62.109.6.186). ALWAYS use this
# script for deploys — never a bare `tar -czf . ...` / `rsync` — because of
# the 2026-11-02 incident: /lk ("Конверсии в Метрику") is maintained by a
# DIFFERENT, independent Claude Code session/agent directly on the server,
# with its own files that never live in this checkout or this git repo
# (app/lk.py, app/scoring.py, app/lk_cities.py, app/templates/lk.html,
# app/static/lk.js, app/static/lk.css, test_lk.py, tools/backfill_ymuid.py).
# A wholesale directory sync overwrote their newer work with this repo's
# stale copies TWICE before this script existed. The exclude list below is
# the actual fix — it is not optional, do not deploy without it.
set -euo pipefail

SERVER=62.109.6.186
REMOTE_DIR=/var/www/dima/data/addAvto
REMOTE_STAGING=/var/www/dima/data/addavto_staging_$$
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Paths /lk owns — never deployed from here, under any circumstances.
LK_OWNED_PATHS=(
  app/lk.py
  app/scoring.py
  app/lk_cities.py
  app/templates/lk.html
  app/static/lk.js
  app/static/lk.css
  test_lk.py
  tools/backfill_ymuid.py
)

TARBALL="/tmp/addavto_deploy_$$.tar.gz"
trap 'rm -f "$TARBALL"' EXIT

EXCLUDES=(--exclude='.venv' --exclude='data' --exclude='__pycache__' --exclude='.pytest_cache' --exclude='.git')
for p in "${LK_OWNED_PATHS[@]}"; do
  EXCLUDES+=(--exclude="$p")
done

echo "--- building tarball (excluding /lk-owned paths) ---"
tar -czf "$TARBALL" -C "$LOCAL_DIR" "${EXCLUDES[@]}" .

echo "--- uploading + staging on $SERVER ---"
scp -q "$TARBALL" "$SERVER:/tmp/addavto_deploy_$$.tar.gz"
ssh "$SERVER" "mkdir -p '$REMOTE_STAGING' && tar -xzf /tmp/addavto_deploy_$$.tar.gz -C '$REMOTE_STAGING' && rm -f /tmp/addavto_deploy_$$.tar.gz && cp '$REMOTE_DIR/.env' '$REMOTE_STAGING/.env'"

echo "--- sanity: confirm staging tarball truly has none of the /lk-owned paths ---"
for p in "${LK_OWNED_PATHS[@]}"; do
  if ssh "$SERVER" "[ -e '$REMOTE_STAGING/$p' ]"; then
    echo "REFUSING TO DEPLOY: $p is present in the staged tree — this should never happen, exclude list is broken." >&2
    ssh "$SERVER" "rm -rf '$REMOTE_STAGING'"
    exit 1
  fi
done

echo "--- copying current live /lk files into staging so app.main can import (read-only, never synced back) ---"
for p in "${LK_OWNED_PATHS[@]}"; do
  ssh "$SERVER" "[ -e '$REMOTE_DIR/$p' ] && mkdir -p '$REMOTE_STAGING/$(dirname "$p")' && cp -r '$REMOTE_DIR/$p' '$REMOTE_STAGING/$p' || true"
done

echo "--- running tests in staging (real .env) ---"
ssh "$SERVER" "cd '$REMOTE_STAGING' && python3.12 -m venv .venv && .venv/bin/pip install -q -r requirements.txt && DATABASE_PATH=./data_test/app.db .venv/bin/python -m pytest tests/ -q"

echo "--- syncing into live dir (rsync, no --delete, /lk paths excluded again for safety) ---"
RSYNC_EXCLUDES=(--exclude='.env' --exclude='.env.bak' --exclude='data' --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache' --exclude='.git')
for p in "${LK_OWNED_PATHS[@]}"; do
  RSYNC_EXCLUDES+=(--exclude="$p")
done
ssh "$SERVER" "rsync -a ${RSYNC_EXCLUDES[*]} '$REMOTE_STAGING/' '$REMOTE_DIR/' && rm -rf '$REMOTE_STAGING'"

echo "--- done. Restart on the server is still a manual step (needs sudo): ---"
echo "    ssh $SERVER 'cd $REMOTE_DIR && sudo docker compose up -d --build'"
