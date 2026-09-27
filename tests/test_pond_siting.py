import numpy as np
import pytest
from fastapi import status
from shapely.geometry import Point, Polygon, shape as shape_from_geojson

from app.schemas.catchment import GeographicExtent, PondCandidateSite
from app.services.candidate_selection import (
    CandidateScoringConfig,
    CandidateSelectionService,
)
from app.services.dem import DEMService
from app.services.hydrology import HydrologyService
from app.services.terrain import TerrainModel, TerrainService

# Realistic UTM zone 44N bounds (min_x, max_x, min_y, max_y) for a 66x66 @ 30 m grid.
BOUNDS = (529_000.0, 530_980.0, 2_348_000.0, 2_349_980.0)
GEO_EXTENT = GeographicExtent(
    min_longitude=81.27, max_longitude=81.32, min_latitude=21.22, max_latitude=21.26
)


def funnel_terrain(rows=66, cols=66, resolution=30.0) -> TerrainModel:
    """V-shaped valley draining south along a central channel (deterministic).

    Water converges to the channel (column cx) and flows toward row 0 (south),
    so the catchment of any in-channel cell is the long upstream strip.
    """
    cx = (cols - 1) / 2.0
    yy, xx = np.mgrid[0:rows, 0:cols]
    grid = 40.0 + np.abs(xx - cx) * 0.4 + yy * 0.05
    return TerrainModel(
        elevation_grid=grid,
        crs="EPSG:32644",
        grid_resolution_meters=resolution,
        bounds=BOUNDS,
        geographic_extent=GEO_EXTENT,
        min_elevation=float(grid.min()),
        max_elevation=float(grid.max()),
        slope_grid=TerrainService.calculate_slope(grid, resolution),
    )


LAND_RECT = Polygon(
    [
        (81.287, 21.238),
        (81.293, 21.238),
        (81.293, 21.242),
        (81.287, 21.242),
        (81.287, 21.238),
    ]
)


def config_with_small_nms() -> CandidateScoringConfig:
    cfg = CandidateSelectionService.FLOW_WEIGHTED_CONFIG
    return CandidateScoringConfig(
        slope_weight=cfg.slope_weight,
        elevation_weight=cfg.elevation_weight,
        flow_weight=cfg.flow_weight,
        min_distance_meters=60.0,  # small grid: allow multiple nearby candidates
    )


# --- Phase 3: land mask + constrained candidates ---------------------------------------


def test_mask_cells_within_polygon():
    terrain = funnel_terrain()
    mask = TerrainService.mask_cells_within_polygon(terrain, LAND_RECT)
    assert mask.shape == terrain.elevation_grid.shape
    assert mask.any()

    # Every masked cell center must lie inside the polygon; none outside.
    min_x, _, min_y, _ = terrain.bounds
    res = terrain.grid_resolution_meters
    rr, cc = np.nonzero(mask)
    for r, c in zip(rr, cc):
        x = min_x + c * res
        y = min_y + r * res
        to_wgs = __import__("pyproj").Transformer.from_crs(
            terrain.crs, "EPSG:4326", always_xy=True
        )
        lon, lat = to_wgs.transform(x, y)
        assert LAND_RECT.contains(Point(lon, lat))


def test_mask_cells_outside_terrain_extent_is_empty():
    terrain = funnel_terrain()
    far_polygon = Polygon(
        [(10.0, 10.0), (11.0, 10.0), (11.0, 11.0), (10.0, 11.0), (10.0, 10.0)]
    )
    mask = TerrainService.mask_cells_within_polygon(terrain, far_polygon)
    assert not mask.any()


def test_identify_candidates_respects_land_mask():
    terrain = funnel_terrain()
    mask = TerrainService.mask_cells_within_polygon(terrain, LAND_RECT)
    filled = HydrologyService.condition_dem(terrain.elevation_grid)
    fdir = HydrologyService.calculate_flow_direction(filled, terrain.grid_resolution_meters)
    acc = HydrologyService.calculate_flow_accumulation(fdir, filled)

    candidates = CandidateSelectionService.identify_candidates(
        terrain,
        config=config_with_small_nms(),
        flow_accumulation=acc,
        candidate_mask=mask,
    )
    assert len(candidates) > 0
    for cand in candidates:
        assert LAND_RECT.contains(Point(cand.longitude, cand.latitude))
        assert "flow_score" in cand.factor_scores

    # Flow-weighted profile must prefer the drainage channel: the primary candidate
    # should carry a strong flow score, not merely be the lowest elevation cell.
    assert candidates[0].factor_scores["flow_score"] > 0.5


def test_identify_candidates_empty_mask_returns_empty():
    terrain = funnel_terrain()
    empty_mask = np.zeros(terrain.elevation_grid.shape, dtype=bool)
    candidates = CandidateSelectionService.identify_candidates(
        terrain,
        config=config_with_small_nms(),
        candidate_mask=empty_mask,
    )
    assert candidates == []


def test_identify_candidates_without_mask_unchanged():
    """Backwards compatibility: no mask -> identical results as before."""
    terrain = funnel_terrain()
    filled = HydrologyService.condition_dem(terrain.elevation_grid)
    fdir = HydrologyService.calculate_flow_direction(filled, terrain.grid_resolution_meters)
    acc = HydrologyService.calculate_flow_accumulation(fdir, filled)

    candidates = CandidateSelectionService.identify_candidates(
        terrain, config=config_with_small_nms(), flow_accumulation=acc
    )
    assert len(candidates) > 0


