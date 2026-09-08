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
│   │   ├── parser.py                # KML/KMZ parsing & validation service
│   │   ├── terrain.py               # Interface for DEM & slope analysis
│   │   └── hydrology.py             # Interface for flow & catchment delineation
│   ├── utils/
│   │   └── file_handler.py          # File format validation and file operations
│   └── main.py                      # FastAPI application entry point & middleware
├── data/
│   └── sample/                      # Directory for sample contour datasets (KML/KMZ)
├── tests/
│   ├── conftest.py                  # Pytest fixtures and TestClient
│   ├── test_catchment.py            # Contour upload, inspection, and error tests
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
| `POST` | `/api/v1/findCatchment` | Upload and inspect KML/KMZ contour map (multipart form-data) |
| `POST` | `/api/v1/analyzeContour` | Alias endpoint for `/findCatchment` |

## Testing the Upload Endpoint

### From Swagger UI (`/docs`)
1. Navigate to [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) in your browser.
2. Expand `POST /api/v1/findCatchment` under the **Catchment Analysis** tag.
3. Click **Try it out**.
4. Click **Choose File** and select any `.kml` or `.kmz` file.
5. Click **Execute** to view the structured validation response.

### Using `curl`
```powershell
curl -X POST "http://127.0.0.1:8000/api/v1/findCatchment" `
  -H "accept: application/json" `
  -F "file=@path/to/contours.kml"
```

### Example Response (`200 OK`)
```json
{
  "filename": "contours.kml",
  "file_type": "kml",
  "file_size_bytes": 452,
  "is_valid": true,
  "can_parse": true,
  "kml_entry_name": null,
  "features_count": 2,
  "message": "Contour file successfully validated and ready for terrain analysis."
}
```

For `.kmz` archives, `kml_entry_name` displays the extracted internal KML document name found dynamically within the archive.

## Running Tests

Execute the automated test suite:

```powershell
pytest tests/ -v
```

## Security & Temporary Storage Handling

- Uploaded files are processed using isolated temporary storage via Python's `tempfile.TemporaryDirectory`.
- No uploaded files are permanently retained on disk.
- KMZ archives are scanned and extracted dynamically without hardcoding internal paths.
- Malformed XML syntax, missing `.kml` contents inside KMZ archives, non-KML XML documents, empty uploads, and unsupported extensions are rejected gracefully with standard HTTP 400 status codes.
