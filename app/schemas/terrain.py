from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.schemas.catchment import (
    CatchmentResult,
    GeographicExtent,
    PondCandidateSite,
    ProjectedBounds,
    TerrainMetadata,
)
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
    contour_interval_m: Optional[float] = Field(
        default=None,
        ge=1.0,
        le=100.0,
        description="Contour interval in meters for generated visualization contours. Defaults to 5 m.",
    )
    include_contours: bool = Field(
        default=True,
        description="Include DEM-derived contour lines (GeoJSON LineStrings) for map visualization.",
    )
    include_analysis: bool = Field(
        default=False,
        description=(
            "Also run candidate pond siting (constrained to the selected land) and catchment "
            "delineation on the acquired DEM."
        ),
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


class TerrainAnalysisInfo(BaseModel):
    """Terrain-level analysis derived from the acquired DEM (Phase 2B)."""

    terrain: TerrainMetadata
    contours: Optional[Dict[str, Any]] = None


class PondSitingResult(BaseModel):
    """Candidate pond sites and catchment computed on the DEM path (Phases 3-4).

    Candidates are constrained to the selected land polygon; the catchment feeding
    the primary candidate may extend beyond the selected land.
    """

    candidate_sites: List[PondCandidateSite] = Field(default_factory=list)
    selected_pond: Optional[PondCandidateSite] = None
    scoring_config: Dict[str, float] = Field(default_factory=dict)
    land_masked_cell_count: int = 0
    catchment: Optional[CatchmentResult] = None


class TerrainPreviewResponse(BaseModel):
    status: str = "success"
    analysis_extent: GeographicExtent
    buffer_meters: float
    target_resolution_meters: float
    dem: DEMPreviewInfo
    terrain: Optional[TerrainAnalysisInfo] = None
    pond_siting: Optional[PondSitingResult] = None
    message: str
