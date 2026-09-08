from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class Coordinates(BaseModel):
    latitude: float
    longitude: float
    elevation: Optional[float] = None


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
