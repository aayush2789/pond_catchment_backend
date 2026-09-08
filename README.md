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
| `POST` | `/api/v1/findCatchment` | Upload contour map, reconstruct terrain, calculate slope, identify candidate pond sites, and delineate upstream catchment |
| `POST` | `/api/v1/analyzeContour` | Alias endpoint for `/findCatchment` |

## Testing the Catchment Delineation Endpoint

### From Swagger UI (`/docs`)
1. Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).
2. Expand `POST /api/v1/findCatchment` under the **Catchment Analysis** tag.
3. Click **Try it out**.
4. Choose a `.kml` or `.kmz` contour map file (e.g. `data/sample/contours_1m.kml`).
5. Click **Execute** to view candidate sites and delineated upstream catchment boundary.

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
    }
  ],
  "selected_pond": {
    "id": "pond_site_1",
    "rank": 1,
    "latitude": 21.2497874,
    "longitude": 81.2899562,
    "elevation": 267.0,
    "slope_degrees": 2.8,
    "suitability_score": 1.0
  },
  "catchment": {
    "outlet_location": {
      "latitude": 21.2497874,
      "longitude": 81.2899562,
      "elevation": 267.0
    },
    "snapped_outlet": {
      "latitude": 21.2496987,
      "longitude": 81.2889922,
      "elevation": 273.86
    },
    "elevation_meters": 273.86,
    "slope_degrees": 4.66,
    "catchment_area_sq_meters": 14500.0,
    "catchment_area_hectares": 1.45,
    "contributing_cells_count": 145,
    "hydrology": {
      "max_flow_accumulation_cells": 173.0,
      "outlet_flow_accumulation_cells": 145.0,
      "conditioned_sinks_filled": true
    },
    "boundary": {
      "type": "Feature",
      "geometry": {
        "type": "Polygon",
        "coordinates": [
          [
            [81.288028, 21.249700],
            [81.288028, 21.249791],
            [81.288029, 21.250062],
            [81.289089, 21.249969],
            [81.288028, 21.249700]
          ]
        ]
      },
      "properties": {
        "contributing_cells": 145,
        "crs": "EPSG:32644"
      }
    }
  },
  "message": "Contour file successfully validated, normalized, reconstructed, and analyzed for pond catchment."
}
```

## Running Tests

Execute the automated test suite:

```powershell
pytest tests/ -v
```

## Hydrological Analysis & Catchment Delineation Details

- **DEM Conditioning (`HydrologyService.condition_dem`)**: Priority-Flood algorithm fills artificial sinks and depressions, guaranteeing continuous downhill drainage across the terrain raster.
- **D8 Flow Direction (`HydrologyService.calculate_flow_direction`)**: Evaluates the steepest descent drop $(z_i - z_n) / d$ among all 8 adjacent neighbors (accounting for diagonal distance $\sqrt{2}$).
- **Flow Accumulation (`HydrologyService.calculate_flow_accumulation`)**: Computes upstream contributing area matrix via elevation-sorted topological routing.
- **Pour-Point Snapping (`HydrologyService.snap_to_drainage_cell`)**: Snaps candidate pond locations to the nearest high-accumulation drainage channel within a configurable search radius (default: $100\text{ m}$) using distance-weighted tie-breaking.
- **Catchment Delineation (`HydrologyService.delineate_catchment`)**: Reconstructs upstream contributing cells using reverse flow BFS graph traversal.
- **Polygonization & Transformation (`HydrologyService.polygonize_catchment`)**: Converts contributing grid cells to a unified geometric polygon via Shapely, simplifies boundary artifacts, and transforms UTM coordinates back to standard WGS84 `(longitude, latitude)` GeoJSON.
- **Metric Calculations**: Catchment area is accurately computed directly in projected metric units ($m^2$ and hectares: $10,000\text{ m}^2 = 1\text{ ha}$).
