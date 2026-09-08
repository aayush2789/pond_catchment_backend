import io
from fastapi import status


def test_root_endpoint(client):
    response = client.get("/")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "project" in data
    assert "docs" in data
    assert data["docs"] == "/docs"


def test_v1_health_endpoint(client):
    response = client.get("/api/v1/health")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "healthy"
    assert "project_name" in data
    assert "version" in data


def test_direct_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "healthy"


def test_analyze_contour_route_file_validation(client):
    file_payload = {"file": ("test.txt", io.BytesIO(b"dummy text"), "text/plain")}
    response = client.post("/api/v1/analyzeContour", files=file_payload)
    assert response.status_code == status.HTTP_400_BAD_REQUEST

    valid_payload = {"file": ("sample.kml", io.BytesIO(b"<kml></kml>"), "application/vnd.google-earth.kml+xml")}
    response = client.post("/api/v1/analyzeContour", files=valid_payload)
    assert response.status_code == status.HTTP_501_NOT_IMPLEMENTED
