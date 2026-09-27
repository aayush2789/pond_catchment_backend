import math

from fastapi import status

BASE_SQUARE = [
    [77.1025, 28.7041],
    [77.1035, 28.7041],
    [77.1035, 28.7051],
    [77.1025, 28.7051],
    [77.1025, 28.7041],
]


def polygon_payload(coordinates=None, geom_type="Polygon"):
    if coordinates is None:
        coordinates = [BASE_SQUARE]
    if geom_type == "Polygon":
        geometry = {"type": "Polygon", "coordinates": coordinates}
    else:
        geometry = {"type": "MultiPolygon", "coordinates": coordinates}
    return {"geometry": geometry}


def post_geometry(client, payload):
    return client.post("/api/v1/analyzeLand", json=payload)


def test_analyze_land_valid_polygon(client):
    response = post_geometry(client, polygon_payload())
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["status"] == "success"
    assert "selected_land" in data

    land = data["selected_land"]
    assert land["geometry_type"] == "Polygon"
    assert land["geometry"]["type"] == "Polygon"

    # ~0.001° x 0.001° square near 28.7°N: roughly 111 m x 98 m ~= 10,900 m².
    assert 8_000.0 < land["area_m2"] < 13_000.0
    assert math.isclose(land["area_hectares"], land["area_m2"] / 10_000.0, abs_tol=0.0001)

    bbox = land["bounding_box"]
    assert bbox["min_longitude"] == 77.1025
    assert bbox["max_longitude"] == 77.1035
    assert bbox["min_latitude"] == 28.7041
    assert bbox["max_latitude"] == 28.7051

    centroid = land["centroid"]
    assert math.isclose(centroid["latitude"], 28.7046, abs_tol=0.0005)
    assert math.isclose(centroid["longitude"], 77.1030, abs_tol=0.0005)
    assert bbox["min_longitude"] <= centroid["longitude"] <= bbox["max_longitude"]
    assert bbox["min_latitude"] <= centroid["latitude"] <= bbox["max_latitude"]


def test_analyze_land_valid_multipolygon(client):
    shifted = [[lon + 0.01, lat] for lon, lat in BASE_SQUARE]
    response = post_geometry(
        client, polygon_payload([[BASE_SQUARE], [shifted]], geom_type="MultiPolygon")
    )
    assert response.status_code == status.HTTP_200_OK

    land = response.json()["selected_land"]
    assert land["geometry_type"] == "MultiPolygon"

    single = post_geometry(client, polygon_payload()).json()["selected_land"]
    # Two disjoint equal squares: total geodesic area must be ~double a single square.
    assert math.isclose(land["area_m2"], 2.0 * single["area_m2"], rel_tol=0.01)


def test_analyze_land_polygon_with_hole(client):
    outer = [
        [77.1025, 28.7041],
        [77.1045, 28.7041],
        [77.1045, 28.7061],
        [77.1025, 28.7061],
        [77.1025, 28.7041],
    ]
    hole = [
        [77.1030, 28.7046],
        [77.1040, 28.7046],
        [77.1040, 28.7056],
        [77.1030, 28.7056],
        [77.1030, 28.7046],
    ]
    solid = post_geometry(client, polygon_payload([outer])).json()["selected_land"]
    with_hole = post_geometry(client, polygon_payload([outer, hole])).json()["selected_land"]

    assert with_hole["area_m2"] < solid["area_m2"]
    # The hole is 1/4 of the outer square, so remaining area is ~3/4 of solid.
    assert math.isclose(with_hole["area_m2"], 0.75 * solid["area_m2"], rel_tol=0.01)


def test_analyze_land_accepts_3d_positions(client):
    coords = [[77.1025, 28.7041, 0.0], [77.1035, 28.7041, 0.0], [77.1035, 28.7051, 0.0], [77.1025, 28.7051, 0.0], [77.1025, 28.7041, 0.0]]
    response = post_geometry(client, polygon_payload([coords]))
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["selected_land"]["area_m2"] > 0


def test_analyze_land_rejects_non_polygon_geojson(client):
    response = post_geometry(client, {"geometry": {"type": "Point", "coordinates": [77.1, 28.7]}})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_analyze_land_rejects_unclosed_ring(client):
    open_ring = BASE_SQUARE[:-1]
    response = post_geometry(client, polygon_payload([open_ring]))
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "closed" in response.json()["detail"].lower()


def test_analyze_land_rejects_ring_with_too_few_positions(client):
    coords = [[77.1025, 28.7041], [77.1035, 28.7051]]
    response = post_geometry(client, polygon_payload([coords]))
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_analyze_land_rejects_zero_area_polygon(client):
    collinear = [
        [77.1025, 28.7041],
        [77.1030, 28.7046],
        [77.1035, 28.7051],
        [77.1040, 28.7056],
        [77.1025, 28.7041],
    ]
    response = post_geometry(client, polygon_payload([collinear]))
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "area" in response.json()["detail"].lower()


def test_analyze_land_rejects_out_of_range_coordinates(client):
    coords = [
        [77.1025, 28.7041],
        [77.1035, 28.7041],
        [77.1035, 28.7051],
        [200.0, 28.7051],
        [77.1025, 28.7041],
    ]
    response = post_geometry(client, polygon_payload([coords]))
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "longitude" in response.json()["detail"].lower()


def test_analyze_land_rejects_self_intersecting_polygon(client):
    bowtie = [
        [77.1025, 28.7041],
        [77.1035, 28.7051],
        [77.1035, 28.7041],
        [77.1025, 28.7051],
        [77.1025, 28.7041],
    ]
    response = post_geometry(client, polygon_payload([bowtie]))
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_analyze_land_rejects_oversized_selection(client):
    # ~0.5° x 0.5° square (~3000 km²) exceeds the configured 100 km² cap.
    coords = [
        [77.0, 28.0],
        [77.5, 28.0],
        [77.5, 28.5],
        [77.0, 28.5],
        [77.0, 28.0],
    ]
    response = post_geometry(client, polygon_payload([coords]))
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "exceeds" in response.json()["detail"].lower()


def test_analyze_land_rejects_antimeridian_crossing(client):
    coords = [
        [179.95, 10.0],
        [-179.95, 10.0],
        [-179.95, 10.01],
        [179.95, 10.01],
        [179.95, 10.0],
    ]
    response = post_geometry(client, polygon_payload([coords]))
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "antimeridian" in response.json()["detail"].lower()


def test_analyze_land_rejects_empty_multipolygon(client):
    response = post_geometry(client, polygon_payload([], geom_type="MultiPolygon"))
    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_analyze_land_rejects_missing_geometry(client):
    response = client.post("/api/v1/analyzeLand", json={"properties": {}})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
