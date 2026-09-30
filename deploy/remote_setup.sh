#!/bin/bash
# Remote per-node setup for the Pond Planning System API (run on each node).
# Mirrors the group-chat deployment pattern: per-node venv + gunicorn --daemon.
# Idempotent: safe to re-run for updates (kills the previous daemon first).
set -e
cd ~/pond_catchment_backend

if [ ! -d venv ]; then
  python3 -m venv venv
  ./venv/bin/pip install -q --upgrade pip 2>/dev/null || true
  ./venv/bin/pip install -q -r requirements.txt
  md5sum requirements.txt | cut -d' ' -f1 > venv/.req_md5
elif [ -f requirements.txt ]; then
  CURR_MD5=$(md5sum requirements.txt | cut -d' ' -f1)
  SAVED_MD5=$(cat venv/.req_md5 2>/dev/null || true)
  if [ "$CURR_MD5" != "$SAVED_MD5" ]; then
    echo "--- requirements.txt changed, installing dependencies ---"
    ./venv/bin/pip install -q -r requirements.txt
    echo "$CURR_MD5" > venv/.req_md5
  else
    echo "--- dependencies unchanged, skipping pip install ---"
  fi
fi

# Stop a previous pond daemon (matched by OUR venv path only).
if [ -f gunicorn.pid ]; then
  kill "$(cat gunicorn.pid)" 2>/dev/null || true
  sleep 1
fi
pkill -f "pond_catchment_backend/venv/bin/gunicorn" 2>/dev/null || true
sleep 1

nohup ./venv/bin/gunicorn \
  -k uvicorn.workers.UvicornWorker \
  -w 2 --threads 4 \
  --timeout 300 \
  -b 0.0.0.0:8000 app.main:app \
  --daemon \
  --pid gunicorn.pid \
  --access-logfile access.log \
  --error-logfile error.log \
  --capture-output

echo "--- waiting for health ---"
HEALTH=""
for i in $(seq 1 20); do
  sleep 2
  HEALTH=$(curl -s --max-time 3 http://127.0.0.1:8000/api/v1/health || true)
  if [ -n "$HEALTH" ]; then echo "$HEALTH"; break; fi
done
if [ -z "$HEALTH" ]; then
  echo "NODE-FAILED-TO-START"
  tail -20 error.log 2>/dev/null || true
  exit 1
fi
echo "--- version ---"
curl -s --max-time 5 http://127.0.0.1:8000/api/v1/version || true
echo
