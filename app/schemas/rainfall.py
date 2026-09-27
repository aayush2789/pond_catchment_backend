from typing import List, Optional

from pydantic import BaseModel, Field


class RainfallResult(BaseModel):
    """Historical rainfall statistics for a location (Phase 5)."""

    rainfall_mm: float = Field(description="Mean annual rainfall depth over the period.")
    units: str = "mm/year"
    period: str = Field(description="Human-readable data period, e.g. '2015-2024'.")
    start_year: int
    end_year: int
    monthly_mm: Optional[List[Optional[float]]] = Field(
        default=None,
        description="Mean monthly rainfall totals (mm) for JAN..DEC, where available.",
    )
    source: str = Field(description="Provider identifier, e.g. 'open-meteo'.")
    dataset: str = Field(description="Underlying dataset description.")
    attribution: str
    cache_hit: bool = False
    fetched_at: str = Field(description="ISO-8601 UTC timestamp of the provider fetch.")
