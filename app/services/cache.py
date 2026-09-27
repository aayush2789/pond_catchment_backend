"""Shared cache layer for the distributed (multi-node) deployment.

Two-level caching used by the DEM and rainfall services:

    L1  per-process in-process cache (existing OrderedDict LRU in each service)
    L2  shared Redis (reachable by all API nodes)  <- this module
    L3  per-node disk cache (existing NPZ/JSON files, fallback when Redis is down)
    L4  external provider / deterministic computation

Design rules (documented):
- Redis is OPTIONAL. REDIS_URL empty or a Redis outage never raises: cache
  misses simply fall through to the next layer. The API must never return 500
  because a cache is unavailable.
- Keys are deterministic and namespaced with the application version so stale
  results cannot survive an algorithm/code change:
      "{namespace}:v{APP_VERSION}:{key}"
- Values are stored per data type: JSON strings for metadata-style payloads and
  raw bytes for binary blobs (e.g. serialized DEM grids).
"""

import json
import logging
import socket
from typing import Any, Optional

from app.core.config import settings

logger = logging.getLogger("pond.cache")

_REDIS_RETRY_INTERVAL_S = 30.0


class CacheLayer:
    """Redis-backed shared cache (L2). All operations are non-fatal."""

    _client: Any = None  # redis.Redis | None
    _client_failed_at: float = 0.0
    _redis_available: bool = False

    @classmethod
    def _get_client(cls):
        if not settings.REDIS_URL:
            return None
        now = __import__("time").monotonic()
        if cls._client is not None:
            return cls._client
        if cls._redis_available is False and (now - cls._client_failed_at) < _REDIS_RETRY_INTERVAL_S:
            return None  # recent failure: don't hammer a down Redis
        try:
            import redis  # imported lazily so the dependency stays optional at import time

            client = redis.Redis.from_url(
                settings.REDIS_URL,
                socket_connect_timeout=1.0,
                socket_timeout=1.5,
                decode_responses=False,
            )
            client.ping()
            cls._client = client
            cls._redis_available = True
            logger.info("Shared Redis cache connected (%s)", settings.REDIS_URL)
            return client
        except Exception as exc:  # noqa: BLE001 — any Redis failure is non-fatal
            cls._client = None
            cls._redis_available = False
            cls._client_failed_at = now
            logger.warning("Redis cache unavailable, continuing without it: %s", exc)
            return None

    @classmethod
    def is_available(cls) -> bool:
        return cls._get_client() is not None

    @staticmethod
    def namespaced(namespace: str, key: str) -> str:
        return f"{namespace}:v{settings.VERSION}:{key}"

    @classmethod
    def get_json(cls, namespace: str, key: str) -> Optional[dict]:
        client = cls._get_client()
        if client is None:
            return None
        try:
            raw = client.get(cls.namespaced(namespace, key))
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis get_json failed (non-fatal): %s", exc)
            return None

    @classmethod
    def set_json(cls, namespace: str, key: str, value: dict, ttl_s: int) -> bool:
        client = cls._get_client()
        if client is None:
            return False
        try:
            client.set(cls.namespaced(namespace, key), json.dumps(value), ex=ttl_s)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis set_json failed (non-fatal): %s", exc)
            return False

    @classmethod
    def get_bytes(cls, namespace: str, key: str) -> Optional[bytes]:
        client = cls._get_client()
        if client is None:
            return None
        try:
            return client.get(cls.namespaced(namespace, key))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis get_bytes failed (non-fatal): %s", exc)
            return None

    @classmethod
    def set_bytes(cls, namespace: str, key: str, value: bytes, ttl_s: int) -> bool:
        client = cls._get_client()
        if client is None:
            return False
        try:
            client.set(cls.namespaced(namespace, key), value, ex=ttl_s)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis set_bytes failed (non-fatal): %s", exc)
            return False

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._client = None
        cls._redis_available = False
        cls._client_failed_at = 0.0


def default_node_id() -> str:
    """Node identifier for logs/diagnostics (hostname unless configured)."""
    return settings.NODE_ID or socket.gethostname()
