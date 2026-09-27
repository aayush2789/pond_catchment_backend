#!/bin/bash
# Restore the correct shared-Redis configuration on a node and restart.
set -e
cd ~/pond_catchment_backend
sed -i 's|^REDIS_URL=.*|REDIS_URL=redis://172.17.0.110:6379/1|' .env
kill "$(cat gunicorn.pid)" 2>/dev/null || true
sleep 1
nohup ./venv/bin/gunicorn -k uvicorn.workers.UvicornWorker -w 2 --threads 4 \
  --timeout 300 -b 0.0.0.0:8000 app.main:app --daemon --pid gunicorn.pid \
  --error-logfile error.log --capture-output
for i in $(seq 1 15); do
  sleep 2
  H=$(curl -s --max-time 3 http://127.0.0.1:8000/api/v1/ready || true)
  [ -n "$H" ] && break
done
echo "ready after restore: $H"
