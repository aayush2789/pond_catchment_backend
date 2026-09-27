from typing import List

from pydantic import BaseModel, Field


class PondStorageResult(BaseModel):
    """Indicative pond storage sizing (Phase 7).

    Clearly distinct from runoff: `design_inflow_m3` is the expected collectible
    water (inflow), while `storage_capacity_m3` is the conceptual basin volume
    sized to hold that inflow. This is NOT an engineering design.
    """

    design_inflow_m3: float
    storage_capacity_m3: float
    depth_m: float
    bottom_width_m: float
    bottom_length_m: float
    top_width_m: float
    top_length_m: float
    surface_area_m2: float
    side_slope_hv: float
    length_to_width_ratio: float
    assumptions: List[str] = Field(default_factory=list)
    note: str = (
        "Indicative conceptual sizing derived from a truncated-pyramid basin geometry; "
        "not a substitute for a certified engineering design."
    )
