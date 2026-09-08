import io
import zipfile
from pathlib import Path
from fastapi import status

VALID_KML_CONTENT = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Village Contours</name>
    <Placemark>
      <name>100.0</name>
      <LineString>
        <coordinates>77.1025,28.7041 77.1030,28.7045</coordinates>
      </LineString>
    </Placemark>
    <Placemark>
      <name>95.0</name>
      <LineString>
        <coordinates>77.1020,28.7035 77.1028,28.7039</coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>
"""


def build_kmz_bytes(kml_filename: str, kml_content: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(kml_filename, kml_content)
    return buffer.getvalue()


def test_find_catchment_valid_kml(client):
    files = {"file": ("contours.kml", io.BytesIO(VALID_KML_CONTENT), "application/vnd.google-earth.kml+xml")}
    response = client.post("/api/v1/findCatchment", files=files)
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["filename"] == "contours.kml"
    assert data["file_type"] == "kml"
    assert data["is_valid"] is True
    assert data["can_parse"] is True
    assert data["contours_processed"] == 2
    assert data["min_elevation"] == 95.0
    assert data["max_elevation"] == 100.0
    assert "extent" in data
    assert data["extent"]["min_latitude"] == 28.7035
    assert data["extent"]["max_latitude"] == 28.7045


def test_find_catchment_real_sample_file(client):
    sample_path = Path("data/sample/contours_1m.kml")
    if not sample_path.exists():
        sample_path = Path("contours_1m.kml")

    with open(sample_path, "rb") as f:
        files = {"file": ("contours_1m.kml", f, "application/vnd.google-earth.kml+xml")}
        response = client.post("/api/v1/findCatchment", files=files)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["filename"] == "contours_1m.kml"
    assert data["file_type"] == "kml"
    assert data["is_valid"] is True
    assert data["contours_processed"] == 1355
    assert data["min_elevation"] == 267.0
    assert data["max_elevation"] == 298.0
    assert data["extent"]["min_latitude"] < data["extent"]["max_latitude"]
    assert data["extent"]["min_longitude"] < data["extent"]["max_longitude"]

    assert data["terrain"] is not None
    terrain = data["terrain"]
    assert terrain["crs"] == "EPSG:32644"
    assert terrain["grid_resolution_meters"] == 10.0
    assert terrain["rows"] > 0
    assert terrain["cols"] > 0
    assert terrain["min_elevation"] >= 267.0
    assert terrain["max_elevation"] <= 298.0
    assert terrain["projected_bounds"]["min_x"] < terrain["projected_bounds"]["max_x"]
    assert terrain["projected_bounds"]["min_y"] < terrain["projected_bounds"]["max_y"]

    assert terrain["slope"] is not None
    assert terrain["slope"]["min_slope_degrees"] >= 0.0
    assert terrain["slope"]["max_slope_degrees"] >= terrain["slope"]["min_slope_degrees"]

    assert "candidate_sites" in data
    assert len(data["candidate_sites"]) > 0
    top_candidate = data["candidate_sites"][0]
    assert 0.0 <= top_candidate["suitability_score"] <= 1.0
    assert top_candidate["rank"] == 1
    assert data["extent"]["min_latitude"] <= top_candidate["latitude"] <= data["extent"]["max_latitude"]
    assert data["extent"]["min_longitude"] <= top_candidate["longitude"] <= data["extent"]["max_longitude"]
    assert top_candidate["elevation"] >= 267.0
    assert "factor_scores" in top_candidate
    assert "slope_score" in top_candidate["factor_scores"]
    assert "elevation_score" in top_candidate["factor_scores"]

    assert data["selected_pond"] is not None
    assert data["selected_pond"]["id"] == top_candidate["id"]

    assert data["catchment"] is not None
    catchment = data["catchment"]
    assert catchment["catchment_area_sq_meters"] > 0
    assert catchment["catchment_area_hectares"] > 0
    assert catchment["contributing_cells_count"] > 0
    assert catchment["hydrology"]["outlet_flow_accumulation_cells"] > 0
    assert catchment["boundary"]["type"] == "Feature"
    assert "geometry" in catchment["boundary"]
    assert catchment["boundary"]["geometry"]["type"] in ("Polygon", "MultiPolygon")





def test_find_catchment_valid_kmz(client):
    kmz_data = build_kmz_bytes("nested/village_terrain.kml", VALID_KML_CONTENT)
    files = {"file": ("village.kmz", io.BytesIO(kmz_data), "application/vnd.google-earth.kmz")}
    response = client.post("/api/v1/findCatchment", files=files)
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["filename"] == "village.kmz"
    assert data["file_type"] == "kmz"
    assert data["contours_processed"] == 2
    assert data["kml_entry_name"] == "nested/village_terrain.kml"


def test_find_catchment_extended_data_elevation(client):
    kml_extended = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <ExtendedData>
        <SchemaData schemaUrl="#contour">
          <SimpleData name="ELEVATION">350.5</SimpleData>
        </SchemaData>
      </ExtendedData>
      <LineString><coordinates>80.001,20.001 80.002,20.002</coordinates></LineString>
    </Placemark>
    <Placemark>
      <ExtendedData>
        <Data name="contour"><value>360.0</value></Data>
      </ExtendedData>
      <LineString><coordinates>80.003,20.003 80.004,20.004</coordinates></LineString>
    </Placemark>
  </Document>
</kml>"""
    files = {"file": ("extended.kml", io.BytesIO(kml_extended), "application/vnd.google-earth.kml+xml")}
    response = client.post("/api/v1/findCatchment", files=files)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["contours_processed"] == 2
    assert data["min_elevation"] == 350.5
    assert data["max_elevation"] == 360.0


