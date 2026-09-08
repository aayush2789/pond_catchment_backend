from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field


class Coordinates(BaseModel):
    latitude: float
    longitude: float
    elevation: Optional[float] = None


class GeographicExtent(BaseModel):
    min_latitude: float
    max_latitude: float
    min_longitude: float
    max_longitude: float


class ProjectedBounds(BaseModel):
    min_x: float
    max_x: float
    min_y: float
    max_y: float


class ContourLine(BaseModel):
    id: str
    elevation: float
    coordinates: List[Tuple[float, float]]
    vertex_count: int
    name: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class NormalizedContourDataset(BaseModel):
    filename: str
    contour_count: int
    min_elevation: float
    max_elevation: float
    elevation_unit: str = "meters"
    extent: GeographicExtent
    contours: List[ContourLine] = Field(default_factory=list)


class TerrainMetadata(BaseModel):
    crs: str
    grid_resolution_meters: float
    rows: int
    cols: int
    min_elevation: float
    max_elevation: float
    projected_bounds: ProjectedBounds
    geographic_extent: GeographicExtent


class CandidatePond(BaseModel):
    id: str
    location: Coordinates
    suitability_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    estimated_depth_meters: Optional[float] = None


class CatchmentBoundary(BaseModel):
    type: str = "Feature"
    geometry: Dict[str, Any]
    properties: Optional[Dict[str, Any]] = None


class CatchmentAnalysisResponse(BaseModel):
    filename: str
    selected_pond: CandidatePond
    catchment_area_sq_meters: float
    catchment_area_hectares: float
    boundary: Optional[CatchmentBoundary] = None
    candidate_ponds: List[CandidatePond] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ContourInspectionResponse(BaseModel):
    filename: str
    file_type: str
    file_size_bytes: int
    is_valid: bool
    can_parse: bool
    contours_processed: int
    min_elevation: float
    max_elevation: float
    extent: GeographicExtent
    kml_entry_name: Optional[str] = None
    terrain: Optional[TerrainMetadata] = None
    message: str
