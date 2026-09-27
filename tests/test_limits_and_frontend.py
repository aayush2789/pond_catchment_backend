import io
import math

from fastapi import status

from app.core.config import settings

from tests.test_catchment import VALID_KML_CONTENT


def _circle_polygon(center_lon=81.29, center_lat=21.245, radius_deg=0.003, points=2100):
    coords = []
    for i in range(points):
        angle = 2 * math.pi * i / (points - 1)
        coords.append(
            [center_lon + radius_deg * math.cos(angle), center_lat + radius_deg * math.sin(angle)]
        )
    coords.append(list(coords[0]))  # close
    return {
        "geometry": {
            "type": "Polygon",
            "coordinates": [coords],
        }
    }


# --- Phase 10: request-size guards -----------------------------------------------------


def test_land_vertex_limit_enforced(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_LAND_VERTICES", 100)
    payload = _circle_polygon(points=150)
    response = client.post("/api/v1/analyzeLand", json=payload)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "vertices" in response.json()["detail"]


def test_land_many_vertices_accepted_when_under_limit(client):
    payload = _circle_polygon(points=60)
    response = client.post("/api/v1/analyzeLand", json=payload)
    assert response.status_code == status.HTTP_200_OK


def test_kml_upload_size_limit_enforced(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_MB", 0.0001)  # ~104 bytes
    files = {"file": ("contours.kml", VALID_KML_CONTENT, "application/vnd.google-earth.kml+xml")}
    response = client.post("/api/v1/findCatchment", files=files)
    assert response.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    assert "maximum accepted size" in response.json()["detail"]


# --- Phase 9: frontend is served by the backend ----------------------------------------


def test_frontend_index_served(client):
    response = client.get("/app/")
    assert response.status_code == status.HTTP_200_OK
    body = response.text
    assert 'id="map"' in body
    assert "app.js" in body
    assert "leaflet" in body.lower()


def test_frontend_assets_served(client):
    response = client.get("/app/app.js")
    assert response.status_code == status.HTTP_200_OK
    assert "analyzePondSite" in response.text

    css = client.get("/app/style.css")
    assert css.status_code == status.HTTP_200_OK


def test_root_advertises_frontend(client):
    response = client.get("/")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["frontend"] == "/app/"