def test_find_catchment_3d_coordinates_fallback(client):
    kml_3d = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <LineString><coordinates>80.001,20.001,150.0 80.002,20.002,150.0</coordinates></LineString>
    </Placemark>
    <Placemark>
      <LineString><coordinates>80.003,20.003,160.0 80.004,20.004,160.0</coordinates></LineString>
    </Placemark>
  </Document>
</kml>"""
    files = {"file": ("3d_coords.kml", io.BytesIO(kml_3d), "application/vnd.google-earth.kml+xml")}
    response = client.post("/api/v1/findCatchment", files=files)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["contours_processed"] == 2
    assert data["min_elevation"] == 150.0
    assert data["max_elevation"] == 160.0


def test_find_catchment_missing_elevation(client):
    kml_no_elev = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>Road Highway</name>
      <LineString><coordinates>80.1,20.1 80.2,20.2</coordinates></LineString>
    </Placemark>
  </Document>
</kml>"""
    files = {"file": ("no_elev.kml", io.BytesIO(kml_no_elev), "application/vnd.google-earth.kml+xml")}
    response = client.post("/api/v1/findCatchment", files=files)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "missing required elevation" in response.json()["detail"] or "Insufficient contour" in response.json()["detail"]


def test_find_catchment_unsupported_geometry_only(client):
    kml_points = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark><name>Point A</name><Point><coordinates>80.1,20.1</coordinates></Point></Placemark>
    <Placemark><name>Point B</name><Point><coordinates>80.2,20.2</coordinates></Point></Placemark>
  </Document>
</kml>"""
    files = {"file": ("points.kml", io.BytesIO(kml_points), "application/vnd.google-earth.kml+xml")}
    response = client.post("/api/v1/findCatchment", files=files)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "No supported contour line geometries" in response.json()["detail"]


def test_find_catchment_degenerate_geometry(client):
    kml_degenerate = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark><name>100</name><LineString><coordinates>80.1,20.1</coordinates></LineString></Placemark>
    <Placemark><name>200</name><LineString><coordinates>80.2,20.2</coordinates></LineString></Placemark>
  </Document>
</kml>"""
    files = {"file": ("degenerate.kml", io.BytesIO(kml_degenerate), "application/vnd.google-earth.kml+xml")}
    response = client.post("/api/v1/findCatchment", files=files)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "minimum 2 coordinate points required" in response.json()["detail"] or "Insufficient contour" in response.json()["detail"]


def test_find_catchment_insufficient_elevation_levels(client):
    kml_flat = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark><name>100</name><LineString><coordinates>80.1,20.1 80.2,20.2</coordinates></LineString></Placemark>
    <Placemark><name>100</name><LineString><coordinates>80.3,20.3 80.4,20.4</coordinates></LineString></Placemark>
  </Document>
