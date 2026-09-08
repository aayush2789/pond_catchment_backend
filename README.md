# AI-Based Village Pond Planning System - Backend

A modular, extensible FastAPI backend service for the AI-based Village Pond Planning System. This system accepts contour maps (in KML/KMZ formats) to model terrain, assess hydrology, detect candidate pond sites, and delineate catchment areas.

## Architecture & Modular Structure

```text
pond_catchment_backend/
├── app/
│   ├── api/
│   │   └── v1/
│   │       ├── endpoints/
│   │       │   ├── health.py        # System health and status endpoint
│   │       │   └── catchment.py     # Contour ingestion & catchment routes
│   │       └── api.py               # V1 API router aggregator
│   ├── core/
│   │   └── config.py                # Environment-driven settings (pydantic-settings)
│   ├── models/                      # Domain entities & future database models
│   ├── schemas/
│   │   ├── health.py                # Health check Pydantic schemas
│   │   └── catchment.py             # Geospatial and inspection response schemas
│   ├── services/
│   │   ├── parser.py                # KML/KMZ normalization & validation service
│   │   ├── terrain.py               # Interface for DEM & slope analysis
│   │   └── hydrology.py             # Interface for flow & catchment delineation
│   ├── utils/
│   │   └── file_handler.py          # File format validation and file operations
│   └── main.py                      # FastAPI application entry point & middleware
├── data/
│   └── sample/                      # Sample contour datasets (KML/KMZ)
│       └── contours_1m.kml
├── tests/
│   ├── conftest.py                  # Pytest fixtures and TestClient
│   ├── test_catchment.py            # Contour upload, inspection, normalization, and error tests
│   └── test_health.py               # API & health check test suite
├── .env.example                     # Environment configuration template
├── .gitignore                       # Git exclusions for Python, venv, caches, logs
├── README.md                        # Documentation & setup guide
└── requirements.txt                 # Project dependencies
```

## Setup Instructions

### 1. Environment Setup

Activate the Python virtual environment:

**Windows (PowerShell):**
```powershell
.\venv\Scripts\Activate.ps1
```

**macOS / Linux:**
```bash
source venv/bin/activate
```

### 2. Install Dependencies

```powershell
pip install -r requirements.txt
```

### 3. Environment Configuration

Copy the sample environment file:

```powershell
cp .env.example .env
```

## Running the Application

Start the development server with Uvicorn:

```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Once running, access:
- **Interactive Swagger UI**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **ReDoc Documentation**: [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)
- **API Root**: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- **Health Check**: [http://127.0.0.1:8000/api/v1/health](http://127.0.0.1:8000/api/v1/health)

## API Endpoints

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/` | API status and root information |
| `GET` | `/api/v1/health` | Service health status |
| `POST` | `/api/v1/findCatchment` | Upload and normalize KML/KMZ contour map (multipart form-data) |
| `POST` | `/api/v1/analyzeContour` | Alias endpoint for `/findCatchment` |

## Testing the Upload Endpoint

### From Swagger UI (`/docs`)
1. Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).
2. Expand `POST /api/v1/findCatchment` under the **Catchment Analysis** tag.
3. Click **Try it out**.
4. Choose a `.kml` or `.kmz` contour map file (e.g. `data/sample/contours_1m.kml`).
5. Click **Execute** to view the structured normalization response.

### Using `curl`
```powershell
curl.exe -X POST "http://127.0.0.1:8000/api/v1/findCatchment" `
  -F "file=@contours_1m.kml"
```

### Example Response (`200 OK`)
```json
{
  "filename": "contours_1m.kml",
  "file_type": "kml",
  "file_size_bytes": 6710528,
  "is_valid": true,
  "can_parse": true,
  "contours_processed": 1355,
  "min_elevation": 267.0,
  "max_elevation": 298.0,
  "extent": {
    "min_latitude": 21.2398224433387,
    "max_latitude": 21.2635806472203,
    "min_longitude": 81.2814044952393,
    "max_longitude": 81.3126468658447
  },
  "kml_entry_name": null,
  "message": "Contour file successfully validated and normalized for terrain analysis."
}
```

## Running Tests

Execute the automated test suite:

```powershell
pytest tests/ -v
```

## Contour Ingestion & Normalization Details

- **Safe Temporary Storage**: Uploads are processed in isolated temporary workspaces (`tempfile.TemporaryDirectory`) and never permanently saved to disk.
- **Dynamic KMZ Extraction**: Archives are inspected to locate the internal `.kml` without assuming fixed filenames.
- **Robust Elevation Resolution**: Automatically extracts elevation from `<ExtendedData>` (`SimpleData`, `Data/value`), `<name>`, `<description>`, or 3D coordinate vertices.
- **Non-Contour Filtering**: Linear contour geometries (`LineString`) are parsed while label markers (Points) and boundary geometries (Polygons) are filtered cleanly.
- **Validation Safeguards**:
  - Missing elevation values (`400 Bad Request`)
  - Unsupported/non-contour geometries (`400 Bad Request`)
  - Degenerate line strings with `< 2` vertices (`400 Bad Request`)
  - Insufficient contour lines or single flat elevation (`400 Bad Request`)
  - Malformed XML or corrupted KMZ archives (`400 Bad Request`)
