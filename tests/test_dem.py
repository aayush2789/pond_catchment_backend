import os

import numpy as np
import pytest
from fastapi import HTTPException, status

from app.core.config import settings
from app.schemas.catchment import GeographicExtent
from app.services.dem import (
    AWSTerrainTilesProvider,
    DEMData,
    DEMProvider,
    DEMService,
    OpenTopographyProvider,
    bilinear_sample,
)
from app.main import app  # noqa: F401  (ensures routers are registered)


# --- Helpers ---------------------------------------------------------------------------


class FakeProvider(DEMProvider):
    """Deterministic tilted-plane elevation source (rises with lon and lat)."""

    name = "fake"
    dataset = "fake_grid"
    attribution = "test attribution"

    def __init__(self, fail=False, nan_stride=0):
        self.calls = 0
        self.fail = fail
        self.nan_stride = nan_stride

    def sample(self, extent, lon, lat, target_resolution_m):
        self.calls += 1
        if self.fail:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="fake provider outage",
            )
        # Tilted plane relative to the analysis area (keeps elevations plausible).
        elev = ((lon - 81.0) * 100.0 + (lat - 21.0) * 50.0).astype(np.float32)
        if self.nan_stride > 0:
            elev[:: self.nan_stride] = np.nan
        return elev


class FailingProvider(DEMProvider):
    name = "failing"
    dataset = "failing"
    attribution = "x"

    def sample(self, extent, lon, lat, target_resolution_m):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="outage")


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    DEMService._memory_cache.clear()
    monkeypatch.setattr(settings, "DEM_CACHE_DIR", str(tmp_path / "dem_cache"))
    yield
    DEMService._memory_cache.clear()


SMALL_EXTENT = GeographicExtent(
    min_longitude=81.29,
    max_longitude=81.30,
    min_latitude=21.24,
    max_latitude=21.25,
)


# --- Unit tests ------------------------------------------------------------------------


def test_compute_analysis_extent_expands_bbox():
    extent = DEMService.compute_analysis_extent(
        GeographicExtent(
            min_longitude=81.29,
            max_longitude=81.30,
            min_latitude=21.24,
            max_latitude=21.25,
        ),
        500.0,
    )
    # At lat ~21.245: 500 m ~ 0.00452 deg lat and ~0.00484 deg lon.
    assert extent.min_latitude < 21.24
    assert extent.max_latitude > 21.25
    assert extent.min_longitude < 81.29
    assert extent.max_longitude > 81.30
    d_lat = 21.24 - extent.min_latitude
    assert abs(d_lat - 500.0 / 110_574.0) < 0.0005
    d_lon = 81.29 - extent.min_longitude
    assert abs(d_lon - 500.0 / (111_320.0 * 0.9323)) < 0.0005


def test_zoom_selection_matches_target_resolution():
    provider = AWSTerrainTilesProvider(timeout_s=10, max_tiles=64)
    # At lat 21.2: z12 ~35.6 m/px (>30), z13 ~17.8 m/px (<=30).
    assert provider._select_zoom(30.0, 21.2) == 13
    assert provider._select_zoom(10.0, 21.2) == 14
    assert provider._select_zoom(100.0, 21.2) == 11


def test_bilinear_sample_center_value():
    grid = np.array([[0.0, 10.0], [20.0, 30.0]], dtype=np.float32)
    value = bilinear_sample(grid, np.array([0.5]), np.array([0.5]))
    assert value[0] == pytest.approx(15.0)


def test_bilinear_sample_clamps_out_of_range():
    grid = np.array([[5.0, 7.0]], dtype=np.float32)
    # Row coordinate clamps to the single row; column clamps at the edges.
    value = bilinear_sample(grid, np.array([-3.0, 9.0]), np.array([0.0, 1.0]))
    assert value[0] == pytest.approx(5.0)
    assert value[1] == pytest.approx(7.0)


def test_aai_grid_parsing_and_sampling():
    provider = OpenTopographyProvider(
        api_key="test-key", dataset="SRTMGL1", timeout_s=10, max_response_mb=64
    )
    aai_text = (
        "ncols         3\n"
        "nrows         2\n"
        "xllcorner     81.0\n"
        "yllcorner     21.0\n"
        "cellsize      0.01\n"
        "NODATA_value  -9999\n"
        "10 20 30\n"
        "40 50 60\n"
    )
    provider._parse(aai_text)
    # Row 0 is the northernmost row. (81.01, 21.015) -> column 1 exactly,
    # halfway between the two rows: (20 + 50) / 2 = 35.
    value = provider.sample(SMALL_EXTENT, np.array([81.01]), np.array([21.015]), 30.0)
    assert value[0] == pytest.approx(35.0, abs=0.01)


