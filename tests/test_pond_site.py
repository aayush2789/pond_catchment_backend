import json

import pytest
from fastapi import status

from app.core.config import settings
from app.services.dem import DEMService
from app.services.rainfall import RainfallService
from tests.test_dem import FakeProvider
from tests.test_rainfall import OPEN_METEO_PAYLOAD

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

VALID_KML_CONTENT = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>100.0</name>
      <LineString><coordinates>77.1025,28.7041 77.1040,28.7048</coordinates></LineString>
    </Placemark>
    <Placemark>
      <name>95.0</name>
      <LineString><coordinates>77.1018,28.7034 77.1035,28.7042</coordinates></LineString>
    </Placemark>
  </Document>
</kml>
"""


@pytest.fixture(autouse=True)
def isolated_caches(tmp_path, monkeypatch):
    DEMService._memory_cache.clear()
    RainfallService._memory_cache.clear()
    monkeypatch.setattr(settings, "DEM_CACHE_DIR", str(tmp_path / "dem_cache"))
    monkeypatch.setattr(settings, "RAINFALL_CACHE_DIR", str(tmp_path / "rain_cache"))
    yield
    DEMService._memory_cache.clear()
    RainfallService._memory_cache.clear()


@pytest.fixture
def fake_external_providers(monkeypatch):
    monkeypatch.setattr(DEMService, "_provider_chain", staticmethod(lambda: [FakeProvider()]))
    monkeypatch.setattr(
        RainfallService, "_http_get_json", staticmethod(lambda url, params: OPEN_METEO_PAYLOAD)
    )


def test_full_analysis_with_automatic_dem(client, fake_external_providers):
    response = client.post(
        "/api/v1/analyzePondSite",
        data={"request": json.dumps(LAND_POLYGON)},
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    assert data["status"] == "success"
    # Selected land
    land = data["selected_land"]
    assert land["area_m2"] > 0
    assert land["geometry"]["type"] == "Polygon"
    # Terrain + contours
    assert data["terrain"]["slope"] is not None
    assert data["contours"]["type"] == "FeatureCollection"
    assert data["dem_source"]["provider"] == "fake"
    # Pond
    pond = data["pond"]
    assert pond is not None
    assert 0.0 <= pond["suitability_score"] <= 1.0
    # Catchment
    catchment = data["catchment"]
    assert catchment["catchment_area_sq_meters"] > 0
    assert catchment["boundary"]["geometry"]["type"] in ("Polygon", "MultiPolygon")
    # Rainfall
    rainfall = data["rainfall"]
    assert rainfall["source"] == "open-meteo"
    assert rainfall["rainfall_mm"] > 0
    assert "ERA5" in rainfall["dataset"]
    # Water: transparent chain area x rainfall(m) x coefficient, then efficiency.
    water = data["water"]
    expected_theoretical = (
        catchment["catchment_area_sq_meters"] * (rainfall["rainfall_mm"] / 1000.0) * water["runoff_coefficient"]
    )
    assert water["theoretical_runoff_m3"] == pytest.approx(expected_theoretical, rel=1e-3)
    assert water["expected_collectible_water_m3"] == pytest.approx(
        water["theoretical_runoff_m3"] * water["collection_efficiency"], rel=1e-3
    )
    # Storage distinct from runoff, sized to the collectible inflow.
    storage = data["pond_storage"]
    assert storage["design_inflow_m3"] == pytest.approx(
        water["expected_collectible_water_m3"], rel=1e-3
    )
    assert storage["storage_capacity_m3"] == pytest.approx(
        water["expected_collectible_water_m3"], rel=0.05
    )
    assert "not a substitute" in storage["note"].lower()


def test_full_analysis_pond_inside_selected_land(client, fake_external_providers):
    from shapely.geometry import Point, shape

    response = client.post(
        "/api/v1/analyzePondSite",
        data={"request": json.dumps(LAND_POLYGON)},
    )
    assert response.status_code == status.HTTP_200_OK
    pond = response.json()["pond"]
    land_geom = shape(LAND_POLYGON["geometry"])
    assert land_geom.contains(Point(pond["longitude"], pond["latitude"]))


def test_full_analysis_kml_fallback_path(client, fake_external_providers):
    files = {"file": ("contours.kml", VALID_KML_CONTENT, "application/vnd.google-earth.kml+xml")}
    response = client.post("/api/v1/analyzePondSite", files=files)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["selected_land"] is None
    assert data["dem_source"] is None
    assert data["pond"] is not None
    assert data["catchment"] is not None
    assert data["rainfall"]["source"] == "open-meteo"
    assert data["water"]["expected_collectible_water_m3"] > 0


def test_analysis_with_explicit_parameters(client, fake_external_providers):
    payload = dict(
        LAND_POLYGON,
        analysis_parameters={
            "buffer_meters": 300.0,
            "runoff_coefficient": 0.45,
            "collection_efficiency": 0.8,
            "contour_interval_m": 10.0,
        },
    )
    response = client.post(
        "/api/v1/analyzePondSite",
        data={"request": json.dumps(payload)},
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["water"]["runoff_coefficient"] == 0.45
    assert data["water"]["collection_efficiency"] == 0.8
    assert "explicitly provided" in data["water"]["runoff_coefficient_basis"].lower()


def test_analysis_requires_exactly_one_terrain_source(client, fake_external_providers):
    # Neither geometry nor file.
    response = client.post("/api/v1/analyzePondSite", data={})
    assert response.status_code == status.HTTP_400_BAD_REQUEST

    # Both at once.
    files = {"file": ("contours.kml", VALID_KML_CONTENT, "application/vnd.google-earth.kml+xml")}
    response = client.post(
        "/api/v1/analyzePondSite",
        files=files,
        data={"request": json.dumps(LAND_POLYGON)},
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_analysis_invalid_request_json_rejected(client):
    response = client.post("/api/v1/analyzePondSite", data={"request": "{not json"})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_analysis_invalid_geometry_rejected(client, fake_external_providers):
    payload = {"geometry": {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]]}}
    response = client.post("/api/v1/analyzePondSite", data={"request": json.dumps(payload)})
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_analysis_selection_smaller_than_grid_cell_rejected(client, fake_external_providers):
    tiny = {
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [81.290000, 21.245000],
                    [81.290010, 21.245000],
                    [81.290010, 21.245010],
                    [81.290000, 21.245010],
                    [81.290000, 21.245000],
                ]
            ],
        }
    }
    response = client.post("/api/v1/analyzePondSite", data={"request": json.dumps(tiny)})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "No suitable pond location" in response.json()["detail"]
