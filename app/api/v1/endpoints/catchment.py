from fastapi import APIRouter, File, UploadFile
from app.schemas.catchment import ContourInspectionResponse
from app.services.parser import ContourParserService

router = APIRouter()


@router.post(
    "/findCatchment",
    response_model=ContourInspectionResponse,
    summary="Validate and inspect uploaded contour map",
    description="Accepts a KML or KMZ contour map file via multipart form-data, safely inspects the archive or document structure in temporary storage, and returns validation metadata.",
)
@router.post(
    "/analyzeContour",
    response_model=ContourInspectionResponse,
    summary="Validate and inspect uploaded contour map (alias)",
    description="Alias endpoint for /findCatchment.",
)
async def find_catchment(
    file: UploadFile = File(..., description="Contour map file (.kml or .kmz)"),
) -> ContourInspectionResponse:
    content = await file.read()
    return ContourParserService.inspect_contour_file(content, file.filename or "")
