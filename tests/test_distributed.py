"""Tests for the distributed-deployment behavior (shared cache, readiness,
version endpoint, graceful Redis degradation). No real external APIs are used.
"""

import pytest
from fastapi import HTTPException, status

from app.core.config import settings
from app.services.cache import CacheLayer
from app.services.dem import DEMService, GeographicExtent, DEMData, DEMSourceInfo
from app.services.rainfall import RainfallService


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    CacheLayer.reset_for_tests()
    RainfallService._memory_cache.clear()
    DEMService._memory_cache.clear()
    monkeypatch.setattr(settings, "RAINFALL_CACHE_DIR", str(tmp_path / "rain"))
    monkeypatch.setattr(settings, "DEM_CACHE_DIR", str(tmp_path / "dem"))
    yield
    CacheLayer.reset_for_tests()
    RainfallService._memory_cache.clear()
    DEMService._memory_cache.clear()


class FakeSharedRedis:
    """In-memory stand-in for Redis, shared across simulated nodes."""

    store: dict = {}

    @classmethod
    def reset(cls):
        cls.store = {}

    @classmethod
    def get_json(cls, namespace, key):
        return cls.store.get((namespace, key))

    @classmethod
    def set_json(cls, namespace, key, value, ttl_s):
        cls.store[(namespace, key)] = value
        return True

    @classmethod
    def get_bytes(cls, namespace, key):
        return cls.store.get((namespace, key))

    @classmethod
    def set_bytes(cls, namespace, key, value, ttl_s):
        cls.store[(namespace, key)] = value
        return True


# --- Health / readiness / version ------------------------------------------------------


def test_health_remains_lightweight(client):
    response = client.get("/api/v1/health")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "healthy"


def test_ready_reports_node_and_redis_state(client, monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "")  # disabled
    response = client.get("/api/v1/ready")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "ready"
    assert data["redis"] == "disabled"
    assert data["node"]


