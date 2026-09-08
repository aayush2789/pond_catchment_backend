from fastapi import APIRouter, File, UploadFile
from app.schemas.catchment import ContourInspectionResponse
from app.services.parser import ContourParserService

router = APIRouter()


@router.post(
    "/findCatchment",
    response_model=ContourInspectionResponse,
    summary="Validate contour map and reconstruct terrain surface",
    description="Accepts a KML or KMZ contour map file via multipart form-data, validates and normalizes contour geometries, reconstructs a continuous projected elevation surface (DEM), and returns terrain metadata.",
)
@router.post(
    "/analyzeContour",
    response_model=ContourInspectionResponse,
    summary="Validate contour map and reconstruct terrain surface (alias)",
    description="Alias endpoint for /findCatchment.",
)
async def find_catchment(
    file: UploadFile = File(..., description="Contour map file (.kml or .kmz)"),
) -> ContourInspectionResponse:
    content = await file.read()
    return ContourParserService.inspect_contour_file(content, file.filename or "")
