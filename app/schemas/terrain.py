from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.catchment import GeographicExtent, ProjectedBounds
from app.schemas.land import LandGeometry


class TerrainPreviewRequest(BaseModel):
    """Selected land area plus optional terrain-acquisition parameters."""

    geometry: LandGeometry
    buffer_meters: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=5000.0,
        description="Buffer added around the land bbox to define the analysis extent. Defaults to ANALYSIS_BUFFER_METERS.",
    )
    resolution_meters: Optional[float] = Field(
        default=None,
        ge=10.0,
        le=100.0,
        description="Target DEM grid resolution in meters. Defaults to DEM_TARGET_RESOLUTION_M.",
    )


class DEMSourceInfo(BaseModel):
    provider: str
    dataset: str
    attribution: str
    zoom_level: Optional[int] = None


class DEMPreviewInfo(BaseModel):
    crs: str
    resolution_meters: float
    rows: int
    cols: int
    min_elevation_m: float
    max_elevation_m: float
    mean_elevation_m: float
    nodata_cells_filled: int
    projected_bounds: ProjectedBounds
    geographic_extent: GeographicExtent
    source: DEMSourceInfo
    cache_hit: bool


class TerrainPreviewResponse(BaseModel):
    status: str = "success"
    analysis_extent: GeographicExtent
    buffer_meters: float
    target_resolution_meters: float
    dem: DEMPreviewInfo
    message: str
