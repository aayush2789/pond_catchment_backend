from typing import List

from pydantic import BaseModel, Field


class WaterVolumeResult(BaseModel):
    """Transparent runoff / collectible-water estimation (Phase 6)."""

    catchment_area_m2: float
    rainfall_mm: float
    rainfall_period: str
    runoff_coefficient: float = Field(ge=0.0, le=1.0)
    runoff_coefficient_basis: str
    theoretical_runoff_m3: float
    collection_efficiency: float = Field(ge=0.0, le=1.0)
    efficiency_basis: str
    expected_collectible_water_m3: float
    assumptions: List[str] = Field(default_factory=list)
