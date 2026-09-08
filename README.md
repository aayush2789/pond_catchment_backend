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
│   │       │   └── catchment.py     # Contour ingestion & terrain routes
│   │       └── api.py               # V1 API router aggregator
│   ├── core/
│   │   └── config.py                # Environment-driven settings (pydantic-settings)
│   ├── models/                      # Domain entities & future database models
│   ├── schemas/
│   │   ├── health.py                # Health check Pydantic schemas
│   │   └── catchment.py             # Geospatial, contour, and terrain schemas
│   ├── services/
│   │   ├── parser.py                # KML/KMZ normalization & validation service
│   │   ├── terrain.py               # Terrain reconstruction (DEM & UTM projection) service
│   │   └── hydrology.py             # Interface for flow & catchment delineation
│   ├── utils/
│   │   └── file_handler.py          # File format validation and file operations
│   └── main.py                      # FastAPI application entry point & middleware
├── data/
│   └── sample/                      # Sample contour datasets (KML/KMZ)
│       └── contours_1m.kml
├── tests/
│   ├── conftest.py                  # Pytest fixtures and TestClient
│   ├── test_catchment.py            # Contour, terrain reconstruction, and error tests
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
| `POST` | `/api/v1/findCatchment` | Upload contour map, normalize geometry, and reconstruct terrain surface (multipart form-data) |
| `POST` | `/api/v1/analyzeContour` | Alias endpoint for `/findCatchment` |

## Testing the Upload & Terrain Reconstruction Endpoint

### From Swagger UI (`/docs`)
1. Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).
2. Expand `POST /api/v1/findCatchment` under the **Catchment Analysis** tag.
3. Click **Try it out**.
4. Choose a `.kml` or `.kmz` contour map file (e.g. `data/sample/contours_1m.kml`).
5. Click **Execute** to view the structured terrain reconstruction response.

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
  "terrain": {
    "crs": "EPSG:32644",
    "grid_resolution_meters": 10.0,
    "rows": 264,
    "cols": 326,
    "min_elevation": 267.0,
    "max_elevation": 298.0,
    "projected_bounds": {
      "min_x": 529194.47,
      "max_x": 532436.11,
      "min_y": 2348720.29,
      "max_y": 2351345.67
    },
    "geographic_extent": {
      "min_latitude": 21.2398224433387,
      "max_latitude": 21.2635806472203,
      "min_longitude": 81.2814044952393,
      "max_longitude": 81.3126468658447
    }
  },
  "message": "Contour file successfully validated, normalized, and reconstructed into terrain surface."
}
```

## Running Tests

Execute the automated test suite:

```powershell
pytest tests/ -v
```

## Terrain Reconstruction & Modeling Details

- **Dynamic Projected CRS**: Automatically calculates the appropriate UTM zone and projected coordinate reference system (e.g. `EPSG:32644`) from dataset coordinates, preserving geographic bounds while performing spatial operations in metres.
- **Continuous Elevation Surface (DEM)**: Converts contour vertices into spatial elevation samples and interpolates a regular metric grid using linear Delaunay triangulation (`scipy.interpolate.griddata`).
- **Boundary Gap Filling**: Replaces extrapolation NaNs along domain boundaries with nearest-neighbor samples, ensuring a gap-free elevation surface ready for hydrological modeling.
- **In-Memory Pipeline**: Keeps the elevation model as a structured `TerrainModel` object directly accessible by subsequent slope, flow-direction, and catchment services.