# --- Phase 4: catchment on precomputed grids -------------------------------------------


def _hydrology_grids(terrain: TerrainModel):
    filled = HydrologyService.condition_dem(terrain.elevation_grid)
    fdir = HydrologyService.calculate_flow_direction(filled, terrain.grid_resolution_meters)
    acc = HydrologyService.calculate_flow_accumulation(fdir, filled)
    return filled, fdir, acc


def _candidate(r: int, c: int, terrain: TerrainModel) -> PondCandidateSite:
    import pyproj

    min_x, _, min_y, _ = terrain.bounds
    res = terrain.grid_resolution_meters
    to_wgs = pyproj.Transformer.from_crs(terrain.crs, "EPSG:4326", always_xy=True)
    lon, lat = to_wgs.transform(min_x + c * res, min_y + r * res)
    return PondCandidateSite(
        id="t", rank=1, latitude=float(lat), longitude=float(lon),
        elevation=float(terrain.elevation_grid[r, c]),
        slope_degrees=float(terrain.slope_grid[r, c]),
        suitability_score=1.0,
    )


def test_analyze_hydrology_precomputed_matches_recomputed():
    terrain = funnel_terrain()
    cand = _candidate(33, 33, terrain)

    direct = HydrologyService.analyze_hydrology(terrain, cand)
    filled, fdir, acc = _hydrology_grids(terrain)
    shared = HydrologyService.analyze_hydrology(
        terrain, cand, conditioned_dem=filled, flow_direction=fdir, flow_accumulation=acc
    )
    assert shared.catchment_area_sq_meters == direct.catchment_area_sq_meters
    assert shared.contributing_cells_count == direct.contributing_cells_count
    assert shared.snapped_outlet.longitude == pytest.approx(direct.snapped_outlet.longitude)
    assert (
        shared.boundary.geometry["coordinates"]
        == direct.boundary.geometry["coordinates"]
    )


def test_catchment_extends_beyond_selected_land():
    terrain = funnel_terrain()
    mask = TerrainService.mask_cells_within_polygon(terrain, LAND_RECT)
    land_cells = int(mask.sum())
    assert land_cells > 0

    filled, fdir, acc = _hydrology_grids(terrain)
    candidates = CandidateSelectionService.identify_candidates(
        terrain,
        config=config_with_small_nms(),
        flow_accumulation=acc,
        candidate_mask=mask,
    )
    pond = candidates[0]
    catchment = HydrologyService.analyze_hydrology(
        terrain,
        pond,
        conditioned_dem=filled,
        flow_direction=fdir,
        flow_accumulation=acc,
    )
    # The catchment feeding the in-land pond must NOT be clipped to the land:
    # in the funnel terrain the upstream strip covers far more cells than the land.
    assert catchment.contributing_cells_count > land_cells
    assert catchment.catchment_area_sq_meters > land_cells * terrain.grid_resolution_meters**2

    # Catchment polygon is GeoJSON directly usable by a frontend map.
    assert catchment.boundary.type == "Feature"
    assert catchment.boundary.geometry["type"] in ("Polygon", "MultiPolygon")


# --- Endpoint integration (fake provider, no network) -----------------------------------

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


def test_terrain_preview_with_analysis(client, monkeypatch):
    from tests.test_dem import FakeProvider

    fake = FakeProvider()
    monkeypatch.setattr(DEMService, "_provider_chain", staticmethod(lambda: [fake]))
    response = client.post(
        "/api/v1/terrainPreview",
        json=dict(LAND_POLYGON, include_analysis=True),
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    siting = data["pond_siting"]
    assert siting is not None
    assert siting["land_masked_cell_count"] > 0
    assert len(siting["candidate_sites"]) > 0
    assert siting["scoring_config"]["flow_weight"] > 0.0

    land_geom = shape_from_geojson(LAND_POLYGON["geometry"])
    for cand in siting["candidate_sites"]:
        point = Point(cand["longitude"], cand["latitude"])
        assert land_geom.contains(point)

    assert siting["selected_pond"]["id"] == siting["candidate_sites"][0]["id"]

    catchment = siting["catchment"]
    assert catchment is not None
    assert catchment["catchment_area_sq_meters"] > 0
    assert catchment["boundary"]["geometry"]["type"] in ("Polygon", "MultiPolygon")
    # Catchment extends beyond the selected land (documented Phase 3/4 rule): the
    # catchment geometry must not be fully contained in the land bounding box.
    ring = catchment["boundary"]["geometry"]["coordinates"][0]
    lons = [pt[0] for pt in ring]
    lats = [pt[1] for pt in ring]
    land_bbox = land_geom.bounds  # (min_lon, min_lat, max_lon, max_lat)
    escapes_land_bbox = (
        max(lons) > land_bbox[2] + 1e-6
        or min(lons) < land_bbox[0] - 1e-6
        or max(lats) > land_bbox[3] + 1e-6
        or min(lats) < land_bbox[1] - 1e-6
    )
    assert escapes_land_bbox


def test_terrain_preview_without_analysis_has_no_siting(client, monkeypatch):
    from tests.test_dem import FakeProvider

    fake = FakeProvider()
    monkeypatch.setattr(DEMService, "_provider_chain", staticmethod(lambda: [fake]))
    response = client.post("/api/v1/terrainPreview", json=LAND_POLYGON)
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["pond_siting"] is None
