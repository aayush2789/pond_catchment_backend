import numpy as np
import pytest
from fastapi import HTTPException, status

from app.schemas.catchment import GeographicExtent
from app.services.contours import MAX_CONTOUR_LEVELS, ContourGenerationService
from app.services.dem import DEMService
from app.services.terrain import DEMData, DEMSourceInfo, TerrainModel, TerrainService

# Realistic UTM zone 44N coordinates so WGS84 conversion is well defined.
UTM_BOUNDS = (529_000.0, 530_780.0, 2_348_000.0, 2_349_780.0)  # (min_x, max_x, min_y, max_y)
GEO_EXTENT = GeographicExtent(
    min_longitude=81.28, max_longitude=81.30, min_latitude=21.23, max_latitude=21.25
)


def cone_terrain(rows=60, cols=60, resolution=30.0) -> TerrainModel:
    """Deterministic conical hill: elevation decreases away from the grid center."""
    cy, cx = (rows - 1) / 2.0, (cols - 1) / 2.0
    yy, xx = np.mgrid[0:rows, 0:cols]
    dist = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
    grid = 50.0 - dist * 0.5
    return TerrainModel(
        elevation_grid=grid,
        crs="EPSG:32644",
        grid_resolution_meters=resolution,
        bounds=UTM_BOUNDS,
        geographic_extent=GEO_EXTENT,
        min_elevation=float(grid.min()),
        max_elevation=float(grid.max()),
        slope_grid=TerrainService.calculate_slope(grid, resolution),
    )


def make_dem_data(grid: np.ndarray) -> DEMData:
    return DEMData(
        elevation_grid=grid.astype(np.float32),
        crs="EPSG:32644",
        resolution_meters=30.0,
        bounds=UTM_BOUNDS,
        geographic_extent=GEO_EXTENT,
        source=DEMSourceInfo(provider="fake", dataset="fake", attribution="test"),
    )


# --- Contour levels --------------------------------------------------------------------


def test_contour_levels_aligned_to_interval():
    levels = ContourGenerationService.contour_levels(267.0, 298.0, 5.0)
    assert levels[0] == 270.0  # ceil(267 / 5) * 5
    assert levels == [270.0, 275.0, 280.0, 285.0, 290.0, 295.0]


def test_contour_levels_capped_for_large_relief():
    levels = ContourGenerationService.contour_levels(0.0, 1000.0, 1.0)
    assert len(levels) <= MAX_CONTOUR_LEVELS
    assert levels[0] >= 0.0 and levels[-1] <= 1000.0


def test_contour_levels_rejects_sub_resolution_interval():
    with pytest.raises(HTTPException) as exc:
        ContourGenerationService.contour_levels(0.0, 50.0, 0.1)
    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST


# --- Contour generation ----------------------------------------------------------------


def test_generate_contours_on_cone_produces_closed_rings():
    terrain = cone_terrain()
    collection = ContourGenerationService.generate_contours(terrain, interval=5.0)

    assert collection["type"] == "FeatureCollection"
    features = collection["features"]
    assert len(features) > 0
    assert collection["properties"]["contour_count"] == len(features)

    elevations = {f["properties"]["elevation_m"] for f in features}
    assert all(lvl in elevations for lvl in (45.0, 40.0, 30.0))

    for feature in features:
        assert feature["type"] == "Feature"
        assert feature["geometry"]["type"] == "LineString"
        coords = feature["geometry"]["coordinates"]
        assert len(coords) >= 2
        for lon, lat in coords:
            assert GEO_EXTENT.min_longitude - 0.01 <= lon <= GEO_EXTENT.max_longitude + 0.01
            assert GEO_EXTENT.min_latitude - 0.01 <= lat <= GEO_EXTENT.max_latitude + 0.01

    # Rings fully inside the grid must be closed (intermost cone contours).
    closed = [
        f for f in features if f["geometry"]["coordinates"][0] == f["geometry"]["coordinates"][-1]
    ]
    assert len(closed) >= 1


def test_generate_contours_flat_grid_returns_empty_collection():
    terrain = cone_terrain()
    terrain.elevation_grid = np.full_like(terrain.elevation_grid, 42.0)
    collection = ContourGenerationService.generate_contours(terrain, interval=5.0)
    assert collection["features"] == []


# --- DEM -> TerrainModel bridge (Phase 2B) ----------------------------------------------


def test_reconstruct_terrain_from_dem():
    grid = cone_terrain().elevation_grid
    dem = make_dem_data(grid)
    terrain = TerrainService.reconstruct_terrain_from_dem(dem)
    assert isinstance(terrain, TerrainModel)
    assert terrain.crs == "EPSG:32644"
    assert terrain.grid_resolution_meters == 30.0
    assert terrain.elevation_grid.shape == grid.shape
    assert terrain.min_elevation == pytest.approx(float(grid.min()))
    assert terrain.max_elevation == pytest.approx(float(grid.max()))
    assert terrain.slope_grid is not None
    assert terrain.slope_grid.shape == grid.shape
    assert float(terrain.slope_grid.max()) > 0.0


def test_reconstruct_terrain_from_dem_rejects_tiny_grid():
    dem = make_dem_data(np.ones((1, 3), dtype=np.float32))
    with pytest.raises(HTTPException) as exc:
        TerrainService.reconstruct_terrain_from_dem(dem)
    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST


# --- Endpoint integration (fake provider, no network) -----------------------------------


def test_terrain_preview_includes_terrain_and_contours(client, monkeypatch):
    from tests.test_dem import FakeProvider

    fake = FakeProvider()
    monkeypatch.setattr(DEMService, "_provider_chain", staticmethod(lambda: [fake]))
    response = client.post(
        "/api/v1/terrainPreview",
        json={
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
        },
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    assert data["terrain"] is not None
    terrain = data["terrain"]["terrain"]
    assert terrain["slope"] is not None
    assert terrain["rows"] > 0 and terrain["cols"] > 0

    contours = data["terrain"]["contours"]
    assert contours is not None
    assert contours["type"] == "FeatureCollection"
    assert contours["properties"]["contour_count"] == len(contours["features"])


def test_terrain_preview_can_skip_contours(client, monkeypatch):
    from tests.test_dem import FakeProvider

    fake = FakeProvider()
    monkeypatch.setattr(DEMService, "_provider_chain", staticmethod(lambda: [fake]))
    response = client.post(
        "/api/v1/terrainPreview",
        json={
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
            },
            "include_contours": False,
        },
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["terrain"]["contours"] is None