def test_ready_when_redis_down_still_ready(client, monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:59999/0")
    monkeypatch.setattr(CacheLayer, "_client_failed_at", 0.0)
    response = client.get("/api/v1/ready")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["redis"] in ("down", "disabled")


def test_version_exposes_safe_metadata_only(client, monkeypatch):
    monkeypatch.setattr(settings, "GIT_COMMIT", "abc1234")
    monkeypatch.setattr(settings, "NODE_ID", "test-node")
    response = client.get("/api/v1/version")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["git_commit"] == "abc1234"
    assert data["node"] == "test-node"
    assert data["version"] == settings.VERSION
    blob = str(data)
    # No secrets or credential-like keys are exposed.
    for forbidden in ("password", "secret", "key", "token", "ssh"):
        assert forbidden not in blob.lower()


def test_request_id_and_node_headers(client):
    response = client.get("/api/v1/health", headers={"X-Request-ID": "test-req-42"})
    assert response.headers["X-Request-ID"] == "test-req-42"
    assert response.headers["X-Served-By"]


# --- Cache layer: determinism, namespacing, graceful degradation -----------------------


def test_cache_key_namespacing_includes_version():
    CacheLayer.reset_for_tests()
    assert CacheLayer.namespaced("rainfall", "abc") == f"rainfall:v{settings.VERSION}:abc"


def test_cache_operations_non_fatal_without_redis(monkeypatch):
    CacheLayer.reset_for_tests()
    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:59999/0")
    monkeypatch.setattr(CacheLayer, "_client_failed_at", 0.0)
    assert CacheLayer.get_json("ns", "k") is None
    assert CacheLayer.set_json("ns", "k", {"a": 1}, 60) is False
    assert CacheLayer.get_bytes("ns", "k") is None
    assert CacheLayer.set_bytes("ns", "k", b"x", 60) is False


def test_cache_roundtrip_through_shared_layer(monkeypatch):
    FakeSharedRedis.reset()
    monkeypatch.setattr(
        CacheLayer, "set_json", classmethod(lambda cls, ns, k, v, ttl: FakeSharedRedis.set_json(ns, k, v, ttl))
    )
    monkeypatch.setattr(
        CacheLayer, "get_json", classmethod(lambda cls, ns, k: FakeSharedRedis.get_json(ns, k))
    )
    assert CacheLayer.set_json("ns", "k", {"x": 1}, 60) is True
    assert CacheLayer.get_json("ns", "k") == {"x": 1}


# --- Rainfall: shared-cache semantics + fallback preservation --------------------------


OPEN_METEO_PAYLOAD = {
    "daily": {
        "time": ["2020-01-01", "2020-06-15", "2021-01-01"],
        "precipitation_sum": [10.0, 100.0, 5.0],
    }
}


def _fake_http(payload):
    return lambda url, params: payload


def test_rainfall_shared_across_nodes_without_local_state(monkeypatch, tmp_path):
    """Node A fetches; node B (fresh memory cache, fresh disk dir) hits the shared cache."""
    FakeSharedRedis.reset()
    monkeypatch.setattr(
        RainfallService, "_http_get_json", staticmethod(_fake_http(OPEN_METEO_PAYLOAD))
    )
    monkeypatch.setattr(CacheLayer, "get_json", classmethod(lambda cls, ns, k: FakeSharedRedis.get_json(ns, k)))
    monkeypatch.setattr(
        CacheLayer, "set_json", classmethod(lambda cls, ns, k, v, ttl: FakeSharedRedis.set_json(ns, k, v, ttl))
    )
    # Node A
    node_a = RainfallService.get_rainfall(21.25, 81.29)
    assert node_a.cache_hit is False
    # Node B: same Redis, empty local memory, empty local disk dir.
    RainfallService._memory_cache.clear()
    monkeypatch.setattr(settings, "RAINFALL_CACHE_DIR", str(tmp_path / "node_b_rain"))
    node_b = RainfallService.get_rainfall(21.25, 81.29)
    assert node_b.cache_hit is True
    assert node_b.rainfall_mm == node_a.rainfall_mm
    assert node_b.source == node_a.source


def test_rainfall_works_when_redis_down(monkeypatch):
    """Redis outage must never make the API fail; provider path still returns data."""
    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:59999/0")
    monkeypatch.setattr(CacheLayer, "_client_failed_at", 0.0)
    monkeypatch.setattr(
        RainfallService, "_http_get_json", staticmethod(_fake_http(OPEN_METEO_PAYLOAD))
    )
    result = RainfallService.get_rainfall(21.30, 81.30)
    assert result.cache_hit is False
    assert result.rainfall_mm > 0


def test_rainfall_provider_fallback_preserved(monkeypatch):
    """Open-Meteo primary fails -> NASA POWER fallback still answers (Redis down)."""
    calls = []

    def fake_get(url, params):
        calls.append(url)
        if "open-meteo" in url:
            raise HTTPException(status_code=502, detail="primary down")
        return {
            "properties": {
                "parameter": {"PRECTOTCORR": {"ANN": 3.0, **{m: 1.0 for m in [
                    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]}}}
            }
        }

    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:59999/0")
    monkeypatch.setattr(CacheLayer, "_client_failed_at", 0.0)
    monkeypatch.setattr(RainfallService, "_http_get_json", staticmethod(fake_get))
    result = RainfallService.get_rainfall(22.0, 81.0)
    assert result.source == "nasa-power"
    assert len([c for c in calls if "open-meteo" in c]) >= 1


# --- DEM: shared-cache semantics --------------------------------------------------------


def _make_dem():
    return DEMData(
        elevation_grid=__import__("numpy").zeros((4, 4), dtype="float32") + 5.0,
        crs="EPSG:32644",
        resolution_meters=30.0,
        bounds=(529000.0, 530000.0, 2348000.0, 2349000.0),
        geographic_extent=GeographicExtent(
            min_longitude=81.2, max_longitude=81.3, min_latitude=21.2, max_latitude=21.3
        ),
        source=DEMSourceInfo(provider="fake", dataset="fake", attribution="test"),
    )


def test_dem_cache_roundtrip_serialization():
    dem = _make_dem()
    grid_bytes, meta_json = DEMService._serialize_dem(dem)
    restored = DEMService._deserialize_dem(grid_bytes, meta_json, cache_hit=True)
    assert restored is not None
    assert restored.crs == dem.crs
    assert restored.elevation_grid.shape == dem.elevation_grid.shape
    assert restored.source.provider == dem.source.provider
    assert restored.cache_hit is True


def test_dem_shared_across_nodes(monkeypatch, tmp_path):
    FakeSharedRedis.reset()
    monkeypatch.setattr(
        CacheLayer, "get_bytes", classmethod(lambda cls, ns, k: FakeSharedRedis.get_bytes(ns, k))
    )
    monkeypatch.setattr(
        CacheLayer, "set_bytes", classmethod(lambda cls, ns, k, v, ttl: FakeSharedRedis.set_bytes(ns, k, v, ttl))
    )
    monkeypatch.setattr(
        CacheLayer, "get_json", classmethod(lambda cls, ns, k: FakeSharedRedis.get_json(ns, k))
    )
    monkeypatch.setattr(
        CacheLayer, "set_json", classmethod(lambda cls, ns, k, v, ttl: FakeSharedRedis.set_json(ns, k, v, ttl))
    )

    class SameResultProvider:
        name = "fake"
        dataset = "fake"
        attribution = "test"
        calls = 0

        def sample(self, extent, lon, lat, target_resolution_m):
            SameResultProvider.calls += 1
            import numpy as np

            return ((lon - 81.0) * 100.0).astype("float32")

    provider = SameResultProvider()
    monkeypatch.setattr(DEMService, "_provider_chain", staticmethod(lambda: [provider]))

    extent = GeographicExtent(
        min_longitude=81.29, max_longitude=81.30, min_latitude=21.24, max_latitude=21.25
    )
    # Node A acquires (calls provider).
    node_a = DEMService.acquire_dem(extent, target_resolution_m=30.0)
    assert node_a.cache_hit is False
    assert SameResultProvider.calls == 1
    # Node B: fresh memory cache, fresh disk dir -> served from the SHARED cache.
    DEMService._memory_cache.clear()
    monkeypatch.setattr(settings, "DEM_CACHE_DIR", str(tmp_path / "node_b_dem"))
    node_b = DEMService.acquire_dem(extent, target_resolution_m=30.0)
    assert node_b.cache_hit is True
    assert SameResultProvider.calls == 1  # provider NOT called again
    assert node_b.elevation_grid.shape == node_a.elevation_grid.shape
