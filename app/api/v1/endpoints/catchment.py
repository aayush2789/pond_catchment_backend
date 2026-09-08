from fastapi import APIRouter, File, HTTPException, UploadFile, status
from app.schemas.catchment import CatchmentAnalysisResponse
from app.utils.file_handler import validate_contour_extension

router = APIRouter()


@router.post("/analyzeContour", response_model=CatchmentAnalysisResponse)
@router.post("/findCatchment", response_model=CatchmentAnalysisResponse)
async def analyze_contour(file: UploadFile = File(...)) -> CatchmentAnalysisResponse:
    validate_contour_extension(file.filename or "")
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Catchment analysis algorithm will be integrated in Phase 2.",
    )
