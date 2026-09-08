import io
import zipfile
from fastapi import status

VALID_KML_CONTENT = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Village Contours</name>
    <Placemark>
      <name>Contour 100m</name>
      <LineString>
        <coordinates>77.1025,28.7041,100 77.1030,28.7045,100</coordinates>
      </LineString>
    </Placemark>
    <Placemark>
      <name>Contour 95m</name>
      <LineString>
        <coordinates>77.1020,28.7035,95 77.1028,28.7039,95</coordinates>
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
    assert data["features_count"] == 2
    assert data["kml_entry_name"] is None


def test_find_catchment_valid_kmz(client):
    kmz_data = build_kmz_bytes("nested/village_terrain_map.kml", VALID_KML_CONTENT)
    files = {"file": ("village.kmz", io.BytesIO(kmz_data), "application/vnd.google-earth.kmz")}
    response = client.post("/api/v1/findCatchment", files=files)
    assert response.status_code == status.HTTP_200_OK

    data = response.json()
    assert data["filename"] == "village.kmz"
    assert data["file_type"] == "kmz"
    assert data["is_valid"] is True
    assert data["can_parse"] is True
    assert data["features_count"] == 2
    assert data["kml_entry_name"] == "nested/village_terrain_map.kml"


def test_analyze_contour_alias_endpoint(client):
    files = {"file": ("contours.kml", io.BytesIO(VALID_KML_CONTENT), "application/vnd.google-earth.kml+xml")}
    response = client.post("/api/v1/analyzeContour", files=files)
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["is_valid"] is True


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


def test_find_catchment_non_kml_xml(client):
    files = {"file": ("not_kml.kml", io.BytesIO(b"<catalog><item>value</item></catalog>"), "application/xml")}
    response = client.post("/api/v1/findCatchment", files=files)
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "not a valid KML document" in response.json()["detail"]


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

