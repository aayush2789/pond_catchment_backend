#!/bin/bash
# Degrade-mode test on one node: point REDIS_URL at a dead endpoint, restart,
# and verify the analysis pipeline still works (graceful cache degradation).
set -e
cd ~/pond_catchment_backend
sed -i 's|^REDIS_URL=.*|REDIS_URL=redis://172.17.0.110:59999/1|' .env
kill "$(cat gunicorn.pid)" 2>/dev/null || true
sleep 1
nohup ./venv/bin/gunicorn -k uvicorn.workers.UvicornWorker -w 2 --threads 4 \
  --timeout 300 -b 0.0.0.0:8000 app.main:app --daemon --pid gunicorn.pid \
  --error-logfile error.log --capture-output

HEALTH=""
for i in $(seq 1 15); do
  sleep 2
  HEALTH=$(curl -s --max-time 3 http://127.0.0.1:8000/api/v1/ready || true)
  [ -n "$HEALTH" ] && break
done
echo "ready: $HEALTH"

cat > /tmp/pond_body.json <<'EOF'
{"geometry": {"type": "Polygon", "coordinates": [[[81.290, 21.245], [81.296, 21.245], [81.296, 21.250], [81.290, 21.250], [81.290, 21.245]]]}}
EOF
curl -s --max-time 120 -X POST http://127.0.0.1:8000/api/v1/terrainPreview \
  -H 'Content-Type: application/json' -d @/tmp/pond_body.json \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("analysis-with-redis-down OK; cache_hit =", d["dem"]["cache_hit"], "; provider =", d["dem"]["source"]["provider"])'
