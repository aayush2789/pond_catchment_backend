# Deployment & Architecture — Distributed Pond Planning System

Production-style deployment of the pond-planning backend across four servers with
an Nginx load balancer in front, modeled on the already-proven group-chat
deployment on the same infrastructure.

```
                    Clients (browser / curl)
                              |
                              v
              Nginx load balancer  (sys1, port 3309)
              upstream: round-robin, max_fails=3, fail_timeout=10s
        +----------------+----------------+----------------+
        v                v                v                v
  Pond API sys1    Pond API sys2    Pond API sys3    Pond API sys4
  172.17.0.110     172.17.0.111     172.17.0.112     172.17.0.113
  :8000            :8000            :8000            :8000
        +----------------+----------------+----------------+
        |                (L2 shared cache)                 |
        +-----------------------+--------------------------+
                                v
                  Shared Redis (sys1, :6379, db 1)
                                |
        +-----------------------+--------------------------+
        v                                                   v
  DEM providers (AWS Terrain Tiles /                 Rainfall providers
  OpenTopography fallback)                           (Open-Meteo / NASA POWER)
```

## What was reused from the existing group-chat infrastructure

- **Nginx load-balancer node**: sys1 already runs the group-chat Nginx + a
  dynamic "Performance LB Controller". The pond site is installed as a separate
  file (`/etc/nginx/sites-enabled/pond.conf`) so the controller (which rewrites
  only the chat `default` site) never touches it. Upstream conventions
  (`max_fails=3 fail_timeout=10s`, `keepalive`), proxy headers
  (`X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto`) and the
  `nginx -t` before reload discipline are adopted directly from the chat config.
- **Shared Redis**: the existing Redis 7 instance on sys1 (`:6379`, internal
  trust domain, no auth) is reused. The pond application uses **database 1**
  and versioned key prefixes so it cannot collide with other users of the
  instance. No new Redis instances were created.
- **Process model**: no Docker exists on these nodes (same as group-chat) —
  each node runs the app from a per-node Python venv with a daemonized
  gunicorn, mirroring the chat pattern (`gunicorn --daemon` + pid file).
- **No database**: the chat stack keeps its data in SQLite behind a dedicated
  service. The pond application has no persistent user/project data, so it
  deliberately keeps **no relational database** — analysis results are
  deterministic recomputations, and shared ephemeral data lives in Redis.

## Why the API nodes are stateless

Any request can be routed to any of the four nodes, so no node may own state
that another node needs:

- No sessions, no user data, no request-scoped files that outlive a request.
- Analysis results are **deterministic functions of the request** (selected
  land geometry + parameters) — any node can recompute them from scratch.
- The only cross-request state is the **cache**, and its authoritative level
  (L2) is shared through Redis, not per-process memory.

## Cache design (L1 → L2 → L3 → provider)

| Level | Scope | Used by | Notes |
| :--- | :--- | :--- | :--- |
| L1 | per-process memory (LRU) | DEM + rainfall services | fastest; per node |
| L2 | **shared Redis** (`{node}:6379/1`) | DEM (NPZ bytes + meta JSON) and rainfall (JSON) | TTL-limited; any node serves any cached entry |
| L3 | per-node disk (`data/cache/`) | DEM (NPZ) + rainfall (JSON) | fallback when Redis is unavailable |
| L4 | external provider / computation | AWS Terrain Tiles → OpenTopography; Open-Meteo → NASA POWER | unchanged provider logic and fallbacks |

- **Keys** are deterministic SHA-256 hashes over every result-relevant input:
  DEM keys = provider + dataset + geographic extent (6 decimals) + resolution;
  rainfall keys = rounded coordinates (2 decimals ≈ 1.1 km) + year window.
  The provider stores its identity *inside* the cached payload, so a cached
  result remains valid regardless of which provider produced it.
- **Namespacing**: keys are prefixed `{service}:v{APP_VERSION}:`, so deploying a
  new application version invalidates all previous cache entries — stale
  results cannot survive an algorithm change.
- **TTLs**: `DEM_CACHE_TTL_S` (default 30 days — elevation is static) and
  `RAINFALL_CACHE_TTL_S` (default 7 days). No unlimited entries.
- **Failures are non-fatal**: if Redis is down (or `REDIS_URL` is unset), the
  cache layer logs a warning and every read is a miss; the API computes
  deterministically or uses the provider fallback chain exactly as before. The
  API never returns 500 because a cache is unavailable.

## Health checks, readiness, versioning

- `GET /api/v1/health` — lightweight liveness probe (no DEM/rainfall/hydrology
  work). Nginx proxies it with a short timeout; excluded from access logs.
- `GET /api/v1/ready` — reports `status: ready`, the node id and the Redis
  state (`up` / `down` / `disabled`). Returns 200 as long as the API process
  can serve (Redis loss degrades but does not disable the service).
- `GET /api/v1/version` — safe build metadata only: application version, git
  commit, node id, environment. Used to verify that all four nodes run the
  same deployment. Never exposes secrets or credentials.

Failure handling is inherited from the proven chat mechanism: an upstream is
marked failed after 3 consecutive errors within 10 s (`max_fails=3
fail_timeout=10s`) and rejoins automatically after the timeout. The API also
emits `X-Served-By` (node id) and honours/propagates `X-Request-ID`, so
load-balancing distribution and request tracing are directly observable.

## Observability

The request middleware logs one JSON line per request:
`request_id`, `node`, `method`, `path`, `status`, `latency_ms`
(no payloads, no credentials). Cache hit/miss and provider outcomes are visible
in the per-node service logs. No Prometheus/Grafana stack exists in the
reference infrastructure, so none was added; the JSON logs are ready to be
shipped if one is introduced later.

## Deploying / updating all four nodes

From the repository root (the SSH password is supplied via the
`DEPLOY_SSH_PW` environment variable and is never stored in the repository):

```bash
# one command: bundle + upload + venv + pip install + restart on all 4 nodes
python deploy/deploy_all.py push

# install/update the pond nginx site on the proxy node (sys1) and reload
python deploy/deploy_all.py nginx

# verify every node reports the same commit + the LB endpoint answers
python deploy/deploy_all.py verify
```

Per-node operations:

```bash
python deploy/deploy_all.py stop-node 2310   # simulate a failed backend
python deploy/deploy_all.py start-node 2310  # bring it back
```

Internal layout per node (operational information):

| Node | SSH port | Internal IP | Pond API |
| :--- | :--- | :--- | :--- |
| sys1 | 2309 | 172.17.0.110 | :8000 (+ Nginx :3309, shared Redis :6379) |
| sys2 | 2310 | 172.17.0.111 | :8000 |
| sys3 | 2311 | 172.17.0.112 | :8000 |
| sys4 | 2312 | 172.17.0.113 | :8000 |

## Configuration

All configuration is environment-based (see `.env.example`). Set on every node:
`NODE_ID`, `REDIS_URL`, `APP_ENV`, `GIT_COMMIT`, plus the existing DEM/rainfall
and limit settings. SSH credentials live only in the deployer's environment —
never in the repository, Docker/nginx config, logs, or documentation.