</kml>"""
    files = {"file": ("flat.kml", io.BytesIO(kml_flat), "application/vnd.google-earth.kml+xml")}
    response = client.post("/api/v1/findCatchment", files=files)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "at least two distinct contour elevation levels" in response.json()["detail"]


def test_find_catchment_unsupported_extension(client):
    files = {"file": ("contours.csv", io.BytesIO(b"lat,lon,elev\n28.7,77.1,100"), "text/csv")}
    response = client.post("/api/v1/findCatchment", files=files)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Unsupported file format" in response.json()["detail"]


def test_find_catchment_empty_file(client):
    files = {"file": ("empty.kml", io.BytesIO(b""), "application/vnd.google-earth.kml+xml")}
    response = client.post("/api/v1/findCatchment", files=files)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Uploaded file is empty" in response.json()["detail"]


def test_find_catchment_malformed_kml(client):
    files = {"file": ("corrupt.kml", io.BytesIO(b"<kml><unclosed>"), "application/vnd.google-earth.kml+xml")}
    response = client.post("/api/v1/findCatchment", files=files)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Malformed KML content" in response.json()["detail"]


def test_find_catchment_corrupted_kmz(client):
    files = {"file": ("broken.kmz", io.BytesIO(b"not a real zip archive"), "application/vnd.google-earth.kmz")}
    response = client.post("/api/v1/findCatchment", files=files)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Malformed or corrupted KMZ archive" in response.json()["detail"]


def test_find_catchment_kmz_without_kml(client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("notes.txt", "No contour or kml file here")
    kmz_no_kml = buffer.getvalue()

    files = {"file": ("no_kml.kmz", io.BytesIO(kmz_no_kml), "application/vnd.google-earth.kmz")}
    response = client.post("/api/v1/findCatchment", files=files)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "does not contain a valid .kml file" in response.json()["detail"]


def test_find_catchment_missing_upload(client):
    response = client.post("/api/v1/findCatchment")
    assert response.status_code == 422


def test_terrain_service_direct_reconstruction():
    import numpy as np
    from app.services.parser import ContourParserService
    from app.services.terrain import TerrainService

    sample_path = Path("data/sample/contours_1m.kml")
    if not sample_path.exists():
        sample_path = Path("contours_1m.kml")

    with open(sample_path, "rb") as f:
        dataset = ContourParserService.parse_and_normalize_kml(f.read(), "contours_1m.kml")

    model = TerrainService.reconstruct_terrain(dataset, resolution_meters=15.0)
    assert model.crs == "EPSG:32644"
    assert isinstance(model.elevation_grid, np.ndarray)
    assert model.elevation_grid.shape[0] == model.rows
    assert model.elevation_grid.shape[1] == model.cols
    assert model.rows > 0 and model.cols > 0
    assert np.isnan(model.elevation_grid).sum() == 0
    assert np.isinf(model.elevation_grid).sum() == 0
    assert model.min_elevation >= 267.0
    assert model.max_elevation <= 298.0

    metadata = model.to_metadata()
    assert metadata.crs == "EPSG:32644"
    assert metadata.rows == model.rows
    assert metadata.cols == model.cols


def test_terrain_reconstruction_bounds_derived_dynamically():
    from app.schemas.catchment import ContourLine, GeographicExtent, NormalizedContourDataset
    from app.services.terrain import TerrainService

    dataset_a = NormalizedContourDataset(
        filename="area_a.kml",
        contour_count=2,
        min_elevation=100.0,
        max_elevation=200.0,
        extent=GeographicExtent(min_latitude=10.0, max_latitude=10.01, min_longitude=75.0, max_longitude=75.01),
        contours=[
            ContourLine(id="1", elevation=100.0, coordinates=[(75.0, 10.0), (75.01, 10.0)], vertex_count=2),
            ContourLine(id="2", elevation=200.0, coordinates=[(75.0, 10.01), (75.01, 10.01)], vertex_count=2),
        ],
    )

    dataset_b = NormalizedContourDataset(
        filename="area_b.kml",
        contour_count=2,
        min_elevation=100.0,
        max_elevation=200.0,
        extent=GeographicExtent(min_latitude=30.0, max_latitude=30.01, min_longitude=85.0, max_longitude=85.01),
        contours=[
            ContourLine(id="1", elevation=100.0, coordinates=[(85.0, 30.0), (85.01, 30.0)], vertex_count=2),
            ContourLine(id="2", elevation=200.0, coordinates=[(85.0, 30.01), (85.01, 30.01)], vertex_count=2),
        ],
    )

    model_a = TerrainService.reconstruct_terrain(dataset_a, resolution_meters=50.0)
    model_b = TerrainService.reconstruct_terrain(dataset_b, resolution_meters=50.0)

    assert model_a.crs != model_b.crs
    assert model_a.bounds != model_b.bounds


def test_terrain_reconstruction_input_change_changes_output():
    from app.schemas.catchment import ContourLine, GeographicExtent, NormalizedContourDataset
    from app.services.terrain import TerrainService

    dataset_low = NormalizedContourDataset(
        filename="low.kml",
        contour_count=2,
        min_elevation=50.0,
        max_elevation=60.0,
        extent=GeographicExtent(min_latitude=20.0, max_latitude=20.01, min_longitude=80.0, max_longitude=80.01),
        contours=[
            ContourLine(id="1", elevation=50.0, coordinates=[(80.0, 20.0), (80.01, 20.0)], vertex_count=2),
            ContourLine(id="2", elevation=60.0, coordinates=[(80.0, 20.01), (80.01, 20.01)], vertex_count=2),
        ],
    )

    dataset_high = NormalizedContourDataset(
        filename="high.kml",
        contour_count=2,
        min_elevation=500.0,
        max_elevation=600.0,
        extent=GeographicExtent(min_latitude=20.0, max_latitude=20.01, min_longitude=80.0, max_longitude=80.01),
        contours=[
            ContourLine(id="1", elevation=500.0, coordinates=[(80.0, 20.0), (80.01, 20.0)], vertex_count=2),
            ContourLine(id="2", elevation=600.0, coordinates=[(80.0, 20.01), (80.01, 20.01)], vertex_count=2),
        ],
    )

    model_low = TerrainService.reconstruct_terrain(dataset_low, resolution_meters=50.0)
    model_high = TerrainService.reconstruct_terrain(dataset_high, resolution_meters=50.0)

    assert model_low.max_elevation < 100.0
    assert model_high.min_elevation > 400.0
    assert not (model_low.elevation_grid == model_high.elevation_grid).all()


def test_terrain_service_insufficient_points():
    import pytest
    from fastapi import HTTPException
    from app.schemas.catchment import ContourLine, GeographicExtent, NormalizedContourDataset
    from app.services.terrain import TerrainService

    dataset_empty = NormalizedContourDataset(
        filename="sparse.kml",
        contour_count=2,
        min_elevation=10.0,
        max_elevation=20.0,
        extent=GeographicExtent(min_latitude=20.0, max_latitude=20.01, min_longitude=80.0, max_longitude=80.01),
        contours=[
            ContourLine(id="1", elevation=10.0, coordinates=[(80.0, 20.0)], vertex_count=1),
            ContourLine(id="2", elevation=20.0, coordinates=[(80.01, 20.01)], vertex_count=1),
        ],
    )

    with pytest.raises(HTTPException) as exc_info:
        TerrainService.reconstruct_terrain(dataset_empty)
    assert exc_info.value.status_code == 400


def test_terrain_service_calculate_slope():
    import numpy as np
    from app.services.terrain import TerrainService

    # 45-degree slope: rise = run (dz = 10m over dx = 10m)
    x = np.arange(0, 50, 10)
    grid = np.tile(x, (5, 1)).astype(np.float64)
    slope = TerrainService.calculate_slope(grid, resolution_meters=10.0)

    # Interior cells have gradient = 1.0 -> arctan(1.0) = 45 degrees
    assert np.isclose(slope[2, 2], 45.0, atol=1e-1)


def test_candidate_selection_explainability_and_scoring():
    from app.services.candidate_selection import CandidateScoringConfig, CandidateSelectionService
    from app.services.parser import ContourParserService
    from app.services.terrain import TerrainService

    sample_path = Path("data/sample/contours_1m.kml")
    if not sample_path.exists():
        sample_path = Path("contours_1m.kml")

    with open(sample_path, "rb") as f:
        dataset = ContourParserService.parse_and_normalize_kml(f.read(), "contours_1m.kml")

    terrain = TerrainService.reconstruct_terrain(dataset, resolution_meters=15.0)

    # Test with default config
    candidates = CandidateSelectionService.identify_candidates(terrain)
    assert len(candidates) > 0
    top = candidates[0]
    assert top.rank == 1
    assert 0.0 <= top.suitability_score <= 1.0
    assert "slope_score" in top.factor_scores
    assert "elevation_score" in top.factor_scores

    # Test configurable weights override
    slope_focused_cfg = CandidateScoringConfig(slope_weight=1.0, elevation_weight=0.0, top_k=3)
    elev_focused_cfg = CandidateScoringConfig(slope_weight=0.0, elevation_weight=1.0, top_k=3)

    c_slope = CandidateSelectionService.identify_candidates(terrain, config=slope_focused_cfg)
    c_elev = CandidateSelectionService.identify_candidates(terrain, config=elev_focused_cfg)

    assert len(c_slope) == 3
    assert len(c_elev) == 3
    # Changing weights changes suitability scores
    assert c_slope[0].suitability_score == c_slope[0].factor_scores["slope_score"]
    assert c_elev[0].suitability_score == c_elev[0].factor_scores["elevation_score"]


def test_candidate_selection_dynamic_locations():
    from app.schemas.catchment import ContourLine, GeographicExtent, NormalizedContourDataset
    from app.services.candidate_selection import CandidateSelectionService
    from app.services.terrain import TerrainService

    dataset_1 = NormalizedContourDataset(
        filename="d1.kml",
        contour_count=2,
        min_elevation=100.0,
        max_elevation=120.0,
        extent=GeographicExtent(min_latitude=15.0, max_latitude=15.02, min_longitude=75.0, max_longitude=75.02),
        contours=[
            ContourLine(id="1", elevation=100.0, coordinates=[(75.0, 15.0), (75.02, 15.0)], vertex_count=2),
            ContourLine(id="2", elevation=120.0, coordinates=[(75.0, 15.02), (75.02, 15.02)], vertex_count=2),
        ],
    )

    dataset_2 = NormalizedContourDataset(
        filename="d2.kml",
        contour_count=2,
        min_elevation=250.0,
        max_elevation=270.0,
        extent=GeographicExtent(min_latitude=25.0, max_latitude=25.02, min_longitude=85.0, max_longitude=85.02),
        contours=[
            ContourLine(id="1", elevation=250.0, coordinates=[(85.0, 25.0), (85.02, 25.0)], vertex_count=2),
            ContourLine(id="2", elevation=270.0, coordinates=[(85.0, 25.02), (85.02, 25.02)], vertex_count=2),
        ],
    )

    terrain_1 = TerrainService.reconstruct_terrain(dataset_1, resolution_meters=50.0)
    terrain_2 = TerrainService.reconstruct_terrain(dataset_2, resolution_meters=50.0)

    cand_1 = CandidateSelectionService.identify_candidates(terrain_1)
    cand_2 = CandidateSelectionService.identify_candidates(terrain_2)

    assert len(cand_1) > 0 and len(cand_2) > 0
    assert cand_1[0].latitude != cand_2[0].latitude
    assert cand_1[0].longitude != cand_2[0].longitude


def test_hydrology_condition_dem():
    import numpy as np
    from app.services.hydrology import HydrologyService

    # 5x5 grid with internal depression (pit) at center
    dem = np.array([
        [10.0, 10.0, 10.0, 10.0, 10.0],
        [10.0,  5.0,  5.0,  5.0, 10.0],
        [10.0,  5.0,  2.0,  5.0, 10.0],
        [10.0,  5.0,  5.0,  5.0, 10.0],
        [10.0, 10.0,  8.0, 10.0, 10.0],
    ], dtype=np.float64)

    filled = HydrologyService.condition_dem(dem)
    # The depression should be filled up to the minimum spillway (8.0 at [4, 2])
    assert filled[2, 2] == 8.0
    assert filled[1, 1] == 8.0


def test_hydrology_flow_direction_and_accumulation():
    import numpy as np
    from app.services.hydrology import HydrologyService

    # Southward inclined ramp: row 0 is 50m, row 4 is 10m
    ramp = np.array([
        [50.0, 50.0, 50.0],
        [40.0, 40.0, 40.0],
        [30.0, 30.0, 30.0],
        [20.0, 20.0, 20.0],
        [10.0, 10.0, 10.0],
    ], dtype=np.float64)

    flow_dir = HydrologyService.calculate_flow_direction(ramp, resolution_meters=10.0)
    acc = HydrologyService.calculate_flow_accumulation(flow_dir, ramp)

    # Interior cells must flow South (index 1 in NEIGHBORS: (1, 0))
    for r in range(1, 4):
        assert flow_dir[r, 1] == 1

    # Accumulation should strictly increase downstream along column 1
    assert acc[1, 1] < acc[2, 1] < acc[3, 1] < acc[4, 1]


def test_hydrology_snap_to_drainage_cell():
    import numpy as np
    from app.services.hydrology import HydrologyService

    acc = np.zeros((10, 10), dtype=np.float64)
    # Stream channel at column 5
    acc[:, 5] = 100.0

    bounds = (0.0, 100.0, 0.0, 100.0)
    resolution = 10.0

    # Candidate at x=35.0 (column 3), y=50.0 (row 5)
    # Should snap to column 5 within snap radius
    outlet_r, outlet_c = HydrologyService.snap_to_drainage_cell(
        cand_x=35.0,
        cand_y=50.0,
        bounds=bounds,
        resolution=resolution,
        accumulation=acc,
        snap_radius_meters=30.0,
    )
    assert outlet_r == 5
    assert outlet_c == 5


def test_hydrology_delineate_catchment_and_polygon():
    from app.services.candidate_selection import CandidateSelectionService
    from app.services.hydrology import HydrologyService
    from app.services.parser import ContourParserService
    from app.services.terrain import TerrainService

    sample_path = Path("data/sample/contours_1m.kml")
    if not sample_path.exists():
        sample_path = Path("contours_1m.kml")

    with open(sample_path, "rb") as f:
        dataset = ContourParserService.parse_and_normalize_kml(f.read(), "contours_1m.kml")

    terrain = TerrainService.reconstruct_terrain(dataset, resolution_meters=15.0)
    candidates = CandidateSelectionService.identify_candidates(terrain)
    assert len(candidates) > 0

    catchment = HydrologyService.analyze_hydrology(terrain, candidates[0])
    assert catchment.catchment_area_sq_meters > 0.0
    assert catchment.catchment_area_hectares > 0.0
    assert catchment.contributing_cells_count > 0
    assert catchment.boundary.type == "Feature"
    assert "coordinates" in catchment.boundary.geometry
    assert catchment.hydrology.max_flow_accumulation_cells >= catchment.hydrology.outlet_flow_accumulation_cells


def test_hydrology_candidate_outside_bounds_raises_400():
    import pytest
    from fastapi import HTTPException
    from app.schemas.catchment import PondCandidateSite
    from app.services.hydrology import HydrologyService
    from app.services.parser import ContourParserService
    from app.services.terrain import TerrainService

    sample_path = Path("data/sample/contours_1m.kml")
    if not sample_path.exists():
        sample_path = Path("contours_1m.kml")

    with open(sample_path, "rb") as f:
        dataset = ContourParserService.parse_and_normalize_kml(f.read(), "contours_1m.kml")

    terrain = TerrainService.reconstruct_terrain(dataset, resolution_meters=20.0)

    outside_cand = PondCandidateSite(
        id="outside",
        rank=1,
        latitude=0.0,
        longitude=0.0,
        elevation=100.0,
        slope_degrees=1.0,
        suitability_score=0.9,
    )

    with pytest.raises(HTTPException) as exc_info:
        HydrologyService.analyze_hydrology(terrain, outside_cand)
    assert exc_info.value.status_code == 400



