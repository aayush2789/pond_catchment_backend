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
      <LineString><coordinates>80.1,20.1 80.2,20.2</coordinates></LineString>
    </Placemark>
    <Placemark>
      <ExtendedData>
        <Data name="contour"><value>360.0</value></Data>
      </ExtendedData>
      <LineString><coordinates>80.3,20.3 80.4,20.4</coordinates></LineString>
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
      <LineString><coordinates>80.1,20.1,150.0 80.2,20.2,150.0</coordinates></LineString>
    </Placemark>
    <Placemark>
      <LineString><coordinates>80.3,20.3,160.0 80.4,20.4,160.0</coordinates></LineString>
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
