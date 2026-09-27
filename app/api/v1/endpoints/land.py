from fastapi import APIRouter

from app.schemas.land import LandSelectionRequest, LandSelectionResponse
from app.services.land import LandSelectionService

router = APIRouter()


@router.post(
    "/analyzeLand",
    response_model=LandSelectionResponse,
    summary="Validate and measure a user-selected land area (GeoJSON)",
    description=(
        "Accepts a user-selected land area as GeoJSON (Polygon or MultiPolygon), validates the "
        "geometry (closed rings, non-zero area, reasonable coordinate ranges and extent), and "
        "returns the selected area in m² and hectares, the geographic bounding box, and the "
        "centroid. The selected polygon becomes the spatial constraint for all subsequent "
        "terrain, hydrology, and pond-siting analysis."
    ),
)
async def analyze_land(request: LandSelectionRequest) -> LandSelectionResponse:
    selected_land = LandSelectionService.validate_and_measure(request.geometry)
    return LandSelectionResponse(
        status="success",
        selected_land=selected_land,
        message=(
            "Selected land area successfully validated and measured. "
            "This polygon constrains all subsequent terrain and hydrology analysis."
        ),
    )
