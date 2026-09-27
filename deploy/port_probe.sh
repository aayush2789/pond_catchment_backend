#!/bin/bash
# Sample established connections on sys1's HTTP listeners while an external probe hits 3309.
for i in $(seq 1 15); do
  ss -tn state established '( sport = :80 or sport = :3000 or sport = :3309 )' 2>/dev/null | tail -n +2
  sleep 0.3
done | sort | uniq -c | sort -rn | head -10
echo "--- listener check ---"
ss -tln | grep -E ":(80|3000|3309) "
