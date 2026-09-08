from fastapi import APIRouter, File, UploadFile
from app.schemas.catchment import ContourInspectionResponse
from app.services.parser import ContourParserService

router = APIRouter()


@router.post(
    "/findCatchment",
    response_model=ContourInspectionResponse,
    summary="Analyze contour map, identify pond sites, and delineate catchment",
    description="Accepts a KML or KMZ contour map file via multipart form-data, reconstructs the DEM, computes slope, evaluates candidate pond sites, and delineates the upstream contributing catchment area and boundary polygon.",
)
@router.post(
    "/analyzeContour",
    response_model=ContourInspectionResponse,
    summary="Analyze contour map, identify pond sites, and delineate catchment (alias)",
    description="Alias endpoint for /findCatchment.",
)
async def find_catchment(
    file: UploadFile = File(..., description="Contour map file (.kml or .kmz)"),
) -> ContourInspectionResponse:
    content = await file.read()
    return ContourParserService.inspect_contour_file(content, file.filename or "")
