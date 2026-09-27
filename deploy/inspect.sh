#!/bin/bash
# Read-only infrastructure inspection for the pond deployment phase.
echo "=== HOST ==="
hostname; ip -4 addr show eth0 2>/dev/null | grep inet
echo "=== HOME ==="
ls ~ 2>/dev/null
echo "=== NGINX SITES ==="
ls /etc/nginx/sites-enabled/ 2>/dev/null; ls /etc/nginx/conf.d/ 2>/dev/null
for f in /etc/nginx/sites-enabled/* /etc/nginx/conf.d/*; do
  [ -f "$f" ] && { echo "== $f"; cat "$f"; }
done
echo "=== RUNNING SERVICES ==="
systemctl list-units --type=service --state=running --no-pager --no-legend 2>/dev/null | grep -Ev 'systemd|dbus|ssh|cron|getty|journal|network|resolved|logind|user@|multipath|udev|packagekit|polkit|rsyslog|unattended' || true
echo "=== LISTEN PORTS ==="
ss -tlnp 2>/dev/null | head -25
echo "=== RELEVANT PROCESSES ==="
ps aux | grep -E 'gunicorn|uvicorn|node |redis|postgres|mysql|python' | grep -v grep | head -15
echo "=== REDIS ==="
redis-cli ping 2>/dev/null || echo redis-cli-failed
echo "=== TOOLING ==="
command -v docker >/dev/null && echo has-docker || echo no-docker
command -v psql >/dev/null && psql --version || echo no-psql
command -v mysql >/dev/null && echo has-mysql || echo no-mysql
python3 --version
pip3 --version 2>/dev/null | head -1
echo "=== SUDO ==="
sudo -n true 2>/dev/null && echo sudo-passwordless || echo sudo-needs-password
