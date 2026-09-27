from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import ValidationError

from app.schemas.pond_site import PondSiteAnalysisRequest, PondSiteAnalysisResponse
from app.services.pond_planning import PondPlanningService

router = APIRouter()


@router.post(
    "/analyzePondSite",
    response_model=PondSiteAnalysisResponse,
    summary="Unified pond-site analysis (land -> DEM -> terrain -> pond -> catchment -> rainfall -> water)",
    description=(
        "Complete end-to-end workflow. Normal usage: send `request` (form field) containing the "
        "selected land GeoJSON and optional analysis parameters; terrain is acquired automatically "
        "from the public DEM service. Expert/testing usage: upload a KML/KMZ contour `file` instead "
        "of the geometry. Returns the selected land metrics, terrain/contour information, the "
        "primary pond candidate (constrained to the selected land), the upstream catchment (which "
        "may extend beyond the selected land), historical rainfall, theoretical runoff, expected "
        "collectible water, and indicative pond storage."
    ),
)
async def analyze_pond_site(
    request: Optional[str] = Form(
        None,
        description="JSON-encoded PondSiteAnalysisRequest: {\"geometry\": {...}, \"analysis_parameters\": {...}}",
    ),
    file: Optional[UploadFile] = File(
        None, description="Optional KML/KMZ contour map (expert/testing terrain source)."
    ),
) -> PondSiteAnalysisResponse:
    if request is not None:
        try:
            payload = PondSiteAnalysisRequest.model_validate_json(request)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid analysis request JSON: {exc}",
            )
    else:
        payload = PondSiteAnalysisRequest()

    kml_bytes = None
    kml_filename = None
    if file is not None:
        kml_bytes = await file.read()
        kml_filename = file.filename or "upload.kml"

    return PondPlanningService.analyze_pond_site(
        payload, kml_bytes=kml_bytes, kml_filename=kml_filename
    )