def test_provider_chain_aws_only_without_key(monkeypatch):
    monkeypatch.setattr(settings, "DEM_PROVIDER", "aws_terrain_tiles")
    monkeypatch.setattr(settings, "OPEN_TOPOGRAPHY_API_KEY", "")
    chain = DEMService._provider_chain()
    assert [p.name for p in chain] == ["aws_terrain_tiles"]


def test_provider_chain_includes_opentopography_with_key(monkeypatch):
    monkeypatch.setattr(settings, "DEM_PROVIDER", "aws_terrain_tiles")
    monkeypatch.setattr(settings, "OPEN_TOPOGRAPHY_API_KEY", "test-key")
    chain = DEMService._provider_chain()
    assert [p.name for p in chain] == ["aws_terrain_tiles", "opentopography"]


def test_provider_chain_unknown_provider_rejected(monkeypatch):
    monkeypatch.setattr(settings, "DEM_PROVIDER", "not_a_provider")
    with pytest.raises(HTTPException) as exc:
        DEMService._provider_chain()
    assert exc.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


# --- Service tests (fake provider, no network) -----------------------------------------


def test_acquire_dem_samples_provider_grid(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(DEMService, "_provider_chain", staticmethod(lambda: [fake]))
    dem = DEMService.acquire_dem(SMALL_EXTENT, target_resolution_m=30.0)
    assert isinstance(dem, DEMData)
    assert dem.rows >= 2 and dem.cols >= 2
    assert dem.crs.startswith("EPSG:")
    assert dem.resolution_meters == 30.0
    assert dem.cache_hit is False
    assert fake.calls == 1
    # The tilted plane rises towards north-east: max > min, both finite.
    assert np.isfinite(dem.elevation_grid).all()
    assert dem.elevation_grid.max() > dem.elevation_grid.min()


def test_acquire_dem_caches_identical_requests(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(DEMService, "_provider_chain", staticmethod(lambda: [fake]))
    first = DEMService.acquire_dem(SMALL_EXTENT, target_resolution_m=30.0)
    second = DEMService.acquire_dem(SMALL_EXTENT, target_resolution_m=30.0)
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert fake.calls == 1
    assert np.array_equal(first.elevation_grid, second.elevation_grid)


def test_acquire_dem_different_resolution_bypasses_cache(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(DEMService, "_provider_chain", staticmethod(lambda: [fake]))
    DEMService.acquire_dem(SMALL_EXTENT, target_resolution_m=30.0)
    DEMService.acquire_dem(SMALL_EXTENT, target_resolution_m=50.0)
    assert fake.calls == 2


def test_acquire_dem_falls_back_to_second_provider(monkeypatch):
    failing = FailingProvider()
    fake = FakeProvider()
    monkeypatch.setattr(
        DEMService, "_provider_chain", staticmethod(lambda: [failing, fake])
    )
    dem = DEMService.acquire_dem(SMALL_EXTENT, target_resolution_m=30.0)
    assert dem.source.provider == "fake"
    assert fake.calls == 1


def test_acquire_dem_all_providers_failing_raises(monkeypatch):
    failing = FailingProvider()
    monkeypatch.setattr(DEMService, "_provider_chain", staticmethod(lambda: [failing]))
    with pytest.raises(HTTPException) as exc:
        DEMService.acquire_dem(SMALL_EXTENT, target_resolution_m=30.0)
    assert exc.value.status_code == status.HTTP_502_BAD_GATEWAY


def test_acquire_dem_rejects_oversized_extent(monkeypatch):
    monkeypatch.setattr(settings, "DEM_MAX_EXTENT_KM", 1.0)
    fake = FakeProvider()
    monkeypatch.setattr(DEMService, "_provider_chain", staticmethod(lambda: [fake]))
    with pytest.raises(HTTPException) as exc:
        DEMService.acquire_dem(SMALL_EXTENT, target_resolution_m=30.0)
    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "exceeds" in exc.value.detail
    assert fake.calls == 0


def test_acquire_dem_fills_nodata_and_reports_count(monkeypatch):
    fake = FakeProvider(nan_stride=7)
    monkeypatch.setattr(DEMService, "_provider_chain", staticmethod(lambda: [fake]))
    dem = DEMService.acquire_dem(SMALL_EXTENT, target_resolution_m=30.0)
    assert np.isfinite(dem.elevation_grid).all()
    assert dem.nodata_cells_filled > 0


def test_acquire_dem_rejects_mostly_invalid_data(monkeypatch):
    fake = FakeProvider(nan_stride=1)  # every cell void
    monkeypatch.setattr(DEMService, "_provider_chain", staticmethod(lambda: [fake]))
    with pytest.raises(HTTPException) as exc:
        DEMService.acquire_dem(SMALL_EXTENT, target_resolution_m=30.0)
    assert exc.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# --- API tests (fake provider, no network) ---------------------------------------------

LAND_POLYGON = {
    "geometry": {
        "type": "Polygon",
        "coordinates": [
            [
                [81.290, 21.245],
                [81.296, 21.245],
                [81.296, 21.250],
                [81.290, 21.250],
                [81.290, 21.245],
            ]
        ],
    }
}


def test_terrain_preview_endpoint_success(client, monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(DEMService, "_provider_chain", staticmethod(lambda: [fake]))
    response = client.post("/api/v1/terrainPreview", json=LAND_POLYGON)
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["status"] == "success"
    assert data["buffer_meters"] == 500.0
    extent = data["analysis_extent"]
    assert extent["min_longitude"] < LAND_POLYGON["geometry"]["coordinates"][0][0][0]

    dem = data["dem"]
    assert dem["source"]["provider"] == "fake"
    assert dem["rows"] > 0 and dem["cols"] > 0
    assert dem["resolution_meters"] == 30.0
    assert dem["cache_hit"] is False
    assert dem["crs"].startswith("EPSG:")
    assert dem["projected_bounds"]["min_x"] < dem["projected_bounds"]["max_x"]
    assert dem["source"]["attribution"]


def test_terrain_preview_endpoint_cache_hit_on_repeat(client, monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(DEMService, "_provider_chain", staticmethod(lambda: [fake]))
    first = client.post("/api/v1/terrainPreview", json=LAND_POLYGON)
    second = client.post("/api/v1/terrainPreview", json=LAND_POLYGON)
    assert first.status_code == status.HTTP_200_OK
    assert second.status_code == status.HTTP_200_OK
    assert first.json()["dem"]["cache_hit"] is False
    assert second.json()["dem"]["cache_hit"] is True
    assert fake.calls == 1


def test_terrain_preview_endpoint_honors_parameter_overrides(client, monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(DEMService, "_provider_chain", staticmethod(lambda: [fake]))
    payload = dict(LAND_POLYGON, buffer_meters=1000.0, resolution_meters=50.0)
    response = client.post("/api/v1/terrainPreview", json=payload)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["buffer_meters"] == 1000.0
    assert data["dem"]["resolution_meters"] == 50.0


def test_terrain_preview_endpoint_rejects_out_of_range_buffer(client, monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(DEMService, "_provider_chain", staticmethod(lambda: [fake]))
    payload = dict(LAND_POLYGON, buffer_meters=99999.0)
    response = client.post("/api/v1/terrainPreview", json=payload)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_terrain_preview_endpoint_rejects_oversized_analysis_extent(client, monkeypatch):
    monkeypatch.setattr(settings, "DEM_MAX_EXTENT_KM", 1.0)
    fake = FakeProvider()
    monkeypatch.setattr(DEMService, "_provider_chain", staticmethod(lambda: [fake]))
    response = client.post("/api/v1/terrainPreview", json=LAND_POLYGON)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "exceeds" in response.json()["detail"]


def test_terrain_preview_endpoint_invalid_geometry(client):
    response = client.post(
        "/api/v1/terrainPreview",
        json={"geometry": {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]]}},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


# --- Opt-in live test (real network) ---------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_DEM_TESTS") != "1",
    reason="Live DEM test requires network access; set RUN_LIVE_DEM_TESTS=1 to enable.",
)
def test_live_aws_terrain_tiles_acquire(client):
    live_extent = GeographicExtent(
        min_longitude=81.28,
        max_longitude=81.32,
        min_latitude=21.24,
        max_latitude=21.26,
    )
    dem = DEMService.acquire_dem(live_extent, target_resolution_m=30.0)
    assert dem.rows >= 10 and dem.cols >= 10
    assert dem.source.provider == "aws_terrain_tiles"
    assert np.isfinite(dem.elevation_grid).all()
    assert 0.0 <= float(dem.elevation_grid.mean()) <= 3000.0
