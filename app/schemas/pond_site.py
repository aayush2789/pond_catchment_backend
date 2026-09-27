from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.schemas.catchment import CatchmentResult, PondCandidateSite, TerrainMetadata
from app.schemas.land import LandGeometry, SelectedLand
from app.schemas.pond import PondStorageResult
from app.schemas.rainfall import RainfallResult
from app.schemas.terrain import DEMSourceInfo
from app.schemas.water import WaterVolumeResult


class AnalysisParameters(BaseModel):
    """Optional tuning of the unified analysis. All values fall back to
    documented server defaults when omitted."""

    buffer_meters: Optional[float] = Field(
        default=None, ge=0.0, le=5000.0, description="Analysis-extent buffer around the land bbox."
    )
    dem_resolution_m: Optional[float] = Field(
        default=None, ge=10.0, le=100.0, description="Target DEM grid resolution in meters."
    )
    contour_interval_m: Optional[float] = Field(
        default=None, ge=1.0, le=100.0, description="Contour interval for generated contours."
    )
    runoff_coefficient: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Explicit runoff coefficient; defaults to the documented 0.30 planning value.",
    )
    collection_efficiency: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Explicit collection efficiency; defaults to the documented 0.75 planning value.",
    )
    snap_radius_meters: Optional[float] = Field(
        default=None, ge=10.0, le=1000.0, description="Pour-point snapping radius."
    )


class PondSiteAnalysisRequest(BaseModel):
    """Unified analysis request.

    Normal workflow: `geometry` only — terrain is acquired automatically from the
    DEM service. Expert/testing workflow: multipart upload of a KML/KMZ contour
    file instead (`file` form field); `geometry` must then be omitted.
    """

    geometry: Optional[LandGeometry] = None
    analysis_parameters: Optional[AnalysisParameters] = None


class PondSiteAnalysisResponse(BaseModel):
    status: str = "success"
    selected_land: Optional[SelectedLand] = None
    terrain: Optional[TerrainMetadata] = None
    contours: Optional[Dict[str, Any]] = None
    pond: Optional[PondCandidateSite] = None
    catchment: Optional[CatchmentResult] = None
    rainfall: Optional[RainfallResult] = None
    water: Optional[WaterVolumeResult] = None
    pond_storage: Optional[PondStorageResult] = None
    dem_source: Optional[DEMSourceInfo] = None
    message: str
