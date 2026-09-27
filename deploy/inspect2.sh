#!/bin/bash
echo "=== LB CONTROLLER (first 120 lines) ==="
head -120 ~/ONLINE_GROUP_CHAT/performance_lb_controller.py 2>/dev/null
echo "=== CONTROLLER: how it writes nginx ==="
grep -n "sites-enabled\|default\|write\|open(" ~/ONLINE_GROUP_CHAT/performance_lb_controller.py 2>/dev/null | head -30
echo "=== INTERNET TEST ==="
timeout 8 curl -sI https://pypi.org/simple/ 2>&1 | head -3 || echo NO-INTERNET
echo "=== REDIS FROM THIS NODE ==="
timeout 5 redis-cli -h 172.17.0.110 ping 2>&1 || echo REDIS-UNREACHABLE
echo "=== REDIS CONFIG ==="
grep -E "^(bind|protected-mode|port|requirepass|maxmemory|maxmemory-policy|databases)" /etc/redis/redis.conf 2>/dev/null
redis-cli info keyspace 2>/dev/null | head -8
echo "=== PORT 8000 FREE? ==="
ss -tln | grep -q ":8000 " && echo "8000-IN-USE" || echo "8000-FREE"
echo "=== EXISTING POND DIR ==="
ls ~/pond_catchment_backend 2>/dev/null | head -20
echo "=== PIP GLOBAL PKGS (relevant) ==="
pip3 list 2>/dev/null | grep -iE "fastapi|uvicorn|gunicorn|flask|numpy|scipy|shapely|pyproj|pillow|httpx|redis|pydantic" || echo none-relevant
echo "=== CHAT VENV PKGS (relevant) ==="
~/ONLINE_GROUP_CHAT/venv/bin/pip list 2>/dev/null | grep -iE "fastapi|uvicorn|gunicorn|flask|numpy|scipy|redis" | head -10 || echo no-venv
