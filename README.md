# AI-Based Village Pond Planning System - Backend

A modular, extensible FastAPI backend service for the AI-based Village Pond Planning System. This system accepts contour maps (in KML/KMZ formats) to model terrain, compute terrain slope, detect promising candidate pond regions, and prepare for catchment delineation.

## Architecture & Modular Structure

```text
pond_catchment_backend/
├── app/
│   ├── api/
│   │   └── v1/
│   │       ├── endpoints/
│   │       │   ├── health.py             # System health and status endpoint
│   │       │   └── catchment.py          # Contour ingestion, terrain, & candidate routes
│   │       └── api.py                    # V1 API router aggregator
│   ├── core/
│   │   └── config.py                     # Environment-driven settings (pydantic-settings)
│   ├── models/                           # Domain entities & future database models
│   ├── schemas/
│   │   ├── health.py                     # Health check Pydantic schemas
│   │   └── catchment.py                  # Geospatial, contour, terrain, and candidate schemas
│   ├── services/
│   │   ├── parser.py                     # KML/KMZ normalization & validation service
│   │   ├── terrain.py                    # Terrain reconstruction (DEM, UTM, & slope) service
│   │   ├── candidate_selection.py        # Explainable multi-factor candidate scoring service
│   │   └── hydrology.py                  # Interface for future flow & catchment delineation
│   ├── utils/
│   │   └── file_handler.py               # File format validation and file operations
│   └── main.py                           # FastAPI application entry point & middleware
├── data/
│   └── sample/                           # Sample contour datasets (KML/KMZ)
│       └── contours_1m.kml
├── tests/
│   ├── conftest.py                       # Pytest fixtures and TestClient
│   ├── test_catchment.py                 # Full unit & integration test suite
│   └── test_health.py                    # API & health check test suite
├── .env.example                          # Environment configuration template
├── .gitignore                            # Git exclusions for Python, venv, caches, logs
├── README.md                             # Documentation & setup guide
└── requirements.txt                      # Project dependencies
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
| `POST` | `/api/v1/findCatchment` | Upload contour map, reconstruct terrain, calculate slope, and identify candidate pond sites |
| `POST` | `/api/v1/analyzeContour` | Alias endpoint for `/findCatchment` |

## Testing the Upload & Candidate Siting Endpoint

### From Swagger UI (`/docs`)
1. Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).
2. Expand `POST /api/v1/findCatchment` under the **Catchment Analysis** tag.
3. Click **Try it out**.
4. Choose a `.kml` or `.kmz` contour map file (e.g. `data/sample/contours_1m.kml`).
5. Click **Execute** to view the candidate sites and terrain metrics.

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
    "min_latitude": 21.2398224,
    "max_latitude": 21.2635806,
    "min_longitude": 81.2814045,
    "max_longitude": 81.3126469
  },
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
      "min_latitude": 21.2398224,
      "max_latitude": 21.2635806,
      "min_longitude": 81.2814045,
      "max_longitude": 81.3126469
    },
    "slope": {
      "min_slope_degrees": 0.0,
      "max_slope_degrees": 32.14,
      "mean_slope_degrees": 3.3
    }
  },
  "candidate_sites": [
    {
      "id": "pond_site_1",
      "rank": 1,
      "latitude": 21.2497874,
      "longitude": 81.2899562,
      "elevation": 267.0,
      "slope_degrees": 2.8,
      "suitability_score": 1.0,
      "factor_scores": {
        "slope_score": 1.0,
        "elevation_score": 1.0
      }
    },
    {
      "id": "pond_site_2",
      "rank": 2,
      "latitude": 21.2631716,
      "longitude": 81.282946,
      "elevation": 267.0,
      "slope_degrees": 2.38,
      "suitability_score": 1.0,
      "factor_scores": {
        "slope_score": 1.0,
        "elevation_score": 1.0
      }
    },
    {
      "id": "pond_site_3",
      "rank": 3,
      "latitude": 21.2460838,
      "longitude": 81.289467,
      "elevation": 268.0,
      "slope_degrees": 2.19,
      "suitability_score": 0.9839,
      "factor_scores": {
        "slope_score": 1.0,
        "elevation_score": 0.9677
      }
    }
  ],
  "message": "Contour file successfully validated, normalized, reconstructed, and evaluated for candidate pond sites."
}
```

## Running Tests

Execute the automated test suite:

```powershell
pytest tests/ -v
```

## Preliminary Pond Candidate Siting Details

- **Slope Modeling (`TerrainService.calculate_slope`)**: Computes finite difference gradients from the metric DEM, producing surface slopes in degrees $[0^\circ, 90^\circ]$.
- **Explainable Multi-Criteria Scoring (`CandidateSelectionService`)**:
  - **Slope Suitability ($S_{\text{slope}}$)**: Favors flat to gently sloping terrain ($\le 3^\circ$ ideal, penalizing steep slopes $> 12^\circ$).
  - **Elevation Suitability ($S_{\text{elevation}}$)**: Favors low-lying valleys and natural collection areas over ridges.
  - Retains individual factor scores for transparent auditing.
- **Spatial Non-Maximum Suppression (NMS)**: Suppresses neighboring cells within a configurable distance (default: $150\text{ m}$), ensuring candidates represent distinct spatial pond regions rather than adjacent pixels.
- **Extensibility**: The candidate scoring interface accepts configurable weights and is architected to seamlessly ingest future hydrological metrics (flow accumulation, catchment area, soil infiltration) without breaking existing modules.
